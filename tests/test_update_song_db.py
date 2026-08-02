import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import medleys.services.update_db as update_song_db
from medleys.database import empty_db, load_db, save_db


class UpdateDbTest(unittest.TestCase):
    @patch("medleys.services.update_db.scrape_song_with_retry")
    @patch("medleys.services.update_db.extract_explore_links")
    @patch("medleys.services.update_db.launch_browser")
    @patch("playwright.sync_api.sync_playwright")
    def test_update_db_skips_known_scrapes_new_and_records_failures(
        self,
        sync_playwright: MagicMock,
        launch_browser: MagicMock,
        extract_links: MagicMock,
        scrape_with_retry: MagicMock,
    ) -> None:
        sync_playwright.return_value.__enter__.return_value = MagicMock()
        browser = MagicMock()
        launch_browser.return_value = browser
        source = "https://example.test/explore"
        extract_links.return_value = [
            {"url": "known", "explore_rank": 1},
            {"url": "good", "explore_rank": 2},
            {"url": "bad", "explore_rank": 3},
        ]
        scrape_with_retry.side_effect = [
            {"url": "good", "artist": "Artist", "title": "Song", "chorus_chords": ["C"]},
            RuntimeError("blocked"),
        ]

        with TemporaryDirectory() as directory:
            path = Path(directory) / "songs.json"
            initial_db = empty_db()
            initial_db["songs"] = {
                "known": {
                    "url": "known",
                    "sources": [],
                    "errors": [],
                    "scrape_count": 1,
                }
            }
            save_db(path, initial_db)
            result = update_song_db.update_db(path, source, 3, 10, refresh=False)
            db = load_db(path)

        self.assertEqual(result["discovered_count"], 3)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["scraped_count"], 1)
        self.assertEqual(result["failure_count"], 1)
        self.assertEqual(result["total_db_songs"], 3)
        self.assertIn(source, db["songs"]["known"]["sources"])
        self.assertEqual(db["songs"]["good"]["title"], "Song")
        self.assertEqual(db["songs"]["bad"]["errors"][-1]["error"], "blocked")
        browser.close.assert_called_once()

    @patch("medleys.services.update_db.scrape_song_with_retry")
    @patch("medleys.services.update_db.extract_explore_links")
    @patch("medleys.services.update_db.launch_browser")
    @patch("playwright.sync_api.sync_playwright")
    def test_update_db_refreshes_known_song(
        self,
        sync_playwright: MagicMock,
        launch_browser: MagicMock,
        extract_links: MagicMock,
        scrape_with_retry: MagicMock,
    ) -> None:
        sync_playwright.return_value.__enter__.return_value = MagicMock()
        launch_browser.return_value = MagicMock()
        extract_links.return_value = [{"url": "known", "explore_rank": 1}]
        scrape_with_retry.return_value = {"url": "known", "title": "Refreshed"}

        with TemporaryDirectory() as directory:
            path = Path(directory) / "songs.json"
            initial_db = empty_db()
            initial_db["songs"] = {
                "known": {
                    "url": "known",
                    "sources": [],
                    "errors": [],
                    "scrape_count": 1,
                }
            }
            save_db(path, initial_db)
            result = update_song_db.update_db(path, "source", None, 0, refresh=True)
            db = load_db(path)

        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(result["scraped_count"], 1)
        self.assertEqual(db["songs"]["known"]["title"], "Refreshed")
        self.assertEqual(db["songs"]["known"]["scrape_count"], 2)


if __name__ == "__main__":
    unittest.main()
