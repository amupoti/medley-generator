from __future__ import annotations

import re
from typing import TypedDict


class ParsedChord(TypedDict):
    chord: str
    root: str | None
    pitch: int | None
    quality: str
    bass: str | None


PITCH_CLASSES = {
    "Cb": 11,
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "E#": 5,
    "Fb": 4,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
    "B#": 0,
}

SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
CHORD_PART_RE = re.compile(r"^([A-G](?:#|b)?)(.*)$")
MAJOR_SCALE_INTERVALS = {0, 2, 4, 5, 7, 9, 11}
MINOR_SCALE_INTERVALS = {0, 2, 3, 5, 7, 8, 10}
FLAT_MAJOR_KEYS = {5, 10, 3, 8, 1, 6}
SHARP_MAJOR_KEYS = {7, 2, 9, 4, 11}


def parse_chord(chord: str) -> ParsedChord:
    root_part, bass_part = split_bass(chord)
    root_match = CHORD_PART_RE.match(root_part)
    if not root_match:
        return {"chord": chord, "root": None, "pitch": None, "quality": "", "bass": bass_part}

    root = root_match.group(1)
    quality = root_match.group(2)
    return {
        "chord": chord,
        "root": root,
        "pitch": PITCH_CLASSES.get(root),
        "quality": quality,
        "bass": bass_part,
    }


def split_bass(chord: str) -> tuple[str, str | None]:
    if "/" not in chord:
        return chord, None
    root_part, bass_part = chord.split("/", 1)
    return root_part, bass_part


def transpose_chord(chord: str, semitones: int, prefer_flats: bool | None = None) -> str:
    parsed = parse_chord(chord)
    if parsed["pitch"] is None:
        return chord

    use_flats = prefer_flats
    if use_flats is None:
        root = parsed["root"] or ""
        inferred_bass = parsed["bass"] or ""
        use_flats = "b" in root or "b" in inferred_bass
    names = FLAT_NAMES if use_flats else SHARP_NAMES
    transposed_root = names[(parsed["pitch"] + semitones) % 12]
    transposed = f"{transposed_root}{parsed['quality']}"

    bass = parsed["bass"]
    if bass:
        bass_match = CHORD_PART_RE.match(bass)
        if bass_match and bass_match.group(1) in PITCH_CLASSES:
            bass_pitch = PITCH_CLASSES[bass_match.group(1)]
            transposed += f"/{names[(bass_pitch + semitones) % 12]}{bass_match.group(2)}"
        else:
            transposed += f"/{bass}"

    return transposed


def transpose_chords(chords: list[str], semitones: int) -> list[str]:
    prefer_flats = infer_prefer_flats(chords, semitones)
    return [transpose_chord(chord, semitones, prefer_flats) for chord in chords]


def infer_prefer_flats(chords: list[str], semitones: int = 0) -> bool:
    parsed = [parse_chord(chord) for chord in chords]
    pitches = [((item["pitch"] + semitones) % 12) for item in parsed if item["pitch"] is not None]
    if not pitches:
        return False

    key_root, mode = best_context_key(pitches, parsed)
    if mode == "minor":
        key_root = (key_root + 3) % 12
    if key_root in FLAT_MAJOR_KEYS:
        return True
    if key_root in SHARP_MAJOR_KEYS:
        return False
    return original_flat_count(parsed) > original_sharp_count(parsed)


def best_context_key(pitches: list[int], parsed_chords: list[ParsedChord]) -> tuple[int, str]:
    pitch_set = set(pitches)
    candidates: list[tuple[int, int, str]] = []
    for root in range(12):
        candidates.append((key_score(pitch_set, root, MAJOR_SCALE_INTERVALS), root, "major"))
        candidates.append((key_score(pitch_set, root, MINOR_SCALE_INTERVALS), root, "minor"))
    best = max(
        candidates,
        key=lambda candidate: (candidate[0], original_root_bonus(candidate[1], parsed_chords)),
    )
    return best[1], best[2]


def key_score(pitches: set[int], root: int, scale_intervals: set[int]) -> int:
    scale = {((root + interval) % 12) for interval in scale_intervals}
    return len(pitches & scale) - len(pitches - scale)


def original_root_bonus(root: int, parsed_chords: list[ParsedChord]) -> int:
    for item in parsed_chords:
        if item["pitch"] == root:
            return 1
    return 0


def original_flat_count(parsed_chords: list[ParsedChord]) -> int:
    return sum("b" in item["root"] for item in parsed_chords if item["root"])


def original_sharp_count(parsed_chords: list[ParsedChord]) -> int:
    return sum("#" in item["root"] for item in parsed_chords if item["root"])
