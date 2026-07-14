import re
from typing import Optional, Tuple


PITCH_CLASSES = {
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
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
}

SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
CHORD_PART_RE = re.compile(r"^([A-G](?:#|b)?)(.*)$")


def parse_chord(chord: str) -> dict:
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


def split_bass(chord: str) -> Tuple[str, Optional[str]]:
    if "/" not in chord:
        return chord, None
    root_part, bass_part = chord.split("/", 1)
    return root_part, bass_part


def transpose_chord(chord: str, semitones: int) -> str:
    parsed = parse_chord(chord)
    if parsed["pitch"] is None:
        return chord

    use_flats = "b" in parsed["root"] or (parsed["bass"] and "b" in parsed["bass"])
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
    return [transpose_chord(chord, semitones) for chord in chords]
