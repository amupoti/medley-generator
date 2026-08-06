from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import medleys.ultimate_guitar.explore as explore


class ExistingUrlsTest(unittest.TestCase):
    def test_load_existing_urls_handles_missing_wrapped_and_bare_data(self) -> None:
        self.assertEqual(explore.load_existing_urls(None), set())
        with TemporaryDirectory() as directory:
            path = Path(directory) / "songs.json"
            songs = [{"url": "one"}, {"title": "missing"}]
            path.write_text(json.dumps({"songs": songs}), encoding="utf-8")
            self.assertEqual(explore.load_existing_urls(path), {"one"})
            path.write_text(json.dumps(songs), encoding="utf-8")
            self.assertEqual(explore.load_existing_urls(path), {"one"})


class ExploreLinksTest(unittest.TestCase):
    def test_extract_links_skips_incomplete_duplicates_and_obeys_limit(self) -> None:
        def link(url: str | None, title: str) -> MagicMock:
            item = MagicMock()
            item.get_attribute.return_value = url
            item.inner_text.return_value = title
            return item

        items = [
            link(None, "Missing URL"),
            link("one", ""),
            link("one", " First "),
            link("one", "Duplicate"),
            link("two", "Second"),
            link("three", "Third"),
        ]
        links = MagicMock()
        links.count.return_value = len(items)
        links.nth.side_effect = items
        page = MagicMock()
        page.locator.return_value = links

        self.assertEqual(
            explore.extract_explore_links(page, 2),
            [
                {"explore_rank": 1, "explore_title": "First", "url": "one"},
                {"explore_rank": 2, "explore_title": "Second", "url": "two"},
            ],
        )


