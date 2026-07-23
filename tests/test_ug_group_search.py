import unittest
from unittest.mock import MagicMock, patch

import ug_group_search as search


class GroupSearchTest(unittest.TestCase):
    def test_builds_encoded_chord_search_url(self) -> None:
        self.assertEqual(
            search.build_group_search_url("AC/DC"),
            "https://www.ultimate-guitar.com/search.php?title=AC%2FDC&page=1&type=300",
        )

    def test_filters_chords_artist_and_duplicate_versions(self) -> None:
        store = {
            "tabs": [
                {
                    "song_name": "One",
                    "artist_name": "Band",
                    "type": "Chords",
                    "tab_url": "low",
                    "rating": 3,
                    "votes": 2,
                },
                {
                    "song_name": "One",
                    "artist_name": "Band",
                    "type": "Chords",
                    "tab_url": "best",
                    "rating": 5,
                    "votes": 10,
                },
                {
                    "song_name": "Two",
                    "artist_name": "Band",
                    "type": "Chords",
                    "tab_url": "two",
                    "rating": 4,
                    "votes": 4,
                },
                {
                    "song_name": "Bass",
                    "artist_name": "Band",
                    "type": "Bass Tabs",
                    "tab_url": "bass",
                },
                {
                    "song_name": "Other",
                    "artist_name": "Other",
                    "type": "Chords",
                    "tab_url": "other",
                },
            ]
        }
        songs = search.extract_chord_tabs(store, "band", 2)
        self.assertEqual([song["url"] for song in songs], ["best", "two"])
        self.assertEqual([song["explore_rank"] for song in songs], [1, 2])

    @patch("ug_group_search.load_html_with_urllib", return_value="search html")
    @patch("ug_group_search.extract_store")
    def test_discovers_group_chord_tabs_from_direct_search_response(
        self, extract_store: MagicMock, load_html: MagicMock
    ) -> None:
        extract_store.return_value = {
            "tabs": [
                {
                    "song_name": "Song",
                    "artist_name": "Band",
                    "type": "Chords",
                    "tab_url": "tab",
                    "rating": 5,
                }
            ]
        }
        songs = search.discover_group_songs("Band", 10, 0)
        self.assertEqual(songs[0]["url"], "tab")
        load_html.assert_called_once_with(
            "https://www.ultimate-guitar.com/search.php?title=Band&page=1&type=300"
        )

    @patch("ug_group_search.load_search_store_with_browser")
    @patch("ug_group_search.extract_store", side_effect=ValueError("direct missing"))
    @patch("ug_group_search.load_html_with_urllib", return_value="missing store")
    def test_search_store_falls_back_to_browser(
        self, _load_html: MagicMock, _extract_store: MagicMock, browser: MagicMock
    ) -> None:
        browser.return_value = {"store": "browser"}

        self.assertEqual(search.load_search_store("search", 10), {"store": "browser"})
        browser.assert_called_once_with("search", 10)

    @patch(
        "ug_group_search.load_search_store_with_browser", side_effect=ValueError("browser missing")
    )
    @patch("ug_group_search.extract_store", side_effect=ValueError("direct missing"))
    @patch("ug_group_search.load_html_with_urllib", return_value="missing store")
    def test_search_store_reports_both_failures(
        self, _load_html: MagicMock, _extract_store: MagicMock, _browser: MagicMock
    ) -> None:
        with self.assertRaisesRegex(ValueError, "direct missing; browser missing"):
            search.load_search_store("search", 10)


if __name__ == "__main__":
    unittest.main()
