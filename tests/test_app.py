import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import MagicMock, patch

import medleys.web.app as app
from medleys.database import empty_db, load_db, save_db


class TranslationsTest(unittest.TestCase):
    def test_loads_translations_for_every_supported_language(self) -> None:
        translations = app.load_translations()

        self.assertEqual(set(translations), set(app.SUPPORTED_LANGUAGES))
        for labels in translations.values():
            self.assertTrue(labels)
            self.assertEqual(set(labels), set(translations[app.DEFAULT_LANG]))


class AppHelpersTest(unittest.TestCase):
    @patch("medleys.web.app.app.run")
    def test_main_listens_on_the_local_network(self, run: MagicMock) -> None:
        app.main()

        run.assert_called_once_with(host="0.0.0.0", debug=True, port=5001, use_reloader=False)

    def test_url_encoding_round_trips_without_padding(self) -> None:
        url = "https://tabs.ultimate-guitar.com/tab/artist/song?q=café"
        encoded = app.encode_url(url)
        self.assertNotIn("=", encoded)
        self.assertEqual(app.decode_url(encoded), url)

    def test_normalize_tab_url_handles_protocol_relative_and_relative_urls(self) -> None:
        self.assertEqual(
            app.normalize_tab_url("//tabs.example/tab/song"), "https://tabs.example/tab/song"
        )
        self.assertEqual(
            app.normalize_tab_url("/tab/artist/song"),
            "https://www.ultimate-guitar.com/tab/artist/song",
        )

    def test_source_labels_and_export_filenames_are_safe(self) -> None:
        self.assertEqual(app.source_label_filter("list:123:Party Songs"), "Party Songs")
        self.assertEqual(
            app.source_label_filter("https://example.test/source"), "https://example.test/source"
        )
        self.assertEqual(
            app.export_filename("list:123:Party Songs!", "F#"), "medley-party-songs-F#.html"
        )

    def test_localized_transpose_labels_cover_directions_and_pluralization(self) -> None:
        labels = app.TRANSLATIONS[app.DEFAULT_LANG]
        self.assertEqual(app.localized_transpose_label(0, app.DEFAULT_LANG), labels["no_transpose"])
        self.assertIn(labels["semitone_one"], app.localized_transpose_label(1, app.DEFAULT_LANG))
        self.assertIn(labels["transpose_down"], app.localized_transpose_label(-2, app.DEFAULT_LANG))

    def test_format_chorus_lines_transposes_and_aligns_chords(self) -> None:
        lines = [
            {
                "lyrics": "hello",
                "chords": [{"symbol": "C", "position": 0}, {"symbol": "G", "position": 4}],
            }
        ]
        self.assertEqual(
            app.format_chorus_lines(lines, 2), [{"chords": "D   A", "lyrics": "hello"}]
        )
        self.assertEqual(app.format_chorus_lines([], 2), [])

    def test_build_chord_line_expands_beyond_lyrics(self) -> None:
        self.assertEqual(app.build_chord_line("hi", [{"position": 3}], ["Am"]), "   Am")

    def test_build_chord_line_rejects_mismatched_chords_and_symbols(self) -> None:
        with self.assertRaisesRegex(ValueError, "same length"):
            app.build_chord_line("", [{"position": 0}], [])

    def test_whatsapp_text_includes_original_and_fallback_medley_chords(self) -> None:
        song = {
            "artist": "Artist",
            "title": "Song",
            "original_tab_lines": [{"chords": "C", "lyrics": "Hello"}],
            "medley_chords": ["D", "A"],
        }
        text = app.build_whatsapp_text("list:1:Party", [song], "D", True)
        self.assertIn("Medley - Party", text)
        self.assertIn("Original:\nC\nHello", text)
        self.assertIn("Medley (D):\nD A", text)

    def test_extract_uploaded_links_deduplicates_and_applies_limit(self) -> None:
        html_text = """
        <a href="/tab/artist/one"> First Song </a>
        <a href="/tab/artist/one">Duplicate</a>
        <a href="//tabs.ultimate-guitar.com/tab/artist/two"></a>
        """
        self.assertEqual(
            app.extract_uploaded_links(html_text, 2),
            [
                {
                    "explore_rank": 1,
                    "explore_title": "First Song",
                    "url": "https://www.ultimate-guitar.com/tab/artist/one",
                },
                {
                    "explore_rank": 2,
                    "explore_title": "two",
                    "url": "https://tabs.ultimate-guitar.com/tab/artist/two",
                },
            ],
        )

    @patch("medleys.web.app.uuid.uuid4")
    @patch("medleys.web.app.now_iso", side_effect=["created", "created", "updated"])
    def test_create_update_snapshot_and_get_job(self, _now: MagicMock, uuid4: MagicMock) -> None:
        uuid4.return_value.hex = "1234567890abcdef"
        app.jobs.clear()
        self.addCleanup(app.jobs.clear)
        job_id = app.create_job("url", "source")
        app.update_job(job_id, status="complete", summary={"count": 1})

        self.assertEqual(job_id, "1234567890ab")
        job = app.get_job(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(cast(dict[str, object], job)["status"], "complete")
        self.assertEqual(app.snapshot_jobs()[0]["summary"], {"count": 1})
        self.assertIsNone(app.get_job("missing"))

    @patch("medleys.web.app.update_job")
    def test_run_job_records_success_and_failure(self, update_job: MagicMock) -> None:
        progress_updates = []

        def successful_target(value: int, progress: MagicMock) -> dict[str, int]:
            progress(processed=1, total=1)
            progress_updates.append(value)
            return {"value": value}

        app.run_job("one", successful_target, (3,))
        self.assertEqual(progress_updates, [3])
        self.assertIn(unittest.mock.call("one", processed=1, total=1), update_job.call_args_list)
        self.assertEqual(update_job.call_args_list[-1].kwargs["status"], "complete")

        update_job.reset_mock()
        app.run_job("two", MagicMock(side_effect=RuntimeError("broken")), ())
        self.assertEqual(
            update_job.call_args_list[-1].kwargs, {"status": "failed", "error": "broken"}
        )

    def test_job_page_renders_live_progress(self) -> None:
        app.jobs["progress-job"] = {
            "id": "progress-job",
            "kind": "url_list",
            "source_id": "list:1:Party",
            "status": "running",
            "created_at": "created",
            "updated_at": "updated",
            "summary": None,
            "error": None,
            "total": 10,
            "processed": 4,
            "skipped": 1,
            "scraped": 2,
            "failed": 1,
        }
        self.addCleanup(app.jobs.clear)

        response = app.app.test_client().get("/jobs/progress-job?lang=ca")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"4 / 10", response.data)
        self.assertIn(b'<progress value="4" max="10">', response.data)

    def test_integer_parsers_handle_missing_and_present_values(self) -> None:
        self.assertIsNone(app.parse_optional_int(None))
        self.assertEqual(app.parse_optional_int("4"), 4)
        self.assertEqual(app.parse_int("", 7), 7)
        self.assertEqual(app.parse_int("3", 7), 3)


