import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app
from song_db import load_db, save_db


class ParseTabUrlsTest(unittest.TestCase):
    def test_parses_and_deduplicates_ultimate_guitar_tab_urls(self):
        first = "https://tabs.ultimate-guitar.com/tab/oasis/wonderwall-chords-27596"
        second = "https://www.ultimate-guitar.com/tab/blur/song-chords-123"

        self.assertEqual(app.parse_tab_urls(f"{first}\n\n{second}\n{first}"), [first, second])

    def test_rejects_non_tab_urls(self):
        with self.assertRaisesRegex(ValueError, "Not an Ultimate Guitar tab URL"):
            app.parse_tab_urls("https://www.ultimate-guitar.com/explore")

    def test_rejects_other_hosts(self):
        with self.assertRaisesRegex(ValueError, "Unsupported Ultimate Guitar URL"):
            app.parse_tab_urls("https://example.com/tab/artist/song")

        with self.assertRaisesRegex(ValueError, "Unsupported Ultimate Guitar URL"):
            app.parse_tab_urls("https://evilultimate-guitar.com/tab/artist/song")


class UrlListRouteTest(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    @patch("app.run_background")
    def test_creates_url_list_job(self, run_background):
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

    def test_requires_at_least_one_url(self):
        response = self.client.post("/analyze/url-list", data={"tab_urls": ""})

        self.assertEqual(response.status_code, 400)

    @patch("app.run_background")
    def test_edits_saved_medley_and_reuses_source_id(self, run_background):
        source_id = "list:1234:Fiesta"
        first = "https://tabs.ultimate-guitar.com/tab/oasis/wonderwall-chords-27596"
        second = "https://www.ultimate-guitar.com/tab/blur/song-chords-123"
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            db = {"version": 1, "songs": {}, "medleys": {}}
            save_db(db_path, db)
            with patch("app.DB_PATH", db_path):
                app.store_medley(source_id, "Fiesta", [first])
                response = self.client.post(
                    f"/medley/{source_id}/edit?lang=ca",
                    data={"medley_name": "Festa", "tab_urls": f"{first}\n{second}", "delay_ms": "0"},
                )
                saved = load_db(db_path)["medleys"][source_id]

        self.assertEqual(response.status_code, 302)
        self.assertEqual(saved["name"], "Festa")
        self.assertEqual(saved["urls"], [first, second])
        self.assertEqual(run_background.call_args.args[3], source_id)

    def test_edit_page_returns_saved_urls(self):
        source_id = "list:1234:Fiesta"
        url = "https://tabs.ultimate-guitar.com/tab/oasis/wonderwall-chords-27596"
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            save_db(db_path, {"version": 1, "songs": {}, "medleys": {}})
            with patch("app.DB_PATH", db_path):
                app.store_medley(source_id, "Fiesta", [url])
                response = self.client.get(f"/medley/{source_id}/edit?lang=es")

        self.assertEqual(response.status_code, 200)
        self.assertIn(url.encode(), response.data)

    def test_edit_page_reconstructs_legacy_medley_urls(self):
        source_id = "list:1234:Legacy"
        url = "https://tabs.ultimate-guitar.com/tab/oasis/wonderwall-chords-27596"
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            save_db(
                db_path,
                {
                    "version": 1,
                    "songs": {url: {"url": url, "sources": [source_id], "explore_rank": 1}},
                },
            )
            with patch("app.DB_PATH", db_path):
                response = self.client.get(f"/medley/{source_id}/edit")

        self.assertEqual(response.status_code, 200)
        self.assertIn(url.encode(), response.data)


class DeleteMedleyTest(unittest.TestCase):
    def test_deletes_only_source_association_and_keeps_songs(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "songs.json"
            save_db(
                db_path,
                {
                    "version": 1,
                    "songs": {
                        "one": {"url": "one", "sources": ["list:1:Party", "other"]},
                        "two": {"url": "two", "sources": ["list:1:Party"]},
                    },
                },
            )
            with patch("app.DB_PATH", db_path):
                response = app.app.test_client().post("/medley/list:1:Party/delete?lang=es")

            db = load_db(db_path)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(set(db["songs"]), {"one", "two"})
            self.assertEqual(db["songs"]["one"]["sources"], ["other"])
            self.assertEqual(db["songs"]["two"]["sources"], [])


class ComparisonExclusionsTest(unittest.TestCase):
    def test_reports_failed_missing_chorus_and_duplicate_songs(self):
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
            save_db(db_path, {"version": 1, "songs": songs})
            with patch("app.DB_PATH", db_path):
                exclusions = app.comparison_exclusions(source)

        self.assertEqual(
            [(song["url"], song["exclusion_reason"]) for song in exclusions],
            [("duplicate", "duplicate"), ("missing", "no_chorus"), ("failed", "scrape_failed")],
        )
        self.assertEqual(exclusions[-1]["exclusion_detail"], "blocked")


if __name__ == "__main__":
    unittest.main()
