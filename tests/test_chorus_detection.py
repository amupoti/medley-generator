from __future__ import annotations

import re
import unittest

import medleys.ultimate_guitar.chorus_detection as cd


def make_units(
    texts: list[str],
    section_names: list[str | None] | None = None,
    block_indices: list[int] | None = None,
    heading_adjacent: list[bool] | None = None,
) -> list[cd.LyricUnit]:
    section_names = section_names or [None] * len(texts)
    block_indices = block_indices or [0] * len(texts)
    heading_adjacent = heading_adjacent or [True] * len(texts)
    return [
        cd.LyricUnit(
            normalized=text,
            chorus_line={"lyrics": text, "chords": []},
            block_index=block_indices[index],
            source_line=index,
            section_name=section_names[index],
            heading_adjacent=heading_adjacent[index],
        )
        for index, text in enumerate(texts)
    ]


def make_candidate(
    *,
    length: int = 2,
    occurrence_starts: list[int],
    lyric_similarity: float = 1.0,
    chord_supported: bool = False,
    normalized_lines: tuple[str, ...] = ("hold on tight", "we are almost there"),
    section_names: list[str | None] | None = None,
    source_start: int = 5,
    spans_whole_block: bool = True,
    heading_adjacent: bool = True,
) -> cd.ChorusCandidate:
    return cd.ChorusCandidate(
        start=occurrence_starts[0],
        length=length,
        occurrence_starts=occurrence_starts,
        lyric_similarity=lyric_similarity,
        chord_supported=chord_supported,
        chords=["C", "G"] if chord_supported else [],
        lines=[{"lyrics": line, "chords": []} for line in normalized_lines],
        normalized_lines=normalized_lines,
        source_start=source_start,
        source_end=source_start + length,
        section_names=section_names or [None] * len(occurrence_starts),
        spans_whole_block=spans_whole_block,
        heading_adjacent=heading_adjacent,
    )


class LowLevelParsingTest(unittest.TestCase):
    def test_repeated_chorus_heading_stops_first_chorus(self) -> None:
        self.assertEqual(cd.first_chorus_source_lines("[Chorus]\nC\n[Chorus 2]\nG"), ["C"])

    def test_non_chorus_heading_after_chorus_stops_it_too(self) -> None:
        self.assertEqual(cd.first_chorus_source_lines("[Chorus]\nC\n[Verse]\nG"), ["C"])

    def test_unbracketed_colon_heading_captures_until_blank_line(self) -> None:
        content = "Coro:\nSi algún día nos cruzamos\nNo respondas\n\nOtra estrofa\n"
        self.assertEqual(
            cd.first_chorus_source_lines(content),
            ["Si algún día nos cruzamos", "No respondas"],
        )

    def test_unbracketed_parenthesized_heading_captures_until_blank_line(self) -> None:
        content = "(Coro)\nSiempre serás\nbienvenido\n\n(Versos)\nmore text\n"
        self.assertEqual(
            cd.first_chorus_source_lines(content), ["Siempre serás", "bienvenido"]
        )

    def test_unbracketed_heading_also_stops_at_a_later_bracket_heading(self) -> None:
        content = "Coro:\nSi algún día nos cruzamos\n[Verse]\nmore text\n"
        self.assertEqual(cd.first_chorus_source_lines(content), ["Si algún día nos cruzamos"])

    def test_parse_marked_line_skips_empty_chord_marker(self) -> None:
        self.assertEqual(cd.parse_chorus_line("A[ch] [/ch]B"), {"lyrics": "AB", "chords": []})

    def test_is_intro_outro_only_with_no_lyric_units_is_false(self) -> None:
        candidate = make_candidate(occurrence_starts=[0])
        self.assertFalse(cd.is_intro_outro_only(candidate, total_lyric_units=0))


class ExplicitHeadingRecognitionTest(unittest.TestCase):
    def test_recognizes_chorus_and_numbered_variants(self) -> None:
        self.assertTrue(cd.is_chorus_section("Chorus"))
        self.assertTrue(cd.is_chorus_section("chorus"))
        self.assertTrue(cd.is_chorus_section("CHORUS"))
        self.assertTrue(cd.is_chorus_section("  Chorus  "))
        self.assertTrue(cd.is_chorus_section("Chorus 1"))
        self.assertTrue(cd.is_chorus_section("Chorus 2"))
        self.assertTrue(cd.is_chorus_section("chorus   12"))

    def test_recognizes_refrain_hook_and_estribillo(self) -> None:
        self.assertTrue(cd.is_chorus_section("Refrain"))
        self.assertTrue(cd.is_chorus_section("REFRAIN"))
        self.assertTrue(cd.is_chorus_section("Hook"))
        self.assertTrue(cd.is_chorus_section(" hook "))
        self.assertTrue(cd.is_chorus_section("Estribillo"))
        self.assertTrue(cd.is_chorus_section("estribillo"))

    def test_recognizes_coro_and_numbered_variant(self) -> None:
        self.assertTrue(cd.is_chorus_section("Coro"))
        self.assertTrue(cd.is_chorus_section("CORO"))
        self.assertTrue(cd.is_chorus_section("Coro 2"))

    def test_does_not_match_pre_chorus_or_unrelated_sections(self) -> None:
        self.assertFalse(cd.is_chorus_section("Pre-Chorus"))
        self.assertFalse(cd.is_chorus_section("Pre Chorus"))
        self.assertFalse(cd.is_chorus_section("PRE-CHORUS"))
        self.assertFalse(cd.is_chorus_section("Verse"))
        self.assertFalse(cd.is_chorus_section("Verse 1"))
        self.assertFalse(cd.is_chorus_section("Bridge"))
        self.assertFalse(cd.is_chorus_section("Intro"))
        self.assertFalse(cd.is_chorus_section("Outro"))


