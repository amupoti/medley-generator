#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from ug_chorus_chords import (
    dismiss_cookie_popup,
    extract_artist,
    extract_chorus_chords,
    extract_title,
    launch_browser,
)


EXPLORE_URL = "https://www.ultimate-guitar.com/explore"


def scrape_explore(source_url: str, limit: Optional[int], delay_ms: int, skip_existing: Optional[Path]) -> dict:
    from playwright.sync_api import sync_playwright

    existing_urls = load_existing_urls(skip_existing)
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            page = browser.new_page()
            page.goto(source_url, wait_until="domcontentloaded", timeout=60000)
            dismiss_cookie_popup(page)
            page.wait_for_timeout(delay_ms)
            songs = extract_explore_links(page, limit)
        finally:
            browser.close()

        skipped = [song for song in songs if song["url"] in existing_urls]
        songs_to_scrape = [song for song in songs if song["url"] not in existing_urls]
        results = []
        failures = []
        for song in songs_to_scrape:
            try:
                results.append(scrape_song_with_retry(playwright, song, delay_ms))
            except Exception as exc:
                failures.append({**song, "error": str(exc)})

        return {
            "source": source_url,
            "discovered_count": len(songs),
            "skipped_count": len(skipped),
            "count": len(results),
            "failure_count": len(failures),
            "songs": results,
            "skipped": skipped,
            "failures": failures,
        }


def load_existing_urls(path: Optional[Path]) -> set[str]:
    if not path:
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    songs = data["songs"] if isinstance(data, dict) and "songs" in data else data
    return {song["url"] for song in songs if song.get("url")}


def extract_explore_links(page, limit: Optional[int]) -> list[dict]:
    links = page.locator('a[href*="/tab/"]')
    seen = set()
    songs = []

    for index in range(links.count()):
        link = links.nth(index)
        url = link.get_attribute("href")
        title = link.inner_text(timeout=3000).strip()
        if not url or not title or url in seen:
            continue
        seen.add(url)
        songs.append({"explore_rank": len(songs) + 1, "explore_title": title, "url": url})
        if limit and len(songs) >= limit:
            break

    return songs


def scrape_song_with_retry(playwright, explore_song: dict, delay_ms: int) -> dict:
    last_error = None
    for _ in range(2):
        browser = launch_browser(playwright)
        try:
            page = browser.new_page()
            return scrape_song(page, explore_song, delay_ms)
        except Exception as exc:
            last_error = exc
        finally:
            browser.close()
    raise last_error


def scrape_song(page, explore_song: dict, delay_ms: int) -> dict:
    page.goto(explore_song["url"], wait_until="domcontentloaded", timeout=60000)
    dismiss_cookie_popup(page)
    page.wait_for_timeout(delay_ms)
    text = page.locator("body").inner_text(timeout=10000)
    chords = extract_chorus_chords(text)

    return {
        **explore_song,
        "title": extract_title(text, page.title()),
        "artist": extract_artist(text, page.title()),
        "chorus_chords": chords,
        "has_chorus": bool(chords),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape Ultimate Guitar Explore songs and store first-chorus chords."
    )
    parser.add_argument(
        "--url",
        default=EXPLORE_URL,
        help="Ultimate Guitar Explore URL to scrape",
    )
    parser.add_argument(
        "--skip-existing",
        type=Path,
        default=None,
        help="Existing scrape JSON whose URLs should not be scraped again",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of Explore songs to scrape",
    )
    parser.add_argument(
        "--delay-ms",
        type=int,
        default=3000,
        help="Delay after each page load before reading rendered text",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="ug_explore_choruses.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    data = scrape_explore(args.url, args.limit, args.delay_ms, args.skip_existing)
    Path(args.output).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "discovered_count": data["discovered_count"],
                "skipped_count": data["skipped_count"],
                "count": data["count"],
                "failure_count": data["failure_count"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
