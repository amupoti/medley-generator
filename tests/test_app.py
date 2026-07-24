import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import MagicMock, patch

import app
from song_db import empty_db, load_db, save_db


class TranslationsTest(unittest.TestCase):
    def test_loads_translations_for_every_supported_language(self) -> None:
        translations = app.load_translations()

        self.assertEqual(set(translations), set(app.SUPPORTED_LANGUAGES))
        for labels in translations.values():
            self.assertTrue(labels)
            self.assertEqual(set(labels), set(translations[app.DEFAULT_LANG]))


class AppHelpersTest(unittest.TestCase):
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

    @patch("app.uuid.uuid4")
    @patch("app.now_iso", side_effect=["created", "created", "updated"])
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

    @patch("app.update_job")
    def test_run_job_records_success_and_failure(self, update_job: MagicMock) -> None:
        app.run_job("one", lambda value: {"value": value}, (3,))
        self.assertEqual(update_job.call_args_list[-1].kwargs["status"], "complete")

        update_job.reset_mock()
        app.run_job("two", MagicMock(side_effect=RuntimeError("broken")), ())
        self.assertEqual(
            update_job.call_args_list[-1].kwargs, {"status": "failed", "error": "broken"}
        )

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

    @patch("app.run_background")
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

    @patch("app.analyze_songs")
    @patch("app.store_medley")
    @patch("app.discover_group_songs")
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

    @patch("app.run_background")
    def test_creates_url_list_job(self, run_background: MagicMock) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            with patch("app.DB_PATH", db_path):
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

    @patch("app.run_background")
    def test_edits_saved_medley_and_reuses_source_id(self, run_background: MagicMock) -> None:
        source_id = "list:1234:Fiesta"
        first = "https://tabs.ultimate-guitar.com/tab/oasis/wonderwall-chords-27596"
        second = "https://www.ultimate-guitar.com/tab/blur/song-chords-123"
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            db = empty_db()
            save_db(db_path, db)
            with patch("app.DB_PATH", db_path):
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
            with patch("app.DB_PATH", db_path):
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

    def test_edit_page_reconstructs_legacy_medley_urls(self) -> None:
        source_id = "list:1234:Legacy"
        url = "https://tabs.ultimate-guitar.com/tab/oasis/wonderwall-chords-27596"
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            db = empty_db()
            db["songs"] = {url: {"url": url, "sources": [source_id], "explore_rank": 1}}
            save_db(db_path, db)
            with patch("app.DB_PATH", db_path):
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
            with patch("app.DB_PATH", db_path):
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
            with patch("app.DB_PATH", db_path):
                response = self.client.get("/api/songs")

        self.assertEqual(len(response.get_json()["songs"]), 20)


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
            with patch("app.DB_PATH", db_path):
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
            with patch("app.DB_PATH", db_path):
                exclusions = app.comparison_exclusions(source)

        self.assertEqual(
            [(song["url"], song["exclusion_reason"]) for song in exclusions],
            [("duplicate", "duplicate"), ("missing", "no_chorus"), ("failed", "scrape_failed")],
        )
        self.assertEqual(exclusions[-1]["exclusion_detail"], "blocked")


if __name__ == "__main__":
    unittest.main()