class UnbracketedHeadingRecognitionTest(unittest.TestCase):
    def test_recognizes_parenthesized_and_colon_suffixed_chorus_headings(self) -> None:
        self.assertEqual(cd.chorus_heading_name("(Coro)"), "Coro")
        self.assertEqual(cd.chorus_heading_name("Coro:"), "Coro")
        self.assertEqual(cd.chorus_heading_name("  Coro:  "), "Coro")

    def test_recognizes_bare_chorus_heading_line(self) -> None:
        self.assertEqual(cd.chorus_heading_name("CORO"), "CORO")
        self.assertEqual(cd.chorus_heading_name("Estribillo"), "Estribillo")

    def test_rejects_blank_and_unrelated_lines(self) -> None:
        self.assertIsNone(cd.chorus_heading_name(""))
        self.assertIsNone(cd.chorus_heading_name("   "))
        self.assertIsNone(cd.chorus_heading_name("She said:"))
        self.assertIsNone(cd.chorus_heading_name("(repeat 2x)"))
        self.assertIsNone(cd.chorus_heading_name("Just an ordinary lyric line"))

    def test_rejects_pre_chorus_and_verse(self) -> None:
        self.assertIsNone(cd.chorus_heading_name("(Pre-Coro)"))
        self.assertIsNone(cd.chorus_heading_name("Verso:"))
        self.assertIsNone(cd.chorus_heading_name("VERSO"))


class NormalizationTest(unittest.TestCase):
    def test_lowercases_and_strips_punctuation(self) -> None:
        self.assertEqual(cd.normalize_lyric_line("Don't Stop!!"), "dont stop")

    def test_removes_tab_and_chord_markup(self) -> None:
        self.assertEqual(cd.normalize_lyric_line("[tab]Hello[/tab]"), "hello")
        self.assertEqual(cd.normalize_lyric_line("[ch]C[/ch]Hello"), "hello")

    def test_removes_repetition_annotations(self) -> None:
        self.assertEqual(cd.normalize_lyric_line("We sing (x2)"), "we sing")
        self.assertEqual(cd.normalize_lyric_line("la la (repeat)"), "la la")
        self.assertEqual(cd.normalize_lyric_line("hey x3"), "hey")

    def test_collapses_whitespace(self) -> None:
        self.assertEqual(cd.normalize_lyric_line("hello    world  "), "hello world")

    def test_preserves_words_and_numbers(self) -> None:
        self.assertEqual(cd.normalize_lyric_line("uno dos tres 123"), "uno dos tres 123")

    def test_empty_and_blank_lines_normalize_to_empty(self) -> None:
        self.assertEqual(cd.normalize_lyric_line(""), "")
        self.assertEqual(cd.normalize_lyric_line("   "), "")
        self.assertEqual(cd.normalize_lyric_line("..."), "")

    def test_normalize_chord_symbol_fixes_case_and_whitespace(self) -> None:
        self.assertEqual(cd.normalize_chord_symbol("am"), "Am")
        self.assertEqual(cd.normalize_chord_symbol("  C  "), "C")
        self.assertEqual(cd.normalize_chord_symbol("g#m7"), "G#m7")

    def test_normalize_chord_symbol_preserves_quality_and_bass(self) -> None:
        self.assertEqual(cd.normalize_chord_symbol("Dsus4"), "Dsus4")
        self.assertEqual(cd.normalize_chord_symbol("c/e"), "C/e")

    def test_normalize_chord_symbol_leaves_non_chord_text_alone(self) -> None:
        self.assertEqual(cd.normalize_chord_symbol("hello"), "hello")
        self.assertEqual(cd.normalize_chord_symbol(""), "")

    def test_normalize_chord_progression_maps_each_symbol(self) -> None:
        self.assertEqual(cd.normalize_chord_progression(["am", " C ", "G7"]), ["Am", "C", "G7"])

    def test_does_not_transpose(self) -> None:
        # Normalization must never change the pitch/root - only formatting.
        self.assertEqual(cd.normalize_chord_symbol("D"), "D")
        self.assertNotEqual(cd.normalize_chord_symbol("D"), "E")


