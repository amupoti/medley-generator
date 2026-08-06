#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from medleys.database import SongRecord
from medleys.ultimate_guitar.chorus_detection import ChorusLine, detect_chorus

JSONObject = dict[str, Any]

TITLE_RE = re.compile(r"(?P<title>.+?)\s+Chords\s+by\s+(?P<artist>.+)", re.IGNORECASE)
UG_BLOCKED_RE = re.compile(r"Just a moment\.\.\.|challenges\.cloudflare\.com", re.IGNORECASE)


class StoreParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.data_content: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        classes = (attr_map.get("class") or "").split()
        if "js-store" in classes and "data-content" in attr_map:
            self.data_content = attr_map["data-content"]


def load_song(source: str) -> SongRecord:
    path = Path(source)
    if path.exists():
        return extract_song(extract_store(path.read_text(encoding="utf-8")))

    html_text = load_html_with_urllib(source)
    try:
        return extract_song(extract_store(html_text))
    except ValueError:
        return load_song_with_browser(source)


def load_html_with_urllib(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36"
            )
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            return cast(str, response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if body:
            return body
        raise
    except URLError:
        return load_html_with_browser(url)


def load_html_with_browser(url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "URL fetch was blocked and Playwright is not installed. "
            "Install it with: python3 -m pip install playwright && "
            "python3 -m playwright install chromium"
        ) from exc

    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                )
            )
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            dismiss_cookie_popup(page)
            page.wait_for_timeout(8000)
            return cast(str, page.content())
        finally:
            browser.close()


def load_song_with_browser(url: str) -> SongRecord:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "URL fetch was blocked and Playwright is not installed. "
            "Install it with: python3 -m pip install playwright && "
            "python3 -m playwright install chromium"
        ) from exc

    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            dismiss_cookie_popup(page)
            page.wait_for_timeout(8000)
            text = page.locator("body").inner_text(timeout=10000)
            return {
                "title": extract_title(text, page.title()),
                "artist": extract_artist(text, page.title()),
                "content": text,
            }
        finally:
            browser.close()


def launch_browser(playwright: Any) -> Any:
    try:
        return playwright.chromium.launch(channel="chrome", headless=True)
    except Exception:
        return playwright.chromium.launch(headless=True)


def dismiss_cookie_popup(page: Any) -> None:
    labels = ["DISAGREE", "AGREE", "Reject All", "Accept All"]
    for label in labels:
        try:
            button = page.get_by_role("button", name=label, exact=True)
            if button.count():
                button.first.click(timeout=3000)
                page.wait_for_timeout(1000)
                return
        except Exception:
            continue


def extract_store(html_text: str) -> JSONObject:
    if UG_BLOCKED_RE.search(html_text):
        raise ValueError(
            "Ultimate Guitar returned a Cloudflare challenge and the browser fallback did not "
            "reach the page data"
        )

    parser = StoreParser()
    parser.feed(html_text)
    if not parser.data_content:
        raise ValueError("Could not find Ultimate Guitar js-store data-content")
    return cast(JSONObject, json.loads(html.unescape(parser.data_content)))


def find_tab_view(node: Any) -> JSONObject | None:
    if isinstance(node, dict):
        if "wiki_tab" in node and isinstance(node["wiki_tab"], dict):
            return cast(JSONObject, node)
        for value in node.values():
            found = find_tab_view(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_tab_view(item)
            if found:
                return found
    return None


def extract_song(store: JSONObject) -> SongRecord:
    tab_view = find_tab_view(store)
    if not tab_view:
        raise ValueError("Could not find tab_view/wiki_tab data")

    wiki_tab = tab_view.get("wiki_tab") or {}
    content = wiki_tab.get("content")
    if not content:
        raise ValueError("Could not find tab content")

    stats = tab_view.get("stats") or {}
    # Some tab pages only carry title/artist under headerMeta, leaving
    # song_name/title/artist_name absent from tab_view entirely.
    header_meta = tab_view.get("headerMeta") or {}
    header_artists = header_meta.get("artists") or []
    return {
        "title": tab_view.get("song_name") or tab_view.get("title") or header_meta.get("name"),
        "artist": tab_view.get("artist_name")
        or (header_artists[0].get("name") if header_artists else None),
        "content": content,
        "favorites_count": stats.get("favorites_count"),
        "view_total": stats.get("view_total"),
    }


def extract_title(text: str, page_title: str) -> str | None:
    match = TITLE_RE.search(text)
    if match:
        return match.group("title").strip()
    return page_title.split(" CHORDS", 1)[0].title() if page_title else None


def extract_artist(text: str, page_title: str) -> str | None:
    match = TITLE_RE.search(text)
    if match:
        return match.group("artist").strip()
    by_marker = " by "
    if by_marker in page_title:
        return page_title.split(by_marker, 1)[1].split("@", 1)[0].strip()
    return None


def extract_chorus_lines(content: str) -> list[ChorusLine]:
    return detect_chorus(content)["lines"]


def extract_chorus_chords(content: str) -> list[str]:
    return detect_chorus(content)["chords"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract chorus chords from an Ultimate Guitar chord page."
    )
    parser.add_argument("source", help="Ultimate Guitar URL or a saved HTML file")
    parser.add_argument(
        "-o",
        "--output",
        default="song_chorus_chords.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    song = load_song(args.source)
    output = {
        "title": song["title"],
        "artist": song["artist"],
        "favorites_count": song.get("favorites_count"),
        "view_total": song.get("view_total"),
        "chorus_chords": extract_chorus_chords(song["content"]),
        "chorus_lines": extract_chorus_lines(song["content"]),
    }

    Path(args.output).write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
