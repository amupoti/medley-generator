from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from song_db import SongRecord
from ug_chorus_chords import (
    dismiss_cookie_popup,
    extract_store,
    launch_browser,
    load_html_with_urllib,
)

SEARCH_URL = "https://www.ultimate-guitar.com/search.php"


def build_group_search_url(group: str) -> str:
    return f"{SEARCH_URL}?{urlencode({'title': group, 'page': 1, 'type': 300})}"


def find_records(node: Any, required: set[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if required <= node.keys():
            records.append(node)
        for value in node.values():
            records.extend(find_records(value, required))
    elif isinstance(node, list):
        for item in node:
            records.extend(find_records(item, required))
    return records


def extract_chord_tabs(store: dict[str, Any], group: str, limit: int) -> list[SongRecord]:
    candidates = find_records(store, {"tab_url", "song_name", "artist_name", "type"})
    chords = [
        tab
        for tab in candidates
        if str(tab["type"]).casefold() == "chords"
        and str(tab["artist_name"]).casefold() == group.casefold()
    ]
    chords.sort(
        key=lambda tab: (float(tab.get("rating") or 0), int(tab.get("votes") or 0)), reverse=True
    )
    songs: list[SongRecord] = []
    seen_titles: set[str] = set()
    for tab in chords:
        title = str(tab["song_name"])
        if title.casefold() in seen_titles:
            continue
        seen_titles.add(title.casefold())
        songs.append(
            {
                "url": str(tab["tab_url"]),
                "artist": str(tab["artist_name"]),
                "title": title,
                "explore_title": title,
                "explore_rank": len(songs) + 1,
            }
        )
        if len(songs) >= limit:
            break
    return songs


def load_search_store_with_browser(url: str, delay_ms: int) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            dismiss_cookie_popup(page)
            page.wait_for_timeout(delay_ms)
            return extract_store(page.content())
        finally:
            browser.close()


def load_search_store(url: str, delay_ms: int) -> dict[str, Any]:
    try:
        return extract_store(load_html_with_urllib(url))
    except ValueError as direct_error:
        try:
            return load_search_store_with_browser(url, delay_ms)
        except ValueError as browser_error:
            raise ValueError(
                "Ultimate Guitar search data was unavailable in both the direct and browser "
                f"responses: {direct_error}; {browser_error}"
            ) from browser_error


def discover_group_songs(group: str, limit: int, delay_ms: int) -> list[SongRecord]:
    url = build_group_search_url(group)
    songs = extract_chord_tabs(load_search_store(url, delay_ms), group, limit)
    if not songs:
        raise ValueError(f"No chord tabs found for Ultimate Guitar artist: {group}")
    return songs