class SongBlockParsingTest(unittest.TestCase):
    def test_splits_on_headings_and_blank_lines_and_preserves_chord_positions(self) -> None:
        content = "[Verse 1]\nHello world\nC G\n\n[Chorus]\n[ch]Am[/ch]Stay with me\nsing along\n"
        blocks = cd.parse_song_blocks(content)

        self.assertEqual(len(blocks), 2)
        verse, chorus = blocks
        self.assertEqual(verse["section_name"], "Verse 1")
        self.assertEqual(verse["start"], 1)
        self.assertEqual(verse["end"], 3)
        self.assertEqual(verse["chords"], ["C", "G"])
        self.assertEqual(verse["normalized_lyrics"], ["hello world"])

        self.assertEqual(chorus["section_name"], "Chorus")
        self.assertEqual(chorus["chords"], ["Am"])
        self.assertEqual(
            chorus["chorus_lines"][0],
            {"lyrics": "Stay with me", "chords": [{"symbol": "Am", "position": 0}]},
        )
        self.assertEqual(chorus["normalized_lyrics"], ["stay with me", "sing along"])

    def test_ignores_blocks_with_no_usable_lyrics_or_chords(self) -> None:
        content = "[Intro]\n...\n\n[Verse]\nreal lyrics here\n"
        blocks = cd.parse_song_blocks(content)

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["section_name"], "Verse")

    def test_chord_only_block_is_kept(self) -> None:
        content = "[Instrumental]\nC G Am F\n"
        blocks = cd.parse_song_blocks(content)

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["chords"], ["C", "G", "Am", "F"])
        self.assertEqual(blocks[0]["normalized_lyrics"], [])

    def test_section_name_persists_across_blank_line_separated_stanzas(self) -> None:
        content = "[Verse]\nfirst stanza line\n\nsecond stanza line\n"
        blocks = cd.parse_song_blocks(content)

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["section_name"], "Verse")
        self.assertEqual(blocks[1]["section_name"], "Verse")

    def test_only_the_first_block_under_a_heading_is_heading_adjacent(self) -> None:
        content = "[Verse]\nfirst stanza line\n\nsecond stanza line\n"
        blocks = cd.parse_song_blocks(content)

        self.assertTrue(blocks[0]["heading_adjacent"])
        self.assertFalse(blocks[1]["heading_adjacent"])

    def test_no_heading_song_has_none_section_name(self) -> None:
        content = "just some lyrics\nwith no headings at all\n"
        blocks = cd.parse_song_blocks(content)

        self.assertEqual(len(blocks), 1)
        self.assertIsNone(blocks[0]["section_name"])

    def test_unbracketed_chorus_heading_is_a_block_boundary_not_content(self) -> None:
        content = "Verse text here\n\nCoro:\nSi algún día nos cruzamos\nNo respondas\n"
        blocks = cd.parse_song_blocks(content)

        self.assertEqual(len(blocks), 2)
        verse, chorus = blocks
        self.assertIsNone(verse["section_name"])
        self.assertEqual(chorus["section_name"], "Coro")
        self.assertEqual(
            chorus["normalized_lyrics"], ["si algún día nos cruzamos", "no respondas"]
        )
        self.assertNotIn("coro", chorus["normalized_lyrics"])


class LyricUnitAndChordIndexTest(unittest.TestCase):
    CONTENT = "[Verse 1]\nHello world\nC G\n\n[Chorus]\n[ch]Am[/ch]Stay with me\nsing along\n"

    def test_build_lyric_units_skips_chord_only_lines_and_tracks_source_position(self) -> None:
        blocks = cd.parse_song_blocks(self.CONTENT)
        units = cd.build_lyric_units(blocks)

        self.assertEqual(
            [unit.normalized for unit in units], ["hello world", "stay with me", "sing along"]
        )
        self.assertEqual([unit.source_line for unit in units], [1, 5, 6])
        self.assertEqual([unit.section_name for unit in units], ["Verse 1", "Chorus", "Chorus"])

    def test_build_flat_chord_index_maps_chord_only_and_inline_chord_lines(self) -> None:
        index = cd.build_flat_chord_index(self.CONTENT)
        self.assertEqual(index, {2: ["C", "G"], 5: ["Am"]})

    def test_flat_chord_index_skips_headings_and_blank_lines(self) -> None:
        index = cd.build_flat_chord_index("[Chorus]\n\nC G\n")
        self.assertEqual(index, {2: ["C", "G"]})


