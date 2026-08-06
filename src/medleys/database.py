#!/usr/bin/env python3
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict, cast

SongRecord = dict[str, Any]


class MedleyRecord(TypedDict):
    name: str
    urls: list[str]
    created_at: str
    updated_at: str


class SongDatabase(TypedDict):
    version: int
    updated_at: str
    songs: dict[str, SongRecord]
    medleys: dict[str, MedleyRecord]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_db() -> SongDatabase:
    return {
        "version": 1,
        "updated_at": now_iso(),
        "songs": {},
        "medleys": {},
    }


def load_db(path: Path) -> SongDatabase:
    if not path.exists():
        return empty_db()
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data.get("songs"), list):
        data["songs"] = {song["url"]: song for song in data["songs"] if song.get("url")}
    data.setdefault("version", 1)
    data.setdefault("songs", {})
    data.setdefault("medleys", {})
    return cast(SongDatabase, data)


def save_medley(db: SongDatabase, source_id: str, name: str, urls: list[str]) -> None:
    existing = db["medleys"].get(source_id)
    timestamp = now_iso()
    db["medleys"][source_id] = {
        "name": name,
        "urls": urls,
        "created_at": existing["created_at"] if existing else timestamp,
        "updated_at": timestamp,
    }


def save_db(path: Path, db: SongDatabase) -> None:
    db["updated_at"] = now_iso()
    path.write_text(json.dumps(db, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_scrape_songs(path: Path) -> list[SongRecord]:
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    songs = data["songs"] if isinstance(data, dict) and "songs" in data else data
    return cast(list[SongRecord], songs)


def db_songs_as_list(db: SongDatabase) -> list[SongRecord]:
    return list(db["songs"].values())


def db_urls(db: SongDatabase) -> set[str]:
    return set(db["songs"])


def merge_song(
    db: SongDatabase,
    song: Mapping[str, Any],
    source_url: str | None = None,
    scraped_at: str | None = None,
) -> None:
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
        "chorus_lines",
        "has_chorus",
        "chorus_detection",
        "chorus_confidence",
        "favorites_count",
        "view_total",
    ]:
        if field in song:
            existing[field] = song[field]

    if source_url and source_url not in existing["sources"]:
        existing["sources"].append(source_url)
    existing["last_seen_at"] = timestamp
    existing["last_scraped_at"] = timestamp
    existing["scrape_count"] = existing.get("scrape_count", 0) + 1
    db["songs"][url] = existing


def mark_seen(db: SongDatabase, song: Mapping[str, Any], source_url: str) -> None:
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


def record_failure(db: SongDatabase, song: Mapping[str, Any], source_url: str, error: str) -> None:
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
