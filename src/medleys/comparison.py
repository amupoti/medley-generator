#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from medleys.chords import PITCH_CLASSES, parse_chord
from medleys.database import SongRecord

PairScore = dict[str, Any]
Transition = dict[str, Any]
MedleyOutput = dict[str, Any]
ComparisonOutput = dict[str, Any]
PairScoreMap = dict[tuple[str, str], PairScore]


def load_songs(path: Path, source_url: str | None = None) -> list[SongRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("songs"), dict):
        songs = list(data["songs"].values())
    elif isinstance(data, dict) and "songs" in data:
        songs = data["songs"]
    else:
        songs = data
    if source_url:
        songs = [song for song in songs if source_url in song.get("sources", [])]
    return dedupe_songs([song for song in songs if song.get("chorus_chords")])


def dedupe_songs(songs: list[SongRecord]) -> list[SongRecord]:
    best: dict[str, SongRecord] = {}
    for song in songs:
        key = canonical_song_key(song)
        current = best.get(key)
        if not current or song_rank(song) < song_rank(current):
            best[key] = song
    return sorted(best.values(), key=song_rank)


def canonical_song_key(song: SongRecord) -> str:
    artist = clean_name(song.get("artist"))
    title = clean_name(song.get("title"))
    return f"{artist}::{title}"


def clean_name(value: str | None) -> str:
    value = value or ""
    return " ".join(value.casefold().split())


def song_rank(song: SongRecord) -> int:
    return song.get("explore_rank") or 999999


def normalize_quality(quality: str) -> str:
    quality = quality.lower()
    if quality in ("", "maj"):
        return "maj"
    if quality.startswith("m") and not quality.startswith("maj"):
        return "min"
    if quality.startswith("sus"):
        return "sus"
    if quality.startswith("dim"):
        return "dim"
    if quality.startswith("aug"):
        return "aug"
    if quality.startswith("7"):
        return "dom"
    return quality