class CandidateGenerationTest(unittest.TestCase):
    def test_exact_match_finds_non_overlapping_occurrences(self) -> None:
        units = make_units(["a a", "b b", "x", "a a", "b b"])
        candidates = cd.generate_candidates_for_length(units, 2)
        self.assertEqual(candidates, [(0, 2, [0, 3])])

    def test_overlapping_repeats_are_not_double_counted(self) -> None:
        units = make_units(["a", "a", "a", "a"])
        candidates = cd.generate_candidates_for_length(units, 2)
        self.assertEqual(candidates, [(0, 2, [0, 2])])

    def test_window_spans_single_section_rejects_boundary_crossing(self) -> None:
        units = make_units(["a", "a"], section_names=["Verse", "Chorus"])
        self.assertFalse(cd.window_spans_single_section(units))

        same_section = make_units(["a", "a"], section_names=["Verse", "Verse"])
        self.assertTrue(cd.window_spans_single_section(same_section))

        no_heading = make_units(["a", "a"], section_names=[None, None])
        self.assertTrue(cd.window_spans_single_section(no_heading))

    def test_occurrence_matches_whole_block_detects_full_block_repeats(self) -> None:
        # Block 0 is entirely the 2-line occurrence; block 1 has an extra
        # verse-only line before the repeated couplet.
        units = make_units(
            ["a", "b", "verse only line", "a", "b"],
            block_indices=[0, 0, 1, 1, 1],
        )
        self.assertTrue(cd.occurrence_matches_whole_block(units, 0, 2))
        self.assertFalse(cd.occurrence_matches_whole_block(units, 3, 2))

    def test_candidates_do_not_cross_a_real_section_boundary(self) -> None:
        units = make_units(
            ["chorus line one", "chorus line two", "verse line one", "verse line two"],
            section_names=["Chorus", "Verse", "Verse", "Verse"],
        )
        candidates = cd.generate_candidates_for_length(units, 2)
        # window [0,1] mixes "Chorus" and "Verse" -> invalid, must not appear as an anchor.
        self.assertTrue(all(anchor != 0 for anchor, _length, _occ in candidates))

    def test_single_occurrence_is_not_a_candidate(self) -> None:
        units = make_units(["unique one", "unique two", "another one", "another two"])
        candidates = cd.generate_candidates_for_length(units, 2)
        self.assertEqual(candidates, [])

    def test_fuzzy_matching_accepts_one_changed_word(self) -> None:
        units = make_units(
            [
                "i love you baby yeah",
                "stay with me tonight always",
                "verse content that differs completely here now",
                "i love you baby yeah",
                "stay with me forever always",
            ]
        )
        candidates = cd.generate_candidates_for_length(units, 2)
        self.assertIn((0, 2, [0, 3]), candidates)

    def test_prefers_the_better_aligned_occurrence_over_an_earlier_near_match(self) -> None:
        # A near-match decoy sits right after the anchor; the true repeat,
        # a perfect match, sits one line further out. Taking the first
        # window that merely clears the fuzzy threshold would lock onto the
        # decoy and never reach the true, better-aligned repeat.
        units = make_units(
            [
                "hold on tight we are almost there",
                "never letting go of this feeling",
                "some unrelated verse content goes here",
                "hold on tight we are almost somewhere",
                "never letting go of this feeling",
                "hold on tight we are almost there",
                "never letting go of this feeling",
            ]
        )
        candidates = cd.generate_candidates_for_length(units, 2)
        self.assertIn((0, 2, [0, 5]), candidates)
        self.assertNotIn((0, 2, [0, 3]), candidates)

    def test_fuzzy_matching_rejects_unrelated_verses(self) -> None:
        left = ("a completely different opening line here", "with its own unrelated second line")
        right = ("nothing at all like the first one is", "and this second line is unrelated too")
        self.assertLess(cd.window_similarity(left, right), cd.FUZZY_LINE_SIMILARITY_THRESHOLD)


class ChordSupportTest(unittest.TestCase):
    def test_occurrence_chords_includes_chord_line_preceding_first_lyric(self) -> None:
        content = "C G Am F\nhold on tight\nwe are almost there\n"
        blocks = cd.parse_song_blocks(content)
        units = cd.build_lyric_units(blocks)
        flat_chords = cd.build_flat_chord_index(content)

        chords = cd.occurrence_chords(units, flat_chords, 0, 2)
        self.assertEqual(chords, ["C", "G", "Am", "F"])

    def test_occurrence_chords_does_not_absorb_previous_lyric_lines_chords(self) -> None:
        content = "C G\nprevious line\nAm F\nhold on tight\nwe are almost there\n"
        blocks = cd.parse_song_blocks(content)
        units = cd.build_lyric_units(blocks)
        flat_chords = cd.build_flat_chord_index(content)

        # unit 0 = "previous line", unit 1 = "hold on tight", unit 2 = "we are almost there"
        chords = cd.occurrence_chords(units, flat_chords, 1, 2)
        self.assertEqual(chords, ["Am", "F"])

    def test_chords_match_requires_non_empty_and_similar_sequences(self) -> None:
        self.assertFalse(cd.chords_match([], []))
        self.assertFalse(cd.chords_match(["C"], []))
        self.assertTrue(cd.chords_match(["C", "G", "Am", "F"], ["C", "G", "Am", "F"]))
        self.assertTrue(cd.chords_match(["C", "G", "Am", "F"], ["C", "G", "Am", "F", "F"]))
        self.assertFalse(cd.chords_match(["C", "G"], ["Dm", "Bb", "E", "F#"]))


