#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_db() -> dict:
    return {
        "version": 1,
        "updated_at": now_iso(),
        "songs": {},
    }


def load_db(path: Path) -> dict:
    if not path.exists():
        return empty_db()
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data.get("songs"), list):
        data["songs"] = {song["url"]: song for song in data["songs"] if song.get("url")}
    data.setdefault("version", 1)
    data.setdefault("songs", {})
    return data


def save_db(path: Path, db: dict) -> None:
    db["updated_at"] = now_iso()
    path.write_text(json.dumps(db, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_scrape_songs(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["songs"] if isinstance(data, dict) and "songs" in data else data


def db_songs_as_list(db: dict) -> list[dict]:
    return list(db.get("songs", {}).values())


def db_urls(db: dict) -> set[str]:
    return set(db.get("songs", {}))


def merge_song(db: dict, song: dict, source_url: Optional[str] = None, scraped_at: Optional[str] = None) -> None:
    url = song.get("url")
    if not url:
        return

    timestamp = scraped_at or now_iso()
    existing = db["songs"].get(url)
    if not existing:
        existing = {
            "url": url,
            "first_seen_at": timestamp,
            "sources": [],
            "scrape_count": 0,
            "errors": [],
        }

    for field in [
        "artist",
        "title",
        "explore_title",
        "explore_rank",
        "chorus_chords",
        "has_chorus",
    ]:
        if field in song:
            existing[field] = song[field]

    if source_url and source_url not in existing["sources"]:
        existing["sources"].append(source_url)
    existing["last_seen_at"] = timestamp
    existing["last_scraped_at"] = timestamp
    existing["scrape_count"] = existing.get("scrape_count", 0) + 1
    db["songs"][url] = existing


def mark_seen(db: dict, song: dict, source_url: str) -> None:
    url = song.get("url")
    if not url or url not in db["songs"]:
        return
    timestamp = now_iso()
    existing = db["songs"][url]
    if source_url not in existing["sources"]:
        existing["sources"].append(source_url)
    existing["last_seen_at"] = timestamp
    if "explore_rank" in song:
        existing["explore_rank"] = song["explore_rank"]


def record_failure(db: dict, song: dict, source_url: str, error: str) -> None:
    url = song.get("url")
    if not url:
        return
    timestamp = now_iso()
    existing = db["songs"].setdefault(
        url,
        {
            "url": url,
            "first_seen_at": timestamp,
            "sources": [],
            "scrape_count": 0,
            "errors": [],
        },
    )
    if source_url not in existing["sources"]:
        existing["sources"].append(source_url)
    existing["last_seen_at"] = timestamp
    existing["errors"].append({"at": timestamp, "source": source_url, "error": error})