class ScrapeSongTest(unittest.TestCase):
    @patch("medleys.ultimate_guitar.explore.detect_chorus")
    @patch("medleys.ultimate_guitar.explore.extract_store")
    @patch("medleys.ultimate_guitar.explore.extract_store_song")
    def test_scrape_song_prefers_store_metadata_and_content(
        self,
        extract_store_song: MagicMock,
        extract_store: MagicMock,
        detect_chorus: MagicMock,
    ) -> None:
        page = MagicMock()
        page.title.return_value = "Page Title"
        page.locator.return_value.inner_text.return_value = "Body text"
        page.content.return_value = "HTML"
        extract_store.return_value = {"store": True}
        extract_store_song.return_value = {
            "title": "Store Song",
            "artist": "Store Artist",
            "content": "Store content",
            "favorites_count": 2130,
            "view_total": 28239,
        }
        detect_chorus.return_value = {
            "lines": [{"lyrics": "hi", "chords": []}],
            "chords": ["C", "G"],
            "method": "explicit",
            "confidence": 1.0,
        }

        result = explore.scrape_song(page, {"url": "one", "explore_rank": 1}, 25)

        self.assertEqual(result["title"], "Store Song")
        self.assertEqual(result["artist"], "Store Artist")
        self.assertTrue(result["has_chorus"])
        self.assertEqual(result["chorus_chords"], ["C", "G"])
        self.assertEqual(result["chorus_detection"], "explicit")
        self.assertEqual(result["chorus_confidence"], 1.0)
        self.assertEqual(result["favorites_count"], 2130)
        self.assertEqual(result["view_total"], 28239)
        detect_chorus.assert_called_once_with("Store content")
        page.goto.assert_called_once_with("one", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout.assert_any_call(25)

    @patch("medleys.ultimate_guitar.explore.extract_artist", return_value="Fallback Artist")
    @patch("medleys.ultimate_guitar.explore.extract_title", return_value="Fallback Song")
    @patch("medleys.ultimate_guitar.explore.detect_chorus")
    @patch("medleys.ultimate_guitar.explore.extract_store", side_effect=ValueError("missing"))
    def test_scrape_song_falls_back_to_rendered_text(
        self,
        _extract_store: MagicMock,
        detect_chorus: MagicMock,
        _extract_title: MagicMock,
        _extract_artist: MagicMock,
    ) -> None:
        page = MagicMock()
        page.title.return_value = "Page Title"
        page.locator.return_value.inner_text.return_value = "Body text"
        detect_chorus.return_value = {
            "lines": [],
            "chords": [],
            "method": "none",
            "confidence": 0.0,
        }

        result = explore.scrape_song(page, {"url": "one"}, 0)

        self.assertEqual(result["title"], "Fallback Song")
        self.assertEqual(result["artist"], "Fallback Artist")
        self.assertFalse(result["has_chorus"])
        self.assertEqual(result["chorus_detection"], "none")
        self.assertEqual(result["chorus_confidence"], 0.0)
        self.assertIsNone(result["favorites_count"])
        self.assertIsNone(result["view_total"])
        detect_chorus.assert_called_once_with("Body text")

    @patch("medleys.ultimate_guitar.explore.extract_artist", return_value="Artist")
    @patch("medleys.ultimate_guitar.explore.extract_title", return_value="Title")
    @patch("medleys.ultimate_guitar.explore.detect_chorus")
    @patch("medleys.ultimate_guitar.explore.extract_store", side_effect=ValueError("missing"))
    def test_scrape_song_marks_inferred_chorus_as_has_chorus(
        self,
        _extract_store: MagicMock,
        detect_chorus: MagicMock,
        _extract_title: MagicMock,
        _extract_artist: MagicMock,
    ) -> None:
        page = MagicMock()
        page.title.return_value = "Page Title"
        page.locator.return_value.inner_text.return_value = "Body text"
        detect_chorus.return_value = {
            "lines": [{"lyrics": "we will rock you", "chords": []}],
            "chords": ["C", "G"],
            "method": "inferred",
            "confidence": 0.72,
        }

        result = explore.scrape_song(page, {"url": "one"}, 0)

        self.assertTrue(result["has_chorus"])
        self.assertEqual(result["chorus_detection"], "inferred")
        self.assertEqual(result["chorus_confidence"], 0.72)

    @patch("medleys.ultimate_guitar.explore.scrape_song")
    @patch("medleys.ultimate_guitar.explore.launch_browser")
    def test_scrape_song_with_retry_closes_each_browser(
        self, launch_browser: MagicMock, scrape_song: MagicMock
    ) -> None:
        first_browser = MagicMock()
        second_browser = MagicMock()
        launch_browser.side_effect = [first_browser, second_browser]
        scrape_song.side_effect = [RuntimeError("first"), {"url": "one"}]

        result = explore.scrape_song_with_retry(MagicMock(), {"url": "one"}, 0)

        self.assertEqual(result, {"url": "one"})
        first_browser.close.assert_called_once()
        second_browser.close.assert_called_once()

    @patch(
        "medleys.ultimate_guitar.explore.scrape_song",
        side_effect=[RuntimeError("first"), RuntimeError("last")],
    )
    @patch("medleys.ultimate_guitar.explore.launch_browser")
    def test_scrape_song_with_retry_raises_last_error(
        self, launch_browser: MagicMock, _scrape_song: MagicMock
    ) -> None:
        launch_browser.side_effect = [MagicMock(), MagicMock()]
        with self.assertRaisesRegex(RuntimeError, "last"):
            explore.scrape_song_with_retry(MagicMock(), {"url": "one"}, 0)


class ScrapeExploreTest(unittest.TestCase):
    @patch("medleys.ultimate_guitar.explore.scrape_song_with_retry")
    @patch("medleys.ultimate_guitar.explore.extract_explore_links")
    @patch("medleys.ultimate_guitar.explore.launch_browser")
    @patch("playwright.sync_api.sync_playwright")
    def test_scrape_explore_reports_skips_successes_and_failures(
        self,
        sync_playwright: MagicMock,
        launch_browser: MagicMock,
        extract_links: MagicMock,
        scrape_with_retry: MagicMock,
    ) -> None:
        sync_playwright.return_value.__enter__.return_value = MagicMock()
        browser = MagicMock()
        launch_browser.return_value = browser
        extract_links.return_value = [
            {"url": "skip", "explore_title": "Skip"},
            {"url": "good", "explore_title": "Good"},
            {"url": "bad", "explore_title": "Bad"},
        ]
        scrape_with_retry.side_effect = [{"url": "good"}, RuntimeError("blocked")]

        with TemporaryDirectory() as directory:
            existing = Path(directory) / "existing.json"
            existing.write_text(json.dumps([{"url": "skip"}]), encoding="utf-8")
            result = explore.scrape_explore("source", 3, 10, existing)

        self.assertEqual(result["discovered_count"], 3)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["failure_count"], 1)
        self.assertEqual(result["failures"][0]["error"], "blocked")
        browser.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