class ScoringSignalsTest(unittest.TestCase):
    def test_exact_repeat_with_chord_support_is_accepted(self) -> None:
        candidate = make_candidate(
            occurrence_starts=[2, 10],
            lyric_similarity=1.0,
            chord_supported=True,
            normalized_lines=("we will rock you all night long", "we will never let you down"),
        )
        score = cd.score_candidate(candidate, total_lyric_units=20, total_source_lines=30)
        self.assertGreaterEqual(score, cd.INFERENCE_CONFIDENCE_THRESHOLD)

    def test_single_occurrence_is_penalized_below_threshold(self) -> None:
        candidate = make_candidate(
            occurrence_starts=[2], lyric_similarity=1.0, chord_supported=True
        )
        score = cd.score_candidate(candidate, total_lyric_units=20, total_source_lines=30)
        self.assertLess(score, cd.INFERENCE_CONFIDENCE_THRESHOLD)

    def test_verse_heading_candidate_is_penalized(self) -> None:
        base = make_candidate(
            occurrence_starts=[2, 10],
            lyric_similarity=1.0,
            chord_supported=True,
            section_names=[None, None],
        )
        verse = make_candidate(
            occurrence_starts=[2, 10],
            lyric_similarity=1.0,
            chord_supported=True,
            section_names=["Verse 1", "Verse 2"],
        )
        base_score = cd.score_candidate(base, total_lyric_units=20, total_source_lines=30)
        verse_score = cd.score_candidate(verse, total_lyric_units=20, total_source_lines=30)
        self.assertLess(verse_score, base_score)

    def test_generic_fragment_is_penalized(self) -> None:
        generic = make_candidate(
            occurrence_starts=[2, 10], normalized_lines=("oh oh", "oh oh"), chord_supported=True
        )
        specific = make_candidate(
            occurrence_starts=[2, 10],
            normalized_lines=("we will rock you", "all night long"),
            chord_supported=True,
        )
        generic_score = cd.score_candidate(generic, total_lyric_units=20, total_source_lines=30)
        specific_score = cd.score_candidate(specific, total_lyric_units=20, total_source_lines=30)
        self.assertLess(generic_score, specific_score)

    def test_generic_fragment_with_no_words_is_treated_as_generic(self) -> None:
        self.assertTrue(
            cd.is_generic_fragment(make_candidate(occurrence_starts=[2, 10], normalized_lines=()))
        )

    def test_short_candidate_is_penalized(self) -> None:
        short = make_candidate(occurrence_starts=[2, 10], length=1, normalized_lines=("oh",))
        normal = make_candidate(occurrence_starts=[2, 10], length=2)
        short_score = cd.score_candidate(short, total_lyric_units=20, total_source_lines=30)
        normal_score = cd.score_candidate(normal, total_lyric_units=20, total_source_lines=30)
        self.assertLess(short_score, normal_score)

    def test_coverage_of_most_of_the_song_is_penalized(self) -> None:
        candidate = make_candidate(occurrence_starts=[0, 4], length=4, chord_supported=True)
        small_song_score = cd.score_candidate(
            candidate, total_lyric_units=10, total_source_lines=15
        )
        large_song_score = cd.score_candidate(
            candidate, total_lyric_units=100, total_source_lines=150
        )
        self.assertLess(small_song_score, large_song_score)

    def test_weak_lyrics_without_chord_support_are_penalized(self) -> None:
        weak = make_candidate(
            occurrence_starts=[2, 10], lyric_similarity=0.5, chord_supported=False
        )
        supported = make_candidate(
            occurrence_starts=[2, 10], lyric_similarity=0.5, chord_supported=True
        )
        weak_score = cd.score_candidate(weak, total_lyric_units=20, total_source_lines=30)
        supported_score = cd.score_candidate(supported, total_lyric_units=20, total_source_lines=30)
        self.assertLess(weak_score, supported_score)

    def test_score_is_normalized_between_zero_and_one(self) -> None:
        candidate = make_candidate(occurrence_starts=[2, 6, 10], chord_supported=True)
        score = cd.score_candidate(candidate, total_lyric_units=20, total_source_lines=30)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)


class TieBreakTest(unittest.TestCase):
    def test_prefers_stronger_lyric_repetition(self) -> None:
        strong = make_candidate(occurrence_starts=[2, 10], lyric_similarity=0.95)
        weak = make_candidate(occurrence_starts=[2, 10], lyric_similarity=0.85)
        self.assertGreater(cd.candidate_sort_key(0.7, strong), cd.candidate_sort_key(0.7, weak))

    def test_then_prefers_chord_supported_match(self) -> None:
        supported = make_candidate(
            occurrence_starts=[2, 10], lyric_similarity=0.9, chord_supported=True
        )
        unsupported = make_candidate(
            occurrence_starts=[2, 10], lyric_similarity=0.9, chord_supported=False
        )
        self.assertGreater(
            cd.candidate_sort_key(0.7, supported), cd.candidate_sort_key(0.7, unsupported)
        )

    def test_then_prefers_more_occurrences(self) -> None:
        more = make_candidate(
            occurrence_starts=[2, 6, 10], lyric_similarity=0.9, chord_supported=True
        )
        fewer = make_candidate(
            occurrence_starts=[2, 10], lyric_similarity=0.9, chord_supported=True
        )
        self.assertGreater(cd.candidate_sort_key(0.7, more), cd.candidate_sort_key(0.7, fewer))

    def test_then_prefers_longer_candidate(self) -> None:
        # A longer verified repeat captures more of the real chorus than a
        # short subset of it, so it should win a same-score tie.
        shorter = make_candidate(occurrence_starts=[2, 10], length=2, lyric_similarity=0.9)
        longer = make_candidate(occurrence_starts=[2, 10], length=4, lyric_similarity=0.9)
        self.assertGreater(cd.candidate_sort_key(0.7, longer), cd.candidate_sort_key(0.7, shorter))

    def test_source_position_is_final_deterministic_tie_breaker(self) -> None:
        earlier = make_candidate(occurrence_starts=[2, 10], source_start=3)
        later = make_candidate(occurrence_starts=[2, 10], source_start=8)
        self.assertGreater(cd.candidate_sort_key(0.7, earlier), cd.candidate_sort_key(0.7, later))


