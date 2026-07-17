import unittest

from chord_utils import (
    infer_prefer_flats,
    parse_chord,
    split_bass,
    transpose_chord,
    transpose_chords,
)


class TransposeChordsTest(unittest.TestCase):
    def test_parse_chord_returns_components(self) -> None:
        self.assertEqual(
            parse_chord("F#m7/C#"),
            {
                "chord": "F#m7/C#",
                "root": "F#",
                "pitch": 6,
                "quality": "m7",
                "bass": "C#",
            },
        )

    def test_parse_chord_preserves_unrecognized_chord(self) -> None:
        self.assertEqual(
            parse_chord("N.C."),
            {"chord": "N.C.", "root": None, "pitch": None, "quality": "", "bass": None},
        )

    def test_split_bass_only_splits_first_separator(self) -> None:
        self.assertEqual(split_bass("C/G/B"), ("C", "G/B"))

    def test_transpose_chord_preserves_quality_and_bass(self) -> None:
        self.assertEqual(transpose_chord("Cmaj7/G", 2), "Dmaj7/A")

    def test_transpose_chord_preserves_invalid_bass(self) -> None:
        self.assertEqual(transpose_chord("C/H", 2), "D/H")

    def test_transpose_chord_preserves_unrecognized_chord(self) -> None:
        self.assertEqual(transpose_chord("N.C.", 5), "N.C.")

    def test_infer_prefer_flats_defaults_to_sharps_without_pitches(self) -> None:
        self.assertFalse(infer_prefer_flats(["N.C."]))

    def test_transpose_chords_prefers_flats_from_context(self) -> None:
        self.assertEqual(transpose_chords(["F", "A#", "C"], 0), ["F", "Bb", "C"])

    def test_transpose_chords_prefers_sharps_from_context(self) -> None:
        self.assertEqual(transpose_chords(["B", "F#", "A#", "E"], 0), ["B", "F#", "A#", "E"])

    def test_transpose_chords_normalizes_slash_bass_to_context(self) -> None:
        self.assertEqual(transpose_chords(["Db", "Gb7/A#"], 0), ["Db", "Gb7/Bb"])


if __name__ == "__main__":
    unittest.main()
