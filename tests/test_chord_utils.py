import unittest

from chord_utils import transpose_chords


class TransposeChordsTest(unittest.TestCase):
    def test_transpose_chords_prefers_flats_from_context(self):
        self.assertEqual(transpose_chords(["F", "A#", "C"], 0), ["F", "Bb", "C"])

    def test_transpose_chords_prefers_sharps_from_context(self):
        self.assertEqual(transpose_chords(["B", "F#", "A#", "E"], 0), ["B", "F#", "A#", "E"])

    def test_transpose_chords_normalizes_slash_bass_to_context(self):
        self.assertEqual(transpose_chords(["Db", "Gb7/A#"], 0), ["Db", "Gb7/Bb"])


if __name__ == "__main__":
    unittest.main()