class DominanceFilterTest(unittest.TestCase):
    def test_shorter_is_subsumed_by_longer_containing_superset(self) -> None:
        shorter = make_candidate(occurrence_starts=[11, 20], length=2, lyric_similarity=1.0)
        longer = make_candidate(occurrence_starts=[9, 18], length=4, lyric_similarity=1.0)
        self.assertTrue(cd.is_subsumed_by(shorter, longer))
        self.assertFalse(cd.is_subsumed_by(longer, shorter))

    def test_not_subsumed_when_occurrences_are_not_contained(self) -> None:
        shorter = make_candidate(occurrence_starts=[11, 30], length=2, lyric_similarity=1.0)
        longer = make_candidate(occurrence_starts=[9, 18], length=4, lyric_similarity=1.0)
        self.assertFalse(cd.is_subsumed_by(shorter, longer))

    def test_not_subsumed_when_longer_similarity_drops_too_much(self) -> None:
        # The longer window only clears the loose fuzzy bar by including an
        # unrelated extra line - too big a similarity drop to trust over
        # the tighter, better-matching shorter candidate.
        shorter = make_candidate(occurrence_starts=[11, 20], length=2, lyric_similarity=1.0)
        longer = make_candidate(occurrence_starts=[9, 18], length=5, lyric_similarity=0.85)
        self.assertFalse(cd.is_subsumed_by(shorter, longer))

    def test_not_subsumed_by_equal_or_shorter_length(self) -> None:
        one = make_candidate(occurrence_starts=[9, 18], length=4, lyric_similarity=1.0)
        other = make_candidate(occurrence_starts=[9, 18], length=4, lyric_similarity=1.0)
        self.assertFalse(cd.is_subsumed_by(one, other))

    def test_not_subsumed_when_longer_has_fewer_occurrences(self) -> None:
        shorter = make_candidate(occurrence_starts=[11, 20, 30], length=2, lyric_similarity=1.0)
        longer = make_candidate(occurrence_starts=[9, 18], length=4, lyric_similarity=1.0)
        self.assertFalse(cd.is_subsumed_by(shorter, longer))


def strip_heading(content: str, heading: str) -> str:
    return re.sub(rf"^\[{re.escape(heading)}\]\n", "", content, count=1, flags=re.MULTILINE)


class DetectChorusIntegrationTest(unittest.TestCase):
    def test_explicit_chorus_takes_precedence_over_inference(self) -> None:
        content = (
            "[Verse 1]\n"
            "some verse line\n"
            "[Chorus]\n"
            "this is the labelled chorus\n"
            "[Verse 2]\n"
            "another verse line\n"
        )
        detection = cd.detect_chorus(content)
        self.assertEqual(detection["method"], "explicit")
        self.assertEqual(detection["confidence"], 1.0)
        self.assertEqual(
            detection["lines"], [{"lyrics": "this is the labelled chorus", "chords": []}]
        )

    def test_unbracketed_coro_heading_is_detected_explicitly(self) -> None:
        # Spanish UG tabs commonly use a bare "Coro:" heading instead of the
        # "[Chorus]" bracket convention.
        content = (
            "Verso 1\nuna línea de verso aquí\n\nCoro:\nSi algún día nos cruzamos\n"
            "No respondas, ni hagas caso\n\nOtra estrofa distinta\n"
        )
        detection = cd.detect_chorus(content)
        self.assertEqual(detection["method"], "explicit")
        self.assertEqual(detection["confidence"], 1.0)
        self.assertEqual(
            detection["lines"],
            [
                {"lyrics": "Si algún día nos cruzamos", "chords": []},
                {"lyrics": "No respondas, ni hagas caso", "chords": []},
            ],
        )

    def test_ambiguous_song_with_no_repeats_returns_none(self) -> None:
        content = (
            "[Verse 1]\n"
            "every single line in this song is different\n"
            "[Verse 2]\n"
            "nothing here repeats at all in any way\n"
            "[Verse 3]\n"
            "still no repetition anywhere to be found\n"
        )
        detection = cd.detect_chorus(content)
        self.assertEqual(detection["method"], "none")
        self.assertEqual(detection["confidence"], 0.0)
        self.assertEqual(detection["lines"], [])
        self.assertEqual(detection["chords"], [])

    def test_full_pipeline_falls_back_to_inference_when_no_heading_present(self) -> None:
        content = (
            "Woke up this morning feeling kind of low\n"
            "Put on my shoes and out the door I go\n"
            "We will rock you all night long\n"
            "We will never let you down\n"
            "Walked to the station caught the earliest train\n"
            "Thought about you and I smiled again\n"
            "We will rock you all night long\n"
            "We will never let you down\n"
        )
        detection = cd.detect_chorus(content)
        self.assertEqual(detection["method"], "inferred")
        self.assertGreaterEqual(detection["confidence"], cd.INFERENCE_CONFIDENCE_THRESHOLD)
        self.assertEqual(
            detection["lines"],
            [
                {"lyrics": "We will rock you all night long", "chords": []},
                {"lyrics": "We will never let you down", "chords": []},
            ],
        )


