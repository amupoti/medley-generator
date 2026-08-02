import html
import json
import unittest
from email.message import Message
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError, URLError

import medleys.ultimate_guitar.song as ug


class StoreExtractionTest(unittest.TestCase):
    def test_extract_store_decodes_embedded_json(self) -> None:
        store = {"page": {"data": {"wiki_tab": {"content": "[Chorus]"}}}}
        encoded = html.escape(json.dumps(store), quote=True)

        self.assertEqual(
            ug.extract_store(f'<div class="other js-store" data-content="{encoded}"></div>'), store
        )

    def test_extract_store_rejects_blocked_and_missing_content(self) -> None:
        with self.assertRaisesRegex(ValueError, "Cloudflare"):
            ug.extract_store("Just a moment...")
        with self.assertRaisesRegex(ValueError, "js-store"):
            ug.extract_store("<html></html>")

    def test_find_tab_view_recurses_through_dicts_and_lists(self) -> None:
        expected = {"wiki_tab": {"content": "song"}, "song_name": "Title"}
        self.assertIs(ug.find_tab_view({"outer": [{"nested": expected}]}), expected)
        self.assertIsNone(ug.find_tab_view({"outer": ["value"]}))

    def test_extract_song_reads_tab_metadata(self) -> None:
        song = ug.extract_song(
            {
                "nested": {
                    "wiki_tab": {"content": "[Chorus]\nC G"},
                    "song_name": "Song",
                    "artist_name": "Artist",
                }
            }
        )
        self.assertEqual(song, {"title": "Song", "artist": "Artist", "content": "[Chorus]\nC G"})

    def test_extract_song_rejects_missing_view_or_content(self) -> None:
        with self.assertRaisesRegex(ValueError, "tab_view"):
            ug.extract_song({})
        with self.assertRaisesRegex(ValueError, "tab content"):
            ug.extract_song({"wiki_tab": {}})


class LoadingTest(unittest.TestCase):
    def test_load_song_reads_local_html(self) -> None:
        store = {
            "wiki_tab": {"content": "[Chorus]\nC"},
            "song_name": "Local",
            "artist_name": "Artist",
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "song.html"
            encoded = html.escape(json.dumps(store), quote=True)
            path.write_text(
                f'<div class="js-store" data-content="{encoded}"></div>',
                encoding="utf-8",
            )
            song = ug.load_song(str(path))

        self.assertEqual(song["title"], "Local")

    @patch("medleys.ultimate_guitar.song.load_song_with_browser")
    @patch("medleys.ultimate_guitar.song.load_html_with_urllib", return_value="<html></html>")
    def test_load_song_falls_back_when_remote_store_is_missing(
        self, _load_html: MagicMock, browser: MagicMock
    ) -> None:
        browser.return_value = {"title": "Browser"}
        self.assertEqual(ug.load_song("https://example.test/song"), {"title": "Browser"})
        browser.assert_called_once_with("https://example.test/song")

    @patch("medleys.ultimate_guitar.song.urlopen")
    def test_load_html_with_urllib_decodes_response(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        response.read.return_value = b"caf\xc3\xa9"
        urlopen.return_value.__enter__.return_value = response
        self.assertEqual(ug.load_html_with_urllib("https://example.test"), "café")

    @patch("medleys.ultimate_guitar.song.urlopen")
    def test_load_html_with_urllib_returns_http_error_body(self, urlopen: MagicMock) -> None:
        error = HTTPError("https://example.test", 403, "Forbidden", Message(), BytesIO(b"blocked"))
        urlopen.side_effect = error
        self.assertEqual(ug.load_html_with_urllib("https://example.test"), "blocked")

    @patch("medleys.ultimate_guitar.song.load_html_with_browser", return_value="browser html")
    @patch("medleys.ultimate_guitar.song.urlopen", side_effect=URLError("offline"))
    def test_load_html_with_urllib_uses_browser_for_url_errors(
        self, _urlopen: MagicMock, browser: MagicMock
    ) -> None:
        self.assertEqual(ug.load_html_with_urllib("https://example.test"), "browser html")
        browser.assert_called_once_with("https://example.test")

    def test_launch_browser_retries_without_chrome_channel(self) -> None:
        playwright = MagicMock()
        playwright.chromium.launch.side_effect = [RuntimeError("missing"), "browser"]
        self.assertEqual(ug.launch_browser(playwright), "browser")
        self.assertEqual(playwright.chromium.launch.call_count, 2)
        self.assertEqual(
            playwright.chromium.launch.call_args_list,
            [call(channel="chrome", headless=True), call(headless=True)],
        )

    def test_dismiss_cookie_popup_tries_labels_until_button_exists(self) -> None:
        page = MagicMock()
        absent = MagicMock()
        absent.count.return_value = 0
        present = MagicMock()
        present.count.return_value = 1
        page.get_by_role.side_effect = [RuntimeError("detached"), absent, present]

        ug.dismiss_cookie_popup(page)

        present.first.click.assert_called_once_with(timeout=3000)
        page.wait_for_timeout.assert_called_once_with(1000)


class ChorusParsingTest(unittest.TestCase):
    def test_extracts_only_first_chorus_and_preserves_positions(self) -> None:
        content = """[Verse 1]
Ignore me
[Chorus]
[tab][ch]C[/ch]Hello [ch]G[/ch]world[/tab]
  Am   F
sing along
[Verse 2]
Ignore me too
[Chorus 2]
D A
"""
        lines = ug.extract_chorus_lines(content)

        self.assertEqual(
            lines,
            [
                {
                    "lyrics": "Hello world",
                    "chords": [{"symbol": "C", "position": 0}, {"symbol": "G", "position": 6}],
                },
                {
                    "lyrics": "",
                    "chords": [{"symbol": "Am", "position": 2}, {"symbol": "F", "position": 7}],
                },
                {"lyrics": "sing along", "chords": []},
            ],
        )
        self.assertEqual(ug.extract_chorus_chords(content), ["C", "G", "Am", "F"])

    def test_repeated_chorus_section_stops_first_chorus(self) -> None:
        self.assertEqual(ug.first_chorus_source_lines("[Chorus]\nC\n[Chorus 2]\nG"), ["C"])

    def test_parse_marked_line_skips_empty_chord_marker(self) -> None:
        self.assertEqual(
            ug.parse_chorus_line("A[ch] [/ch]B"),
            {"lyrics": "AB", "chords": []},
        )

    def test_chord_and_section_recognition(self) -> None:
        self.assertTrue(ug.is_chorus_section(" Chorus 12 "))
        self.assertFalse(ug.is_chorus_section("Pre-Chorus"))
        self.assertTrue(ug.is_chord_line("C G/B Am7"))
        self.assertFalse(ug.is_chord_line(""))
        self.assertFalse(ug.is_chord_line("sing along"))

    def test_title_and_artist_use_text_then_page_title_fallbacks(self) -> None:
        text = "Wonderwall Chords by Oasis"
        self.assertEqual(ug.extract_title(text, "ignored"), "Wonderwall")
        self.assertEqual(ug.extract_artist(text, "ignored"), "Oasis")
        self.assertEqual(ug.extract_title("", "WONDERWALL CHORDS"), "Wonderwall")
        self.assertEqual(ug.extract_artist("", "Wonderwall by Oasis @ Ultimate Guitar"), "Oasis")
        self.assertIsNone(ug.extract_title("", ""))
        self.assertIsNone(ug.extract_artist("", "Wonderwall"))


if __name__ == "__main__":
    unittest.main()