def reduce_repeated_loop(chords: list[str]) -> list[str]:
    for size in range(1, max(1, len(chords) // 2) + 1):
        if len(chords) % size != 0:
            continue
        loop = chords[:size]
        if loop * (len(chords) // size) == chords:
            return loop
    return chords


def normalize_song(song: SongRecord) -> SongRecord:
    chords = reduce_repeated_loop(song["chorus_chords"])
    parsed = [parse_chord(chord) for chord in chords]
    pitch_sequence = [item["pitch"] for item in parsed if item["pitch"] is not None]
    quality_sequence = [normalize_quality(item["quality"]) for item in parsed]
    interval_sequence = intervals(pitch_sequence)
    return {
        **song,
        "normalized_chords": chords,
        "pitch_sequence": pitch_sequence,
        "quality_sequence": quality_sequence,
        "interval_sequence": interval_sequence,
    }


def intervals(pitches: list[int]) -> list[int]:
    if len(pitches) < 2:
        return []
    return [(pitches[index + 1] - pitches[index]) % 12 for index in range(len(pitches) - 1)]


def sequence_score(left: list[Any], right: list[Any]) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(a=left, b=right, autojunk=False).ratio()


def jaccard_score(left: list[Any], right: list[Any]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def similarity(left: SongRecord, right: SongRecord) -> PairScore:
    absolute = sequence_score(left["normalized_chords"], right["normalized_chords"])
    interval = sequence_score(left["interval_sequence"], right["interval_sequence"])
    qualities = sequence_score(left["quality_sequence"], right["quality_sequence"])
    chord_overlap = jaccard_score(left["normalized_chords"], right["normalized_chords"])
    score = (absolute * 0.35) + (interval * 0.35) + (qualities * 0.15) + (chord_overlap * 0.15)
    return {
        "score": round(score, 4),
        "absolute_score": round(absolute, 4),
        "interval_score": round(interval, 4),
        "quality_score": round(qualities, 4),
        "chord_overlap": round(chord_overlap, 4),
        "transpose_right_by": best_transpose(left["pitch_sequence"], right["pitch_sequence"]),
    }


def pairwise_scores(songs: list[SongRecord]) -> list[PairScore]:
    pairs: list[PairScore] = []
    for left_index, left in enumerate(songs):
        for right in songs[left_index + 1 :]:
            scores = similarity(left, right)
            pairs.append(
                {
                    **scores,
                    "left": song_ref(left),
                    "right": song_ref(right),
                }
            )
    return sorted(pairs, key=lambda pair: pair["score"], reverse=True)


def song_ref(song: SongRecord) -> SongRecord:
    ref: SongRecord = {
        "rank": song.get("explore_rank"),
        "artist": song.get("artist"),
        "title": song.get("title"),
        "url": song.get("url"),
        "chorus_chords": song.get("normalized_chords", song.get("chorus_chords")),
        "global_transpose_by": song.get("global_transpose_by", 0),
    }
    if song.get("chorus_lines"):
        ref["chorus_lines"] = song["chorus_lines"]
    if song.get("favorites_count") is not None:
        ref["favorites_count"] = song["favorites_count"]
    return ref


def medley_order(songs: list[SongRecord], pairs: list[PairScore], target_root: str) -> MedleyOutput:
    if not songs:
        return {"average_transition_score": 0.0, "songs": [], "transitions": []}

    by_key = {song_key(song): song for song in songs}
    pair_score: PairScoreMap = {}
    for pair in pairs:
        left_key = ref_key(pair["left"])
        right_key = ref_key(pair["right"])
        pair_score[(left_key, right_key)] = pair
        pair_score[(right_key, left_key)] = reverse_pair(pair)

    orders = [greedy_order_from(start, set(by_key), pair_score) for start in by_key]
    order = max(orders, key=lambda candidate: path_score(candidate, pair_score))
    songs_in_order = apply_target_root_transpose([by_key[key] for key in order], target_root)

    return {
        "average_transition_score": round(path_score(order, pair_score), 4),
        "target_root": target_root,
        "songs": [song_ref(song) for song in songs_in_order],
        "transitions": build_transitions(order, by_key, pair_score),
    }


def ordered_medley(songs: list[SongRecord], target_root: str) -> MedleyOutput:
    if not songs:
        return {"average_transition_score": 0.0, "songs": [], "transitions": []}

    songs_in_order = apply_target_root_transpose(songs, target_root)
    transitions: list[Transition] = []
    for index in range(len(songs_in_order) - 1):
        pair = similarity(songs_in_order[index], songs_in_order[index + 1])
        transitions.append(
            {
                "from": song_ref(songs_in_order[index]),
                "to": song_ref(songs_in_order[index + 1]),
                "score": pair["score"],
                "transpose_to_by": pair["transpose_right_by"],
            }
        )
    scores = [transition["score"] for transition in transitions]
    average = sum(scores) / len(scores) if scores else 0.0

    return {
        "average_transition_score": round(average, 4),
        "target_root": target_root,
        "songs": [song_ref(song) for song in songs_in_order],
        "transitions": transitions,
    }


def last_chord_medley(songs: list[SongRecord], target_root: str) -> MedleyOutput:
    if not songs:
        return {"average_transition_score": 0.0, "songs": [], "transitions": []}

    target_pitch = PITCH_CLASSES[target_root]
    chains = [last_chord_chain_from(start, songs, target_pitch) for start in songs]
    chain = max(chains, key=lambda candidate: candidate[2])
    ordered, shifts, average = chain
    shifted_songs = [
        {**song, "global_transpose_by": shift} for song, shift in zip(ordered, shifts)
    ]
    transitions = []
    for index in range(len(shifted_songs) - 1):
        transitions.append(
            {
                "from": song_ref(shifted_songs[index]),
                "to": song_ref(shifted_songs[index + 1]),
                "score": last_chord_transition_score(
                    ordered[index], shifts[index], ordered[index + 1], shifts[index + 1]
                ),
                "transpose_to_by": shifts[index + 1],
            }
        )
    return {
        "average_transition_score": round(average, 4),
        "target_root": target_root,
        "songs": [song_ref(song) for song in shifted_songs],
        "transitions": transitions,
    }


def last_chord_chain_from(
    start: SongRecord, songs: list[SongRecord], target_pitch: int
) -> tuple[list[SongRecord], list[int], float]:
    ordered = [start]
    shifts = [transpose_to_target(start["pitch_sequence"], target_pitch)]
    unused = [song for song in songs if song is not start]
    scores: list[float] = []
    while unused:
        current = ordered[-1]
        current_shift = shifts[-1]
        current_pitches = shifted_pitches(current, current_shift)
        end_pitch = current_pitches[-1] if current_pitches else target_pitch
        candidates = []
        for candidate in unused:
            shift = transpose_to_target(candidate["pitch_sequence"], end_pitch)
            score = last_chord_transition_score(current, current_shift, candidate, shift)
            candidates.append((score, -song_rank(candidate), candidate, shift))
        score, _, selected, shift = max(candidates, key=lambda candidate: candidate[:2])
        ordered.append(selected)
        shifts.append(shift)
        scores.append(score)
        unused.remove(selected)
    average = sum(scores) / len(scores) if scores else 0.0
    return ordered, shifts, average


def shifted_pitches(song: SongRecord, shift: int) -> list[int]:
    return [((pitch + shift) % 12) for pitch in song["pitch_sequence"]]


def last_chord_transition_score(
    left: SongRecord, left_shift: int, right: SongRecord, right_shift: int
) -> float:
    left_pitches = shifted_pitches(left, left_shift)
    right_pitches = shifted_pitches(right, right_shift)
    absolute = sequence_score(left_pitches, right_pitches)
    interval = sequence_score(left["interval_sequence"], right["interval_sequence"])
    qualities = sequence_score(left["quality_sequence"], right["quality_sequence"])
    overlap = jaccard_score(left_pitches, right_pitches)
    return round(
        (absolute * 0.35) + (interval * 0.35) + (qualities * 0.15) + (overlap * 0.15), 4
    )


def apply_target_root_transpose(songs: list[SongRecord], target_root: str) -> list[SongRecord]:
    target_pitch = PITCH_CLASSES[target_root]
    return [
        {**song, "global_transpose_by": transpose_to_target(song["pitch_sequence"], target_pitch)}
        for song in songs
    ]


def transpose_to_target(pitches: list[int], target_pitch: int) -> int:
    if not pitches:
        return 0
    shift = (target_pitch - pitches[0]) % 12
    return shift - 12 if shift > 6 else shift


def greedy_order_from(start: str, all_keys: set[str], pair_score: PairScoreMap) -> list[str]:
    order = [start]
    unused = set(all_keys)
    unused.remove(start)
    while unused:
        best = max(unused, key=lambda candidate: transition_score(pair_score, order[-1], candidate))
        order.append(best)
        unused.remove(best)
    return order


def path_score(order: list[str], pair_score: PairScoreMap) -> float:
    if len(order) < 2:
        return 0.0
    total = sum(
        transition_score(pair_score, order[index], order[index + 1])
        for index in range(len(order) - 1)
    )
    return total / (len(order) - 1)


def build_transitions(
    order: list[str], by_key: dict[str, SongRecord], pair_score: PairScoreMap
) -> list[Transition]:
    transitions: list[Transition] = []
    for index in range(len(order) - 1):
        pair = pair_score.get((order[index], order[index + 1]))
        transitions.append(
            {
                "from": song_ref(by_key[order[index]]),
                "to": song_ref(by_key[order[index + 1]]),
                "score": pair["score"] if pair else 0.0,
                "transpose_to_by": pair["transpose_right_by"] if pair else 0,
            }
        )
    return transitions


def song_key(song: SongRecord) -> str:
    return f"{song.get('artist')}::{song.get('title')}::{song.get('url')}"


def ref_key(ref: SongRecord) -> str:
    return f"{ref.get('artist')}::{ref.get('title')}::{ref.get('url')}"


def reverse_pair(pair: PairScore) -> PairScore:
    return {
        **pair,
        "left": pair["right"],
        "right": pair["left"],
        "transpose_right_by": -pair.get("transpose_right_by", 0),
    }


def transition_score(pair_score: PairScoreMap, current: str, candidate: str) -> float:
    pair = pair_score.get((current, candidate))
    return pair["score"] if pair else 0.0


def best_transpose(left: list[int], right: list[int]) -> int:
    if not left or not right:
        return 0
    best_shift = 0
    best_score = -1.0
    for shift in range(-6, 6):
        shifted = [((pitch + shift) % 12) for pitch in right]
        score = sequence_score(left, shifted)
        if score > best_score:
            best_score = score
            best_shift = shift
    return best_shift


def build_output(
    songs: list[SongRecord], top: int, target_root: str, sort: str = "transition"
) -> ComparisonOutput:
    normalized = [normalize_song(song) for song in songs]
    pairs = pairwise_scores(normalized)
    if sort == "favorites":
        favorites_order = sorted(normalized, key=lambda song: -(song.get("favorites_count") or 0))
        medley = ordered_medley(favorites_order, target_root)
    elif sort == "chord_match":
        medley = last_chord_medley(normalized, target_root)
    else:
        medley = medley_order(normalized, pairs, target_root)
    return {
        "song_count": len(normalized),
        "top_pairs": pairs[:top],
        "medley": medley,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare first-chorus chord progressions and propose a medley order."
    )
    parser.add_argument("input", help="JSON generated by ug_explore_scrape.py")
    parser.add_argument("-o", "--output", default="medley_candidates.json")
    parser.add_argument("--top", type=int, default=25, help="Number of top pairs to keep")
    parser.add_argument(
        "--source-url", default=None, help="Only compare DB songs seen in this source URL"
    )
    parser.add_argument(
        "--target-root",
        default="C",
        choices=sorted(PITCH_CLASSES),
        help="Root for rendered medley chords",
    )
    args = parser.parse_args()

    output = build_output(load_songs(Path(args.input), args.source_url), args.top, args.target_root)
    Path(args.output).write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"song_count": output["song_count"], "top_pairs": len(output["top_pairs"])}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