class LabelledChorusEvaluationTest(unittest.TestCase):
    """Milestone 7: strip a labelled chorus heading and check inference recovers it."""

    def evaluate(
        self, content: str, heading: str = "Chorus"
    ) -> tuple[cd.ChorusDetection, cd.ChorusDetection]:
        expected = cd.detect_explicit_chorus(content)
        assert expected is not None, "test fixture must contain an explicit chorus"
        stripped = strip_heading(content, heading)
        inferred = cd.detect_inferred_chorus(stripped)
        return expected, inferred  # type: ignore[return-value]

    def test_conventional_verse_chorus_pop_song(self) -> None:
        content = (
            "[Verse 1]\n"
            "Woke up this morning feeling kind of low\n"
            "Put on my shoes and out the door I go\n"
            "[Chorus]\n"
            "C       G       Am      F\n"
            "We will rock you all night long\n"
            "We will never let you down\n"
            "[Verse 2]\n"
            "Walked to the station caught the earliest train\n"
            "Thought about you and I smiled again\n"
            "[Chorus]\n"
            "C       G       Am      F\n"
            "We will rock you all night long\n"
            "We will never let you down\n"
            "[Bridge]\n"
            "Something different happens here\n"
        )
        expected, inferred = self.evaluate(content)
        self.assertIsNotNone(inferred)
        self.assertEqual(inferred["method"], "inferred")
        self.assertEqual(inferred["lines"], expected["lines"])
        self.assertGreaterEqual(inferred["confidence"], cd.INFERENCE_CONFIDENCE_THRESHOLD)

    def test_repeated_chorus_identical_lyrics_and_chords(self) -> None:
        content = (
            "[Verse 1]\n"
            "First verse opening line about the morning light\n"
            "Second verse line continuing the story onward\n"
            "[Chorus]\n"
            "Am      F       C       G\n"
            "Hold on tight we are almost there\n"
            "Never letting go of this feeling\n"
            "[Verse 2]\n"
            "Another verse line about the evening sky\n"
            "Yet another line to close out the verse\n"
            "[Chorus]\n"
            "Am      F       C       G\n"
            "Hold on tight we are almost there\n"
            "Never letting go of this feeling\n"
        )
        expected, inferred = self.evaluate(content)
        self.assertEqual(inferred["method"], "inferred")
        self.assertEqual(inferred["lines"], expected["lines"])
        self.assertEqual(inferred["chords"], expected["chords"])

    def test_chorus_with_slightly_changed_final_occurrence(self) -> None:
        content = (
            "[Verse 1]\n"
            "First verse opening line about the morning light\n"
            "Second verse line continuing the story onward\n"
            "[Chorus]\n"
            "Hold on tight we are almost there tonight\n"
            "Never letting go of this feeling now\n"
            "[Verse 2]\n"
            "Another verse line about the evening sky\n"
            "Yet another line to close out the verse\n"
            "[Chorus]\n"
            "Hold on tight we are almost there always\n"
            "Never letting go of this feeling now\n"
        )
        _expected, inferred = self.evaluate(content)
        self.assertEqual(inferred["method"], "inferred")
        self.assertGreaterEqual(inferred["confidence"], cd.INFERENCE_CONFIDENCE_THRESHOLD)

    def test_same_lyrics_slightly_different_chord_formatting(self) -> None:
        content = (
            "[Verse 1]\n"
            "First verse opening line about the morning light\n"
            "Second verse line continuing the story onward\n"
            "[Chorus]\n"
            "am    F    C    G\n"
            "Hold on tight we are almost there\n"
            "Never letting go of this feeling\n"
            "[Verse 2]\n"
            "Another verse line about the evening sky\n"
            "Yet another line to close out the verse\n"
            "[Chorus]\n"
            "Am F C G\n"
            "Hold on tight we are almost there\n"
            "Never letting go of this feeling\n"
        )
        _expected, inferred = self.evaluate(content)
        self.assertEqual(inferred["method"], "inferred")

    def test_repeated_four_chord_loop_with_no_lyrics_is_rejected(self) -> None:
        content = "[Intro]\nC G Am F\nC G Am F\nC G Am F\nC G Am F\n"
        detection = cd.detect_chorus(content)
        self.assertEqual(detection["method"], "none")

    def test_repeated_verses_but_no_chorus_is_rejected(self) -> None:
        content = (
            "[Verse 1]\n"
            "This exact same passage repeats verbatim\n"
            "Word for word every single time through\n"
            "[Bridge]\n"
            "A different bridge passage goes here instead\n"
            "[Verse 2]\n"
            "This exact same passage repeats verbatim\n"
            "Word for word every single time through\n"
        )
        detection = cd.detect_chorus(content)
        self.assertEqual(detection["method"], "none")

    def test_unlabeled_chorus_directly_after_verse_lines_is_still_accepted(self) -> None:
        # No [Chorus] heading at all: the repeated couplet inherits the
        # preceding [Verse N] section name but is only the *tail* of each
        # verse block, not the whole block, so it must not be treated as a
        # repeated verse and rejected.
        content = (
            "[Verse 1]\n"
            "Woke up this morning feeling kind of low\n"
            "Put on my shoes and out the door I go\n"
            "We will rock you all night long\n"
            "We will never let you down\n"
            "[Verse 2]\n"
            "Walked to the station caught the earliest train\n"
            "Thought about you and I smiled again\n"
            "We will rock you all night long\n"
            "We will never let you down\n"
        )
        detection = cd.detect_chorus(content)
        self.assertEqual(detection["method"], "inferred")
        self.assertEqual(
            detection["lines"],
            [
                {"lyrics": "We will rock you all night long", "chords": []},
                {"lyrics": "We will never let you down", "chords": []},
            ],
        )

    def test_unlabeled_chorus_after_blank_line_is_still_accepted(self) -> None:
        # Same as above but with a blank line separating the verse from the
        # unlabeled chorus (the more common tab layout) - the chorus becomes
        # its own block that merely inherits the stale "Verse N" label, so
        # it must not be rejected as a repeated verse either.
        content = (
            "[Verse 1]\n"
            "Woke up this morning feeling kind of low\n"
            "Put on my shoes and out the door I go\n"
            "\n"
            "We will rock you all night long\n"
            "We will never let you down\n"
            "\n"
            "[Verse 2]\n"
            "Walked to the station caught the earliest train\n"
            "Thought about you and I smiled again\n"
            "\n"
            "We will rock you all night long\n"
            "We will never let you down\n"
        )
        detection = cd.detect_chorus(content)
        self.assertEqual(detection["method"], "inferred")

    def test_refrain_consisting_of_one_line(self) -> None:
        content = "[Verse]\nJust one lonely verse line here\n[Refrain]\nOh happy day\n"
        detection = cd.detect_chorus(content)
        self.assertEqual(detection["method"], "explicit")
        self.assertEqual(detection["lines"], [{"lyrics": "Oh happy day", "chords": []}])

    def test_chord_only_instrumental_is_rejected(self) -> None:
        content = "[Solo]\nC G Am F\nDm G C Am\n"
        detection = cd.detect_chorus(content)
        self.assertEqual(detection["method"], "none")

    def test_song_with_no_repeated_section_is_rejected(self) -> None:
        content = (
            "[Verse 1]\nEvery line in this song is completely unique\n"
            "[Verse 2]\nThere is nothing here that repeats itself ever\n"
            "[Verse 3]\nEach passage says something entirely different\n"
        )
        detection = cd.detect_chorus(content)
        self.assertEqual(detection["method"], "none")

    def test_explicit_chorus(self) -> None:
        detection = cd.detect_chorus("[Chorus]\nThis is the chorus\n")
        self.assertEqual(detection["method"], "explicit")
        self.assertEqual(detection["confidence"], 1.0)

    def test_explicit_refrain(self) -> None:
        detection = cd.detect_chorus("[Refrain]\nThis is the refrain\n")
        self.assertEqual(detection["method"], "explicit")

    def test_explicit_hook(self) -> None:
        detection = cd.detect_chorus("[Hook]\nThis is the hook\n")
        self.assertEqual(detection["method"], "explicit")

    def test_explicit_estribillo(self) -> None:
        detection = cd.detect_chorus("[Estribillo]\nEste es el estribillo\n")
        self.assertEqual(detection["method"], "explicit")

    def test_pre_chorus_without_chorus_is_not_explicit(self) -> None:
        content = (
            "[Verse]\nUnique opening verse line right here\n"
            "[Pre-Chorus]\nBuilding up the tension now\n"
            "[Verse 2]\nAnother unique closing verse line\n"
        )
        detection = cd.detect_chorus(content)
        self.assertEqual(detection["method"], "none")

    def test_song_with_both_pre_chorus_and_chorus_uses_the_chorus(self) -> None:
        content = (
            "[Verse]\nUnique opening verse line right here\n"
            "[Pre-Chorus]\nBuilding up the tension now\n"
            "[Chorus]\nThis is the real chorus content\n"
            "[Verse 2]\nAnother unique closing verse line\n"
        )
        detection = cd.detect_chorus(content)
        self.assertEqual(detection["method"], "explicit")
        self.assertEqual(
            detection["lines"], [{"lyrics": "This is the real chorus content", "chords": []}]
        )


if __name__ == "__main__":
    unittest.main()
