#!/usr/bin/env python3
"""Explicit and inferred chorus detection for Ultimate Guitar tab content.

Explicit detection looks for a [Chorus]-equivalent heading. When none is
present, inference searches the song for a repeated passage of lyric lines
(optionally supported by a repeated chord progression) using deterministic
text/chord repetition analysis - no LLM or external service involved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TypedDict

from medleys.chords import parse_chord

# --- Parsing primitives (shared with explicit extraction) -----------------

SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
ANY_SECTION_RE = re.compile(r"^\s*\[[^\]]+\]\s*$")
CHORD_RE = re.compile(r"\[ch\](.*?)\[/ch\]", re.IGNORECASE)
TAB_RE = re.compile(r"\[/?tab\]", re.IGNORECASE)
CHORD_TOKEN_RE = re.compile(
    r"^[A-G](?:#|b)?(?:maj|min|dim|aug|sus|add|m)?[0-9]*(?:sus[0-9]+)?(?:add[0-9]+)?(?:/[A-G](?:#|b)?)?$",
    re.IGNORECASE,
)

CHORUS_EQUIVALENT_NAMES = {"chorus", "refrain", "hook", "estribillo", "coro"}
CHORUS_NUMBERED_RE = re.compile(
    r"^(?:" + "|".join(sorted(CHORUS_EQUIVALENT_NAMES)) + r")\s+\d+$"
)
VERSE_HEADING_RE = re.compile(r"^verse\b")

# UG tabs in languages other than English often skip the "[Name]" bracket
# convention entirely for the chorus heading, e.g. Spanish "Coro:" or
# "(Coro)". These match a *standalone* line in one of those forms.
PAREN_HEADING_RE = re.compile(r"^\(([^)]+)\)$")
COLON_HEADING_RE = re.compile(r"^([^:]+):$")

# --- Normalization ----------------------------------------------------------

REPETITION_ANNOTATION_RE = re.compile(
    r"\(?\bx\s*\d+\)?|\(\s*repeat(?:\s*x?\s*\d*)?\s*\)", re.IGNORECASE
)
APOSTROPHE_RE = re.compile(r"['’]")  # noqa: RUF001 - matches curly apostrophes in scraped lyrics
PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
WHITESPACE_RE = re.compile(r"\s+")

# --- Candidate search / scoring tuning constants ----------------------------

MIN_CANDIDATE_LINES = 2
MAX_CANDIDATE_LINES = 8
MIN_OCCURRENCES = 2
FUZZY_LINE_SIMILARITY_THRESHOLD = 0.82
CHORD_SIMILARITY_THRESHOLD = 0.8
GENERIC_FRAGMENT_MAX_UNIQUE_WORDS = 2
COVERAGE_PENALTY_RATIO = 0.6
MIDDLE_POSITION_RATIO = 0.25
LATE_POSITION_RATIO = 0.5
OUTER_BOUNDARY_RATIO = 0.15
STRONG_LYRIC_MATCH_RATIO = 0.9
# How much lyric_similarity a longer candidate is allowed to give up and
# still be treated as strictly better than a shorter candidate it fully
# contains - the various positional bonuses in score_candidate don't scale
# monotonically with length, so relying on raw score alone can let a short
# fragment of a real, fully-matching chorus outrank the whole thing.
DOMINANCE_SIMILARITY_SLACK = 0.05

# Conservative on purpose: a false negative just means the song is skipped,
# a false positive would corrupt medley similarity scoring with unrelated
# lyrics/chords, so the bar to accept an inferred chorus is kept high.
INFERENCE_CONFIDENCE_THRESHOLD = 0.55

WEIGHT_LYRIC_MATCH = 0.40
WEIGHT_EXACT_BONUS = 0.05
WEIGHT_CHORD_SUPPORT = 0.15
WEIGHT_BOTH_LYRICS_AND_CHORDS = 0.10
WEIGHT_MULTIPLE_OCCURRENCES = 0.10
# Scaled by length/MAX_CANDIDATE_LINES (see score_candidate), not applied
# flat: a short window can match its own occurrences byte-for-byte just by
# luck, so without a length-scaled reward the scorer would keep preferring
# an accidental short match over the real, longer chorus whenever the two
# repeats of the true chorus differ by so much as a line-wrap.
WEIGHT_IDEAL_LENGTH = 0.20
WEIGHT_MIDDLE_POSITION = 0.05
WEIGHT_LATE_POSITION = 0.05
WEIGHT_INTERVENING_CONTENT = 0.05

PENALTY_SINGLE_OCCURRENCE = 0.6
PENALTY_SHORT_CANDIDATE = 0.15
PENALTY_GENERIC_FRAGMENT = 0.25
PENALTY_COVERS_MOST_OF_SONG = 0.35
PENALTY_VERSE_HEADING = 0.4
PENALTY_INTRO_OUTRO_ONLY = 0.2
PENALTY_WEAK_LYRICS_NO_CHORDS = 0.2


class ChordPosition(TypedDict):
    symbol: str
    position: int


class ChorusLine(TypedDict):
    lyrics: str
    chords: list[ChordPosition]


class ChorusDetection(TypedDict):
    lines: list[ChorusLine]
    chords: list[str]
    method: str
    confidence: float


class SongBlock(TypedDict):
    source_lines: list[str]
    chorus_lines: list[ChorusLine]
    normalized_lyrics: list[str]
    chords: list[str]
    start: int
    end: int
    section_name: str | None
    heading_adjacent: bool


@dataclass
class LyricUnit:
    normalized: str
    chorus_line: ChorusLine
    block_index: int
    source_line: int
    section_name: str | None
    heading_adjacent: bool


@dataclass
class ChorusCandidate:
    start: int
    length: int
    occurrence_starts: list[int]
    lyric_similarity: float
    chord_supported: bool
    chords: list[str]
    lines: list[ChorusLine]
    normalized_lines: tuple[str, ...]
    source_start: int
    source_end: int
    section_names: list[str | None]
    spans_whole_block: bool
    heading_adjacent: bool


# --- Public API --------------------------------------------------------------


def detect_chorus(content: str) -> ChorusDetection:
    explicit = detect_explicit_chorus(content)
    if explicit is not None:
        return explicit
    inferred = detect_inferred_chorus(content)
    if inferred is not None:
        return inferred
    return {"lines": [], "chords": [], "method": "none", "confidence": 0.0}


def detect_explicit_chorus(content: str) -> ChorusDetection | None:
    lines = [
        parsed
        for parsed in (parse_chorus_line(line) for line in first_chorus_source_lines(content))
        if parsed["lyrics"].strip() or parsed["chords"]
    ]
    if not lines:
        return None
    chords = [chord["symbol"] for line in lines for chord in line["chords"]]
    return {"lines": lines, "chords": chords, "method": "explicit", "confidence": 1.0}


def detect_inferred_chorus(content: str) -> ChorusDetection | None:
    blocks = parse_song_blocks(content)
    units = build_lyric_units(blocks)
    if len(units) < MIN_CANDIDATE_LINES:
        return None

    source_lines = content.splitlines()
    flat_chords = build_flat_chord_index(content)
    raw_candidates = generate_raw_candidates(units)
    if not raw_candidates:
        return None

    candidates = [
        build_candidate(units, source_lines, flat_chords, anchor, length, occurrences)
        for anchor, length, occurrences in raw_candidates
    ]
    scored = [
        (score_candidate(candidate, len(units), len(source_lines)), candidate)
        for candidate in candidates
    ]
    scored = [
        (score, candidate)
        for score, candidate in scored
        if not any(
            is_subsumed_by(candidate, other)
            for other_score, other in scored
            if other is not candidate
        )
    ]
    if not scored:
        return None
    best_score, best = max(scored, key=lambda item: candidate_sort_key(*item))

    if best_score < INFERENCE_CONFIDENCE_THRESHOLD:
        return None
    return {
        "lines": best.lines,
        "chords": best.chords,
        "method": "inferred",
        "confidence": round(best_score, 4),
    }


# --- Explicit section extraction (existing behaviour) -----------------------


def is_chorus_section(section: str) -> bool:
    name = section.strip().lower()
    return name in CHORUS_EQUIVALENT_NAMES or CHORUS_NUMBERED_RE.match(name) is not None


def chorus_heading_name(line: str) -> str | None:
    """Return the matched name when `line` is a standalone chorus-equivalent
    heading written as `(Name)`, `Name:`, or a bare `Name` line - i.e. every
    common heading style except `[Name]`, which callers already detect via
    `SECTION_RE` and check separately."""
    stripped = line.strip()
    if not stripped:
        return None
    for pattern in (PAREN_HEADING_RE, COLON_HEADING_RE):
        match = pattern.match(stripped)
        if match:
            return match.group(1) if is_chorus_section(match.group(1)) else None
    return stripped if is_chorus_section(stripped) else None


def is_chord_line(line: str) -> bool:
    tokens = line.split()
    return bool(tokens) and all(CHORD_TOKEN_RE.match(token) for token in tokens)


def first_chorus_source_lines(content: str) -> list[str]:
    lines = []
    in_chorus = False
    found_chorus = False
    # True while capturing a chorus that started at an unbracketed heading
    # (e.g. "Coro:") - such tabs don't reliably use a closing heading, so a
    # blank line is the only structural signal that the section ended.
    unbracketed_capture = False

    for line in content.splitlines():
        section_match = SECTION_RE.match(line)
        if section_match:
            if found_chorus:
                break
            is_chorus = is_chorus_section(section_match.group(1))
            in_chorus = is_chorus
            found_chorus = found_chorus or is_chorus
            unbracketed_capture = False
            continue
        if not found_chorus and chorus_heading_name(line) is not None:
            in_chorus = True
            found_chorus = True
            unbracketed_capture = True
            continue
        if in_chorus and ANY_SECTION_RE.match(line):
            break
        if in_chorus and unbracketed_capture and not line.strip():
            break
        if in_chorus:
            lines.append(line)

    return lines


def parse_chorus_line(line: str) -> ChorusLine:
    line = TAB_RE.sub("", line)
    if CHORD_RE.search(line):
        return parse_marked_chord_line(line)
    if is_chord_line(line):
        return {
            "lyrics": "",
            "chords": [
                {"symbol": match.group(0), "position": match.start()}
                for match in re.finditer(r"\S+", line)
            ],
        }
    return {"lyrics": line, "chords": []}


def parse_marked_chord_line(line: str) -> ChorusLine:
    lyrics = []
    chords: list[ChordPosition] = []
    cursor = 0

    for match in CHORD_RE.finditer(line):
        lyrics.append(line[cursor : match.start()])
        chord = match.group(1).strip()
        if chord:
            chords.append({"symbol": chord, "position": len("".join(lyrics))})
        cursor = match.end()

    lyrics.append(line[cursor:])
    return {"lyrics": "".join(lyrics), "chords": chords}


# --- Normalization -----------------------------------------------------------


def normalize_lyric_line(text: str) -> str:
    normalized = TAB_RE.sub("", text)
    normalized = CHORD_RE.sub("", normalized)
    normalized = REPETITION_ANNOTATION_RE.sub(" ", normalized)
    normalized = normalized.lower()
    normalized = APOSTROPHE_RE.sub("", normalized)
    normalized = PUNCTUATION_RE.sub(" ", normalized)
    return WHITESPACE_RE.sub(" ", normalized).strip()


def normalize_chord_symbol(symbol: str) -> str:
    stripped = symbol.strip()
    if not stripped:
        return stripped
    candidate = stripped[0].upper() + stripped[1:]
    parsed = parse_chord(candidate)
    if parsed["root"] is None:
        return stripped
    canonical = f"{parsed['root']}{parsed['quality']}"
    if parsed["bass"]:
        canonical += f"/{parsed['bass'].strip()}"
    return canonical


def normalize_chord_progression(chords: list[str]) -> list[str]:
    return [normalize_chord_symbol(chord) for chord in chords]


# --- Milestone 2: song block parsing -----------------------------------------


def parse_song_blocks(content: str) -> list[SongBlock]:
    blocks: list[SongBlock] = []
    lines = content.splitlines()
    current_section: str | None = None
    # True until the first real block under the current heading is flushed.
    # A block is only "heading_adjacent" while this is still true - once a
    # blank line has separated a block from its heading, later blocks under
    # the same (unchanged) heading are just inheriting a stale label, not
    # provably part of that section.
    section_started = True
    body_lines: list[str] = []
    body_start: int | None = None

    def flush(end_index: int) -> None:
        nonlocal body_lines, body_start, section_started
        if body_lines and body_start is not None:
            block = build_block(body_lines, body_start, end_index, current_section, section_started)
            if block is not None:
                blocks.append(block)
                section_started = False
        body_lines = []
        body_start = None

    for index, line in enumerate(lines):
        section_match = SECTION_RE.match(line)
        heading_name = (
            section_match.group(1).strip() if section_match else chorus_heading_name(line)
        )
        if heading_name is not None:
            flush(index)
            current_section = heading_name
            section_started = True
            continue
        if not line.strip():
            flush(index)
            continue
        if body_start is None:
            body_start = index
        body_lines.append(line)

    flush(len(lines))
    return blocks


def build_block(
    source_lines: list[str],
    start: int,
    end: int,
    section_name: str | None,
    heading_adjacent: bool,
) -> SongBlock | None:
    chorus_lines = [parse_chorus_line(line) for line in source_lines]
    normalized_lyrics = [
        normalized
        for normalized in (normalize_lyric_line(line["lyrics"]) for line in chorus_lines)
        if normalized
    ]
    chords = [chord["symbol"] for line in chorus_lines for chord in line["chords"]]
    if not normalized_lyrics and not chords:
        return None
    return {
        "source_lines": source_lines,
        "chorus_lines": chorus_lines,
        "normalized_lyrics": normalized_lyrics,
        "chords": chords,
        "start": start,
        "end": end,
        "section_name": section_name,
        "heading_adjacent": heading_adjacent,
    }


def build_lyric_units(blocks: list[SongBlock]) -> list[LyricUnit]:
    units: list[LyricUnit] = []
    for block_index, block in enumerate(blocks):
        for offset, chorus_line in enumerate(block["chorus_lines"]):
            normalized = normalize_lyric_line(chorus_line["lyrics"])
            if not normalized:
                continue
            units.append(
                LyricUnit(
                    normalized=normalized,
                    chorus_line=chorus_line,
                    block_index=block_index,
                    source_line=block["start"] + offset,
                    section_name=block["section_name"],
                    heading_adjacent=block["heading_adjacent"],
                )
            )
    return units


def build_flat_chord_index(content: str) -> dict[int, list[str]]:
    index: dict[int, list[str]] = {}
    for line_index, line in enumerate(content.splitlines()):
        if SECTION_RE.match(line) or not line.strip():
            continue
        parsed = parse_chorus_line(line)
        symbols = [chord["symbol"] for chord in parsed["chords"]]
        if symbols:
            index[line_index] = symbols
    return index


# --- Milestone 4: repeated-section candidate generation ---------------------


def window_spans_single_section(window: list[LyricUnit]) -> bool:
    return all(window[i].section_name == window[i + 1].section_name for i in range(len(window) - 1))


def window_similarity(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if left == right:
        return 1.0
    return SequenceMatcher(None, "\n".join(left), "\n".join(right)).ratio()


def occurrence_matches_whole_block(units: list[LyricUnit], start: int, length: int) -> bool:
    """True when no other lyric line from the same block(s) falls outside the span.

    Distinguishes "this whole verse repeats verbatim" (every line of the
    block is part of the candidate) from an unlabeled chorus that merely
    sits inside a verse-headed block alongside other verse-only lines - the
    latter must not be treated as a repeated verse.
    """
    block_indices = {unit.block_index for unit in units[start : start + length]}
    return all(
        start <= index < start + length
        for index, unit in enumerate(units)
        if unit.block_index in block_indices
    )


def generate_raw_candidates(units: list[LyricUnit]) -> list[tuple[int, int, list[int]]]:
    candidates: list[tuple[int, int, list[int]]] = []
    max_length = min(MAX_CANDIDATE_LINES, len(units))
    for length in range(MIN_CANDIDATE_LINES, max_length + 1):
        candidates.extend(generate_candidates_for_length(units, length))
    return candidates


def generate_candidates_for_length(
    units: list[LyricUnit], length: int
) -> list[tuple[int, int, list[int]]]:
    n = len(units)
    windows: list[tuple[int, tuple[str, ...] | None]] = []
    for start in range(n - length + 1):
        window = units[start : start + length]
        if window_spans_single_section(window):
            windows.append((start, tuple(unit.normalized for unit in window)))
        else:
            windows.append((start, None))

    candidates: list[tuple[int, int, list[int]]] = []
    for anchor_start, anchor_text in windows:
        if anchor_text is None:
            continue
        occurrences = [anchor_start]
        next_allowed = anchor_start + length
        while True:
            match_start = best_matching_window_start(windows, anchor_text, next_allowed, length)
            if match_start is None:
                break
            occurrences.append(match_start)
            next_allowed = match_start + length
        if len(occurrences) >= MIN_OCCURRENCES:
            candidates.append((anchor_start, length, occurrences))
    return candidates


def best_matching_window_start(
    windows: list[tuple[int, tuple[str, ...] | None]],
    anchor_text: tuple[str, ...],
    next_allowed: int,
    length: int,
) -> int | None:
    """Find the next occurrence of `anchor_text` at or after `next_allowed`.

    A window shifted by a line or two from the true repeat can still clear
    the fuzzy threshold, so taking the very first passing window risks
    locking onto a worse, misaligned match instead of the correctly aligned
    repeat sitting just beyond it. Once a first match is found, this also
    checks the next `length` window starts and keeps whichever scores best.
    """
    first_hit: int | None = None
    for start, text in windows[next_allowed:]:
        if text is None:
            continue
        if window_similarity(anchor_text, text) >= FUZZY_LINE_SIMILARITY_THRESHOLD:
            first_hit = start
            break
    if first_hit is None:
        return None

    best_start = first_hit
    best_score = window_similarity(anchor_text, windows[first_hit][1])
    for start, text in windows[first_hit + 1 : first_hit + 1 + length]:
        if text is None:
            continue
        score = window_similarity(anchor_text, text)
        if score > best_score:
            best_start, best_score = start, score
    return best_start


def occurrence_source_start(
    units: list[LyricUnit], flat_chords: dict[int, list[str]], start: int
) -> int:
    first_source = units[start].source_line

    # UG tabs conventionally put a chord-only line directly above the lyric
    # line it belongs to, so the chord for the candidate's first lyric line
    # sits just *before* the span. Walk back over any such lines, stopping at
    # the previous lyric unit so we never absorb an unrelated passage.
    previous_lyric_source = units[start - 1].source_line if start > 0 else -1
    lead = first_source - 1
    while lead > previous_lyric_source and lead in flat_chords:
        first_source = lead
        lead -= 1
    return first_source


def occurrence_chords(
    units: list[LyricUnit], flat_chords: dict[int, list[str]], start: int, length: int
) -> list[str]:
    first_source = occurrence_source_start(units, flat_chords, start)
    last_source = units[start + length - 1].source_line

    symbols: list[str] = []
    for source_line in range(first_source, last_source + 1):
        symbols.extend(flat_chords.get(source_line, []))
    return symbols


def chords_match(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    return SequenceMatcher(None, left, right, autojunk=False).ratio() >= CHORD_SIMILARITY_THRESHOLD


def build_candidate(
    units: list[LyricUnit],
    source_lines: list[str],
    flat_chords: dict[int, list[str]],
    anchor: int,
    length: int,
    occurrences: list[int],
) -> ChorusCandidate:
    anchor_window = units[anchor : anchor + length]
    anchor_text = tuple(unit.normalized for unit in anchor_window)
    anchor_chords = normalize_chord_progression(
        occurrence_chords(units, flat_chords, anchor, length)
    )

    similarities = []
    chord_matches = 0
    for other in occurrences[1:]:
        other_window = units[other : other + length]
        other_text = tuple(unit.normalized for unit in other_window)
        similarities.append(window_similarity(anchor_text, other_text))
        other_chords = normalize_chord_progression(
            occurrence_chords(units, flat_chords, other, length)
        )
        if chords_match(anchor_chords, other_chords):
            chord_matches += 1

    lyric_similarity = min(similarities) if similarities else 0.0
    chord_supported = chord_matches > 0 and bool(anchor_chords)

    source_start = occurrence_source_start(units, flat_chords, anchor)
    source_end = anchor_window[-1].source_line
    raw_lines = [parse_chorus_line(line) for line in source_lines[source_start : source_end + 1]]
    lines = [line for line in raw_lines if line["lyrics"].strip() or line["chords"]]
    chords = occurrence_chords(units, flat_chords, anchor, length)
    spans_whole_block = all(
        occurrence_matches_whole_block(units, occurrence, length) for occurrence in occurrences
    )
    heading_adjacent = all(units[occurrence].heading_adjacent for occurrence in occurrences)

    return ChorusCandidate(
        start=anchor,
        length=length,
        occurrence_starts=occurrences,
        lyric_similarity=lyric_similarity,
        chord_supported=chord_supported,
        chords=chords,
        lines=lines,
        normalized_lines=anchor_text,
        source_start=source_start,
        source_end=source_end,
        section_names=[units[index].section_name for index in occurrences],
        spans_whole_block=spans_whole_block,
        heading_adjacent=heading_adjacent,
    )


# --- Milestone 5: scoring ------------------------------------------------


def is_verse_heading(name: str | None) -> bool:
    if not name:
        return False
    return VERSE_HEADING_RE.match(name.strip().lower()) is not None


def is_generic_fragment(candidate: ChorusCandidate) -> bool:
    words = " ".join(candidate.normalized_lines).split()
    if not words:
        return True
    return len(set(words)) <= GENERIC_FRAGMENT_MAX_UNIQUE_WORDS


def is_intro_outro_only(candidate: ChorusCandidate, total_lyric_units: int) -> bool:
    if total_lyric_units == 0:
        return False
    boundary = max(1, round(total_lyric_units * OUTER_BOUNDARY_RATIO))
    late_boundary = total_lyric_units - boundary
    return all(start < boundary or start >= late_boundary for start in candidate.occurrence_starts)


def has_intervening_content(candidate: ChorusCandidate) -> bool:
    starts = candidate.occurrence_starts
    return all(
        starts[index + 1] > starts[index] + candidate.length for index in range(len(starts) - 1)
    )


def score_candidate(
    candidate: ChorusCandidate, total_lyric_units: int, total_source_lines: int
) -> float:
    score = 0.0
    exact = candidate.lyric_similarity >= 0.999

    score += WEIGHT_LYRIC_MATCH * candidate.lyric_similarity
    if exact:
        score += WEIGHT_EXACT_BONUS

    if candidate.chord_supported:
        score += WEIGHT_CHORD_SUPPORT
        if exact or candidate.lyric_similarity >= FUZZY_LINE_SIMILARITY_THRESHOLD:
            score += WEIGHT_BOTH_LYRICS_AND_CHORDS

    occurrence_count = len(candidate.occurrence_starts)
    if occurrence_count >= 3:
        score += WEIGHT_MULTIPLE_OCCURRENCES
    elif occurrence_count < MIN_OCCURRENCES:
        score -= PENALTY_SINGLE_OCCURRENCE

    if MIN_CANDIDATE_LINES <= candidate.length <= MAX_CANDIDATE_LINES:
        score += WEIGHT_IDEAL_LENGTH * (candidate.length / MAX_CANDIDATE_LINES)
    if candidate.length <= 1:
        score -= PENALTY_SHORT_CANDIDATE

    if candidate.source_start / max(total_source_lines, 1) >= MIDDLE_POSITION_RATIO:
        score += WEIGHT_MIDDLE_POSITION
    if candidate.occurrence_starts[-1] / max(total_lyric_units, 1) >= LATE_POSITION_RATIO:
        score += WEIGHT_LATE_POSITION
    if has_intervening_content(candidate):
        score += WEIGHT_INTERVENING_CONTENT

    span_ratio = (occurrence_count * candidate.length) / max(total_lyric_units, 1)
    if span_ratio >= COVERAGE_PENALTY_RATIO:
        score -= PENALTY_COVERS_MOST_OF_SONG

    if is_generic_fragment(candidate):
        score -= PENALTY_GENERIC_FRAGMENT

    if (
        candidate.spans_whole_block
        and candidate.heading_adjacent
        and candidate.section_names
        and all(is_verse_heading(name) for name in candidate.section_names)
    ):
        score -= PENALTY_VERSE_HEADING

    if is_intro_outro_only(candidate, total_lyric_units):
        score -= PENALTY_INTRO_OUTRO_ONLY

    if not candidate.chord_supported and candidate.lyric_similarity < STRONG_LYRIC_MATCH_RATIO:
        score -= PENALTY_WEAK_LYRICS_NO_CHORDS

    return max(0.0, min(1.0, score))


def candidate_sort_key(
    score: float, candidate: ChorusCandidate
) -> tuple[float, float, int, int, int, int]:
    return (
        round(score, 2),
        candidate.lyric_similarity,
        1 if candidate.chord_supported else 0,
        len(candidate.occurrence_starts),
        # Prefer the longer candidate on a tie: a longer verified repeat
        # captures more of the actual chorus than a short subset of it.
        candidate.length,
        -candidate.source_start,
    )


def is_subsumed_by(candidate: ChorusCandidate, other: ChorusCandidate) -> bool:
    """True when `other` is a longer, comparably well-matched candidate
    whose occurrences fully contain `candidate`'s.

    The positional bonuses in score_candidate don't scale monotonically
    with length, so a short fragment of the real chorus can occasionally
    out-score the fuller match it sits inside of. `candidate` is then just
    that fragment and must not be picked over `other`.
    """
    if other.length <= candidate.length:
        return False
    if len(other.occurrence_starts) < len(candidate.occurrence_starts):
        return False
    if other.lyric_similarity < candidate.lyric_similarity - DOMINANCE_SIMILARITY_SLACK:
        return False
    other_spans = [(start, start + other.length) for start in other.occurrence_starts]

    def contained(start: int) -> bool:
        end = start + candidate.length
        return any(span_start <= start and end <= span_end for span_start, span_end in other_spans)

    return all(contained(start) for start in candidate.occurrence_starts)
