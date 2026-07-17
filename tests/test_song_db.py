import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from song_db import (
    db_songs_as_list,
    db_urls,
    empty_db,
    load_db,
    load_scrape_songs,
    mark_seen,
    merge_song,
    record_failure,
    save_db,
    save_medley,
)


class SongDbTest(unittest.TestCase):
    def test_load_missing_database_returns_empty_schema(self) -> None:
        with TemporaryDirectory() as directory, patch("song_db.now_iso", return_value="created"):
            self.assertEqual(
                load_db(Path(directory) / "missing.json"),
                {"version": 1, "updated_at": "created", "songs": {}, "medleys": {}},
            )

    def test_load_database_migrates_song_list_and_adds_defaults(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "songs.json"
            path.write_text(
                json.dumps({"songs": [{"url": "one"}, {"title": "missing URL"}]}),
                encoding="utf-8",
            )

            db = load_db(path)

            self.assertEqual(db["songs"], {"one": {"url": "one"}})
            self.assertEqual(db["version"], 1)
            self.assertEqual(db["medleys"], {})

    def test_save_database_updates_timestamp_and_round_trips(self) -> None:
        with TemporaryDirectory() as directory, patch("song_db.now_iso", return_value="saved"):
            path = Path(directory) / "songs.json"
            db = empty_db()
            save_db(path, db)

            self.assertEqual(load_db(path)["updated_at"], "saved")
            self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))

    def test_load_scrape_songs_accepts_wrapped_and_bare_lists(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "songs.json"
            songs = [{"url": "one"}]
            path.write_text(json.dumps({"songs": songs}), encoding="utf-8")
            self.assertEqual(load_scrape_songs(path), songs)
            path.write_text(json.dumps(songs), encoding="utf-8")
            self.assertEqual(load_scrape_songs(path), songs)

    def test_save_medley_preserves_creation_time_on_update(self) -> None:
        db = empty_db()
        with patch("song_db.now_iso", side_effect=["created", "updated"]):
            save_medley(db, "party", "First", ["one"])
            save_medley(db, "party", "Second", ["two"])

        self.assertEqual(
            db["medleys"]["party"],
            {
                "name": "Second",
                "urls": ["two"],
                "created_at": "created",
                "updated_at": "updated",
            },
        )

    def test_merge_song_creates_then_updates_record(self) -> None:
        db = empty_db()
        merge_song(
            db,
            {"url": "one", "artist": "Artist", "title": "Old", "ignored": "value"},
            "source-a",
            "first",
        )
        merge_song(db, {"url": "one", "title": "New"}, "source-b", "second")

        song = db["songs"]["one"]
        self.assertEqual(song["artist"], "Artist")
        self.assertEqual(song["title"], "New")
        self.assertNotIn("ignored", song)
        self.assertEqual(song["sources"], ["source-a", "source-b"])
        self.assertEqual(song["scrape_count"], 2)
        self.assertEqual(song["first_seen_at"], "first")
        self.assertEqual(song["last_seen_at"], "second")
        self.assertEqual(db_urls(db), {"one"})
        self.assertEqual(db_songs_as_list(db), [song])

    def test_merge_and_mark_seen_ignore_missing_urls(self) -> None:
        db = empty_db()
        merge_song(db, {"title": "No URL"})
        mark_seen(db, {"url": "unknown"}, "source")
        self.assertEqual(db["songs"], {})

    def test_mark_seen_adds_source_and_rank(self) -> None:
        db = empty_db()
        merge_song(db, {"url": "one"}, scraped_at="first")
        with patch("song_db.now_iso", return_value="seen"):
            mark_seen(db, {"url": "one", "explore_rank": 3}, "source")

        self.assertEqual(db["songs"]["one"]["sources"], ["source"])
        self.assertEqual(db["songs"]["one"]["last_seen_at"], "seen")
        self.assertEqual(db["songs"]["one"]["explore_rank"], 3)

    def test_record_failure_creates_and_appends_error(self) -> None:
        db = empty_db()
        with patch("song_db.now_iso", return_value="failed"):
            record_failure(db, {"url": "one"}, "source", "timeout")
            record_failure(db, {"url": "one"}, "source", "blocked")
            record_failure(db, {}, "source", "ignored")

        song = db["songs"]["one"]
        self.assertEqual(song["sources"], ["source"])
        self.assertEqual(
            song["errors"],
            [
                {"at": "failed", "source": "source", "error": "timeout"},
                {"at": "failed", "source": "source", "error": "blocked"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