class ParseTabUrlsTest(unittest.TestCase):
    def test_parses_and_deduplicates_ultimate_guitar_tab_urls(self) -> None:
        first = "https://tabs.ultimate-guitar.com/tab/oasis/wonderwall-chords-27596"
        second = "https://www.ultimate-guitar.com/tab/blur/song-chords-123"

        self.assertEqual(app.parse_tab_urls(f"{first}\n\n{second}\n{first}"), [first, second])

    def test_rejects_non_tab_urls(self) -> None:
        with self.assertRaisesRegex(ValueError, "Not an Ultimate Guitar tab URL"):
            app.parse_tab_urls("https://www.ultimate-guitar.com/explore")

    def test_rejects_other_hosts(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported Ultimate Guitar URL"):
            app.parse_tab_urls("https://example.com/tab/artist/song")

        with self.assertRaisesRegex(ValueError, "Unsupported Ultimate Guitar URL"):
            app.parse_tab_urls("https://evilultimate-guitar.com/tab/artist/song")


class UrlListRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        app.jobs.clear()
        self.addCleanup(app.jobs.clear)
        self.client = app.app.test_client()

    @patch("medleys.web.app.run_background")
    def test_creates_group_search_job(self, run_background: MagicMock) -> None:
        response = self.client.post(
            "/analyze/group?lang=es",
            data={"group": "Oasis", "limit": "200", "delay_ms": "0"},
        )

        self.assertEqual(response.status_code, 302)
        job = max(app.snapshot_jobs(), key=lambda item: item["created_at"])
        self.assertEqual(job["kind"], "group")
        self.assertTrue(job["source_id"].endswith(":Oasis"))
        self.assertEqual(run_background.call_args.args[3:6], (job["source_id"], 50, 0))

    def test_group_search_requires_a_group(self) -> None:
        response = self.client.post("/analyze/group", data={"group": ""})

        self.assertEqual(response.status_code, 400)

    @patch("medleys.web.app.analyze_songs")
    @patch("medleys.web.app.store_medley")
    @patch("medleys.web.app.discover_group_songs")
    def test_group_analysis_keeps_search_url_in_summary(
        self, discover: MagicMock, store_medley: MagicMock, analyze_songs: MagicMock
    ) -> None:
        discover.return_value = [{"url": "tab"}]
        analyze_songs.return_value = {"eligible_count": 1}

        summary = app.analyze_group("Blind Guardian", "source", 50, 0, False)

        self.assertEqual(
            summary["search_url"],
            "https://www.ultimate-guitar.com/search.php?title=Blind+Guardian&page=1&type=300",
        )
        store_medley.assert_called_once_with("source", "Blind Guardian", ["tab"])

    @patch("medleys.web.app.run_background")
    def test_creates_url_list_job(self, run_background: MagicMock) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            with patch("medleys.web.app.DB_PATH", db_path):
                response = self.client.post(
                    "/analyze/url-list?lang=es",
                    data={
                        "medley_name": "Fiesta",
                        "tab_urls": "https://tabs.ultimate-guitar.com/tab/oasis/wonderwall-chords-27596",
                        "delay_ms": "0",
                    },
                )
                db = load_db(db_path)

        self.assertEqual(response.status_code, 302)
        job = max(app.snapshot_jobs(), key=lambda item: item["created_at"])
        self.assertEqual(job["kind"], "url_list")
        self.assertTrue(job["source_id"].endswith(":Fiesta"))
        self.assertEqual(
            db["medleys"][job["source_id"]]["urls"],
            ["https://tabs.ultimate-guitar.com/tab/oasis/wonderwall-chords-27596"],
        )
        run_background.assert_called_once()

    def test_requires_at_least_one_url(self) -> None:
        response = self.client.post("/analyze/url-list", data={"tab_urls": ""})

        self.assertEqual(response.status_code, 400)

    def test_index_includes_database_song_picker(self) -> None:
        response = self.client.get("/?lang=ca")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Crea des de la BD", html)
        self.assertIn("data-song-picker", html)
        self.assertIn("/api/songs", html)

    @patch("medleys.web.app.run_background")
    def test_edits_saved_medley_and_reuses_source_id(self, run_background: MagicMock) -> None:
        source_id = "list:1234:Fiesta"
        first = "https://tabs.ultimate-guitar.com/tab/oasis/wonderwall-chords-27596"
        second = "https://www.ultimate-guitar.com/tab/blur/song-chords-123"
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            db = empty_db()
            save_db(db_path, db)
            with patch("medleys.web.app.DB_PATH", db_path):
                app.store_medley(source_id, "Fiesta", [first])
                response = self.client.post(
                    f"/medley/{source_id}/edit?lang=ca",
                    data={
                        "medley_name": "Festa",
                        "tab_urls": f"{first}\n{second}",
                        "delay_ms": "0",
                    },
                )
                saved = load_db(db_path)["medleys"][source_id]

        self.assertEqual(response.status_code, 302)
        self.assertEqual(saved["name"], "Festa")
        self.assertEqual(saved["urls"], [first, second])
        self.assertEqual(run_background.call_args.args[3], source_id)

    def test_edit_page_returns_saved_urls(self) -> None:
        source_id = "list:1234:Fiesta"
        first = "https://tabs.ultimate-guitar.com/tab/oasis/wonderwall-chords-27596"
        second = "https://www.ultimate-guitar.com/tab/blur/song-chords-123"
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            db = empty_db()
            db["songs"][first] = {"url": first, "artist": "Oasis", "title": "Wonderwall"}
            save_db(db_path, db)
            with patch("medleys.web.app.DB_PATH", db_path):
                app.store_medley(source_id, "Fiesta", [second, first])
                response = self.client.get(f"/medley/{source_id}/edit?lang=es")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Oasis - Wonderwall", html)
        self.assertIn(second, html)
        self.assertLess(html.index(second), html.index("Oasis - Wonderwall"))
        self.assertIn("data-add-song", html)
        self.assertIn("data-remove-song", html)
        self.assertIn("data-move-up", html)

    def test_edit_page_shows_favorites_count_and_dash_when_missing(self) -> None:
        source_id = "list:1234:Fiesta"
        with_favorites = "https://tabs.ultimate-guitar.com/tab/oasis/wonderwall-chords-27596"
        without_favorites = "https://www.ultimate-guitar.com/tab/blur/song-chords-123"
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            db = empty_db()
            db["songs"][with_favorites] = {
                "url": with_favorites,
                "artist": "Oasis",
                "title": "Wonderwall",
                "favorites_count": 2130,
            }
            db["songs"][without_favorites] = {
                "url": without_favorites,
                "artist": "Blur",
                "title": "Song 2",
            }
            save_db(db_path, db)
            with patch("medleys.web.app.DB_PATH", db_path):
                app.store_medley(source_id, "Fiesta", [with_favorites, without_favorites])
                response = self.client.get(f"/medley/{source_id}/edit?lang=ca")

        html = response.get_data(as_text=True)
        self.assertIn("2130", html)

    def test_edit_page_reconstructs_legacy_medley_urls(self) -> None:
        source_id = "list:1234:Legacy"
        url = "https://tabs.ultimate-guitar.com/tab/oasis/wonderwall-chords-27596"
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            db = empty_db()
            db["songs"] = {url: {"url": url, "sources": [source_id], "explore_rank": 1}}
            save_db(db_path, db)
            with patch("medleys.web.app.DB_PATH", db_path):
                response = self.client.get(f"/medley/{source_id}/edit")

        self.assertEqual(response.status_code, 200)
        self.assertIn(url.encode(), response.data)


class SongSearchApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app.app.test_client()

    def test_searches_stored_songs_by_artist_title_and_url(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            db = empty_db()
            db["songs"] = {
                "oasis-url": {
                    "url": "oasis-url",
                    "artist": "Oasis",
                    "title": "Wonderwall",
                },
                "blur-url": {
                    "url": "blur-url",
                    "artist": "Blur",
                    "title": "Song 2",
                },
            }
            save_db(db_path, db)
            with patch("medleys.web.app.DB_PATH", db_path):
                response = self.client.get("/api/songs?q=wonder")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["songs"],
            [
                {
                    "artist": "Oasis",
                    "explore_title": None,
                    "title": "Wonderwall",
                    "url": "oasis-url",
                    "favorites_count": None,
                }
            ],
        )

    def test_limits_empty_search_to_twenty_songs(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            db = empty_db()
            db["songs"] = {
                f"url-{index}": {"url": f"url-{index}", "title": f"Song {index}"}
                for index in range(25)
            }
            save_db(db_path, db)
            with patch("medleys.web.app.DB_PATH", db_path):
                response = self.client.get("/api/songs")

        self.assertEqual(len(response.get_json()["songs"]), 20)

    def test_includes_favorites_count_when_present(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            db = empty_db()
            db["songs"]["oasis-url"] = {
                "url": "oasis-url",
                "artist": "Oasis",
                "title": "Wonderwall",
                "favorites_count": 2130,
            }
            save_db(db_path, db)
            with patch("medleys.web.app.DB_PATH", db_path):
                response = self.client.get("/api/songs?q=wonder")

        self.assertEqual(response.get_json()["songs"][0]["favorites_count"], 2130)


class DeleteMedleyTest(unittest.TestCase):
    def test_deletes_only_source_association_and_keeps_songs(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            initial_db = empty_db()
            initial_db["songs"] = {
                "one": {"url": "one", "sources": ["list:1:Party", "other"]},
                "two": {"url": "two", "sources": ["list:1:Party"]},
            }
            save_db(db_path, initial_db)
            with patch("medleys.web.app.DB_PATH", db_path):
                response = app.app.test_client().post("/medley/list:1:Party/delete?lang=es")

            db = load_db(db_path)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(set(db["songs"]), {"one", "two"})
            self.assertEqual(db["songs"]["one"]["sources"], ["other"])
            self.assertEqual(db["songs"]["two"]["sources"], [])


class ComparisonExclusionsTest(unittest.TestCase):
    def test_reports_failed_missing_chorus_and_duplicate_songs(self) -> None:
        source = "list:1:Party"
        songs = {
            "kept": {
                "url": "kept",
                "artist": "Artist",
                "title": "Song",
                "chorus_chords": ["C"],
                "explore_rank": 1,
                "sources": [source],
            },
            "duplicate": {
                "url": "duplicate",
                "artist": "artist",
                "title": " song ",
                "chorus_chords": ["G"],
                "explore_rank": 2,
                "sources": [source],
            },
            "missing": {
                "url": "missing",
                "chorus_chords": [],
                "explore_rank": 3,
                "sources": [source],
                "errors": [],
            },
            "failed": {
                "url": "failed",
                "explore_rank": 4,
                "sources": [source],
                "errors": [{"error": "blocked"}],
            },
        }
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            db = empty_db()
            db["songs"] = songs
            save_db(db_path, db)
            with patch("medleys.web.app.DB_PATH", db_path):
                exclusions = app.comparison_exclusions(source)

        self.assertEqual(
            [(song["url"], song["exclusion_reason"]) for song in exclusions],
            [("duplicate", "duplicate"), ("missing", "no_chorus"), ("failed", "scrape_failed")],
        )
        self.assertEqual(exclusions[-1]["exclusion_detail"], "blocked")


class SongsSortTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app.app.test_client()

    def test_sorted_songs_orders_by_favorites_with_missing_treated_as_zero(self) -> None:
        songs = [
            {"url": "a", "artist": "A", "title": "A Song", "favorites_count": 5},
            {"url": "b", "artist": "B", "title": "B Song", "favorites_count": 50},
            {"url": "c", "artist": "C", "title": "C Song"},
        ]
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            db = empty_db()
            db["songs"] = {song["url"]: song for song in songs}
            save_db(db_path, db)
            with patch("medleys.web.app.DB_PATH", db_path):
                ordered = app.sorted_songs("favorites")

        self.assertEqual([song["url"] for song in ordered], ["b", "a", "c"])

    def test_songs_route_honors_favorites_sort(self) -> None:
        songs = [
            {"url": "a", "artist": "A", "title": "A Song", "favorites_count": 5},
            {"url": "b", "artist": "B", "title": "B Song", "favorites_count": 50},
        ]
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            db = empty_db()
            db["songs"] = {song["url"]: song for song in songs}
            save_db(db_path, db)
            with patch("medleys.web.app.DB_PATH", db_path):
                response = self.client.get("/songs?sort=favorites&lang=es")

        self.assertEqual(response.status_code, 200)
        self.assertLess(response.data.index(b"B Song"), response.data.index(b"A Song"))


class MedleyContextSortTest(unittest.TestCase):
    def test_build_medley_context_orders_songs_by_favorites_when_requested(self) -> None:
        source = "list:1:Party"
        songs = {
            "one": {
                "url": "one",
                "artist": "One",
                "title": "First",
                "chorus_chords": ["C", "G", "Am", "F"],
                "favorites_count": 5,
                "sources": [source],
            },
            "two": {
                "url": "two",
                "artist": "Two",
                "title": "Second",
                "chorus_chords": ["D", "A", "Bm", "G"],
                "favorites_count": 50,
                "sources": [source],
            },
        }
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            db = empty_db()
            db["songs"] = songs
            save_db(db_path, db)
            with (
                patch("medleys.web.app.DB_PATH", db_path),
                app.app.test_request_context(f"/medley/{source}?sort=favorites"),
            ):
                context = app.build_medley_context(source)

        self.assertEqual(context["sort"], "favorites")
        self.assertEqual([song["title"] for song in context["songs"]], ["Second", "First"])

    def test_build_medley_context_defaults_to_transition_sort_for_invalid_value(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            save_db(db_path, empty_db())
            with (
                patch("medleys.web.app.DB_PATH", db_path),
                app.app.test_request_context("/medley/list:1:Party?sort=bogus"),
            ):
                context = app.build_medley_context("list:1:Party")

        self.assertEqual(context["sort"], "transition")


if __name__ == "__main__":
    unittest.main()
