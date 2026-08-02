from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from medleys.chords import transpose_chords
from medleys.database import SongRecord

DynamicRecord = dict[str, Any]


def normalize_tab_url(url: str) -> str:
    if url.startswith("//"):
        return f"https:{url}"
    return urljoin("https://www.ultimate-guitar.com", url)


def encode_url(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def decode_url(value: str) -> str:
    padded = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


def source_label(value: str) -> str:
    if value.startswith(("upload:", "list:")):
        return value.rsplit(":", 1)[-1]
    return value


def format_chorus_lines(lines: list[DynamicRecord], shift: int = 0) -> list[DynamicRecord]:
    formatted: list[DynamicRecord] = []
    for line in lines or []:
        chords = line.get("chords", [])
        symbols = [chord["symbol"] for chord in chords]
        shifted_symbols = transpose_chords(symbols, shift)
        chord_line = build_chord_line(line.get("lyrics", ""), chords, shifted_symbols)
        formatted.append({"chords": chord_line, "lyrics": line.get("lyrics", "")})
    return formatted


def build_chord_line(lyrics: str, chords: list[DynamicRecord], symbols: list[str]) -> str:
    if len(chords) != len(symbols):
        raise ValueError("chords and symbols must have the same length")
    width = len(lyrics)
    for chord, symbol in zip(chords, symbols):  # noqa: B905
        width = max(width, int(chord.get("position", 0)) + len(symbol))
    chars = [" "] * width
    for chord, symbol in zip(chords, symbols):  # noqa: B905
        position = int(chord.get("position", 0))
        for index, char in enumerate(symbol):
            target = position + index
            if target < len(chars):
                chars[target] = char
    return "".join(chars).rstrip()


def build_whatsapp_text(
    source_id: str, songs: list[SongRecord], target_root: str, show_original: bool
) -> str:
    lines = [f"Medley - {source_label(source_id)}", f"Target root: {target_root}", ""]
    for index, song in enumerate(songs, 1):
        lines.append(f"{index}. {song.get('artist')} - {song.get('title')}")
        if show_original and song.get("original_tab_lines"):
            lines.append("Original:")
            lines.extend(tab_lines_as_text(song["original_tab_lines"]))
        lines.append(f"Medley ({target_root}):")
        if song.get("medley_tab_lines"):
            lines.extend(tab_lines_as_text(song["medley_tab_lines"]))
        else:
            lines.append(" ".join(song.get("medley_chords", [])))
        lines.append("")
    return "\n".join(lines).strip()


def tab_lines_as_text(lines: list[DynamicRecord]) -> list[str]:
    text = []
    for line in lines:
        if line.get("chords"):
            text.append(line["chords"])
        if line.get("lyrics"):
            text.append(line["lyrics"])
    return text


def export_filename(source_id: str, target_root: str) -> str:
    label = source_label(source_id).rsplit("/", 1)[-1] or "medley"
    safe_label = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-").lower() or "medley"
    return f"medley-{safe_label}-{target_root}.html"


def parse_tab_urls(value: str) -> list[str]:
    urls = []
    seen = set()
    for line in value.splitlines():
        raw_url = line.strip()
        if not raw_url:
            continue
        url = normalize_tab_url(raw_url)
        parsed = urlparse(url)
        is_ug_host = parsed.hostname == "ultimate-guitar.com" or (parsed.hostname or "").endswith(
            ".ultimate-guitar.com"
        )
        if parsed.scheme not in {"http", "https"} or not is_ug_host:
            raise ValueError(f"Unsupported Ultimate Guitar URL: {raw_url}")
        if "/tab/" not in parsed.path:
            raise ValueError(f"Not an Ultimate Guitar tab URL: {raw_url}")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls
