#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from medleys.database import db_urls, load_db, mark_seen, merge_song, record_failure, save_db
from medleys.ultimate_guitar.explore import (
    EXPLORE_URL,
    extract_explore_links,
    scrape_song_with_retry,
)
from medleys.ultimate_guitar.song import dismiss_cookie_popup, launch_browser

UpdateSummary = dict[str, Any]
ProgressCallback = Callable[..., None]


def update_db(
    db_path: Path,
    source_url: str,
    limit: int | None,
    delay_ms: int,
    refresh: bool,
    progress: ProgressCallback | None = None,
) -> UpdateSummary:
    from playwright.sync_api import sync_playwright

    db = load_db(db_path)
    known_urls = db_urls(db)

    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            page = browser.new_page()
            page.goto(source_url, wait_until="domcontentloaded", timeout=60000)
            dismiss_cookie_popup(page)
            page.wait_for_timeout(delay_ms)
            discovered = extract_explore_links(page, limit)
        finally:
            browser.close()

        to_scrape = []
        skipped = []
        for song in discovered:
            if not refresh and song["url"] in known_urls:
                mark_seen(db, song, source_url)
                skipped.append(song)
            else:
                to_scrape.append(song)

        scraped = []
        failures = []
        if progress:
            progress(
                total=len(discovered),
                processed=len(skipped),
                skipped=len(skipped),
                scraped=0,
                failed=0,
            )
        for song in to_scrape:
            try:
                scraped_song = scrape_song_with_retry(playwright, song, delay_ms)
                merge_song(db, scraped_song, source_url)
                scraped.append(scraped_song)
            except Exception as exc:
                error = str(exc)
                record_failure(db, song, source_url, error)
                failures.append({**song, "error": error})
            if progress:
                progress(
                    total=len(discovered),
                    processed=len(skipped) + len(scraped) + len(failures),
                    skipped=len(skipped),
                    scraped=len(scraped),
                    failed=len(failures),
                )

    save_db(db_path, db)
    return {
        "db": str(db_path),
        "source": source_url,
        "discovered_count": len(discovered),
        "skipped_count": len(skipped),
        "scraped_count": len(scraped),
        "failure_count": len(failures),
        "total_db_songs": len(db["songs"]),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update local Ultimate Guitar song DB from an Explore URL."
    )
    parser.add_argument("--url", default=EXPLORE_URL)
    parser.add_argument("--db", type=Path, default=Path("songs_db.json"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay-ms", type=int, default=2000)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    summary = update_db(args.db, args.url, args.limit, args.delay_ms, args.refresh)
    print(json.dumps({key: value for key, value in summary.items() if key != "failures"}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
