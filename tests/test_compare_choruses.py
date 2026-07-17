import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from compare_choruses import (
    best_transpose,
    build_output,
    canonical_song_key,
    intervals,
    jaccard_score,
    load_songs,
    normalize_quality,
    normalize_song,
    path_score,
    reduce_repeated_loop,
    reverse_pair,
    sequence_score,
    transpose_to_target,
)


class CompareChorusesTest(unittest.TestCase):
    def test_load_songs_filters_deduplicates_and_sorts(self) -> None:
        songs = {
            "old": {
                "artist": " Artist ",
                "title": "Song",
                "url": "old",
                "explore_rank": 4,
                "chorus_chords": ["C"],
                "sources": ["wanted"],
            },
            "best": {
                "artist": "artist",
                "title": " song ",
                "url": "best",
                "explore_rank": 1,
                "chorus_chords": ["C"],
                "sources": ["wanted"],
            },
            "other": {
                "artist": "Other",
                "title": "Song",
                "url": "other",
                "explore_rank": 2,
                "chorus_chords": ["G"],
                "sources": ["elsewhere"],
            },
            "empty": {"url": "empty", "chorus_chords": [], "sources": ["wanted"]},
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "songs.json"
            path.write_text(json.dumps({"songs": songs}), encoding="utf-8")
            loaded = load_songs(path, "wanted")

        self.assertEqual([song["url"] for song in loaded], ["best"])
        self.assertEqual(canonical_song_key(loaded[0]), "artist::song")

    def test_normalize_quality_groups_common_variants(self) -> None:
        cases = {
            "": "maj",
            "maj7": "maj7",
            "m7": "min",
            "sus4": "sus",
            "dim7": "dim",
            "aug": "aug",
            "7": "dom",
            "add9": "add9",
        }
        for quality, expected in cases.items():
            with self.subTest(quality=quality):
                self.assertEqual(normalize_quality(quality), expected)

    def test_reduce_repeated_loop_handles_repeated_and_unique_progressions(self) -> None:
        self.assertEqual(reduce_repeated_loop(["C", "G", "C", "G"]), ["C", "G"])
        self.assertEqual(reduce_repeated_loop(["C", "G", "Am"]), ["C", "G", "Am"])

    def test_normalize_song_builds_pitch_quality_and_interval_sequences(self) -> None:
        normalized = normalize_song({"chorus_chords": ["C", "G7", "C", "G7"]})
        self.assertEqual(normalized["normalized_chords"], ["C", "G7"])
        self.assertEqual(normalized["pitch_sequence"], [0, 7])
        self.assertEqual(normalized["quality_sequence"], ["maj", "dom"])
        self.assertEqual(normalized["interval_sequence"], [7])
        self.assertEqual(intervals([0]), [])

    def test_sequence_and_jaccard_scores_handle_empty_and_matching_values(self) -> None:
        self.assertEqual(sequence_score([], [1]), 0.0)
        self.assertEqual(sequence_score([1, 2], [1, 2]), 1.0)
        self.assertEqual(jaccard_score([], [1]), 0.0)
        self.assertEqual(jaccard_score(["C", "G"], ["G", "D"]), 1 / 3)

    def test_transposition_uses_shortest_shift_and_handles_empty_sequences(self) -> None:
        self.assertEqual(transpose_to_target([], 0), 0)
        self.assertEqual(transpose_to_target([7], 0), 5)
        self.assertEqual(transpose_to_target([0], 7), -5)
        self.assertEqual(best_transpose([], [0]), 0)
        self.assertEqual(best_transpose([0, 7], [2, 9]), -2)

    def test_reverse_pair_swaps_references_and_inverts_transposition(self) -> None:
        pair = {
            "left": {"title": "A"},
            "right": {"title": "B"},
            "score": 0.5,
            "transpose_right_by": 3,
        }
        reversed_pair = reverse_pair(pair)
        self.assertEqual(reversed_pair["left"], {"title": "B"})
        self.assertEqual(reversed_pair["right"], {"title": "A"})
        self.assertEqual(reversed_pair["transpose_right_by"], -3)

    def test_path_score_handles_empty_and_missing_pairs(self) -> None:
        self.assertEqual(path_score(["one"], {}), 0.0)
        self.assertEqual(path_score(["one", "two"], {}), 0.0)

    def test_build_output_scores_pairs_and_builds_medley(self) -> None:
        songs = [
            {
                "artist": "One",
                "title": "First",
                "url": "one",
                "explore_rank": 1,
                "chorus_chords": ["C", "G", "Am", "F"],
            },
            {
                "artist": "Two",
                "title": "Second",
                "url": "two",
                "explore_rank": 2,
                "chorus_chords": ["D", "A", "Bm", "G"],
                "chorus_lines": [{"lyrics": "line"}],
            },
        ]

        output = build_output(songs, top=1, target_root="C")

        self.assertEqual(output["song_count"], 2)
        self.assertEqual(len(output["top_pairs"]), 1)
        self.assertEqual(output["top_pairs"][0]["transpose_right_by"], -2)
        self.assertEqual(output["medley"]["target_root"], "C")
        self.assertEqual(len(output["medley"]["songs"]), 2)
        self.assertEqual(len(output["medley"]["transitions"]), 1)
        self.assertIn("chorus_lines", output["medley"]["songs"][0] | output["medley"]["songs"][1])

    def test_build_output_handles_empty_song_list(self) -> None:
        output = build_output([], top=5, target_root="C")
        self.assertEqual(output["top_pairs"], [])
        self.assertEqual(output["medley"]["songs"], [])


if __name__ == "__main__":
    unittest.main()
