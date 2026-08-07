#!/usr/bin/env python3
from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for

from medleys.chords import PITCH_CLASSES, transpose_chords
from medleys.comparison import build_output, canonical_song_key, load_songs
from medleys.database import (
    SongDatabase,
    SongRecord,
    db_songs_as_list,
    db_urls,
    load_db,
    mark_seen,
    merge_song,
    record_failure,
    save_db,
    save_medley,
)
from medleys.services.update_db import update_db
from medleys.ultimate_guitar.explore import scrape_song_with_retry
from medleys.ultimate_guitar.group_search import build_group_search_url, discover_group_songs
from medleys.web.formatting import (
    build_chord_line,
    build_whatsapp_text,
    decode_url,
    encode_url,
    export_filename,
    format_chorus_lines,
    normalize_tab_url,
    parse_tab_urls,
    source_label,
    tab_lines_as_text,
)

__all__ = [
    "app",
    "build_chord_line",
    "build_whatsapp_text",
    "decode_url",
    "encode_url",
    "export_filename",
    "format_chorus_lines",
    "normalize_tab_url",
    "parse_tab_urls",
    "tab_lines_as_text",
]

WEB_DIR = Path(__file__).resolve().parent
PROJECT_DIR = WEB_DIR.parents[2]
LOCALES_DIR = WEB_DIR / "locales"
DB_PATH = PROJECT_DIR / "data" / "songs_db.json"
DEFAULT_DELAY_MS = 2000
DEFAULT_TARGET_ROOT = "C"
DEFAULT_MEDLEY_SORT = "transition"
MEDLEY_SORTS = {"transition", "favorites", "chord_match"}
DEFAULT_SONGS_SORT = "alpha"
SONGS_SORTS = {"alpha", "favorites"}
DEFAULT_MEDLEYS_SORT = "created_at"
DEFAULT_MEDLEYS_DIRECTION = "desc"
MEDLEYS_SORTS = {"name", "created_at", "updated_at", "total", "eligible", "skipped"}
SORT_DIRECTIONS = {"asc", "desc"}
TOP_PAIR_COUNT = 50
MEDLEY_LIMIT = 20
GROUP_SEARCH_LIMIT = 50
DEFAULT_LANG = "ca"
SUPPORTED_LANGUAGES = {
    "ca": "Català",
    "es": "Español",
}
DynamicRecord = dict[str, Any]
JobRecord = dict[str, Any]
ProgressCallback = Callable[..., None]


def load_translations(locales_dir: Path = LOCALES_DIR) -> dict[str, dict[str, str]]:
    translations: dict[str, dict[str, str]] = {}
    for lang in SUPPORTED_LANGUAGES:
        with (locales_dir / f"{lang}.json").open(encoding="utf-8") as locale_file:
            translations[lang] = json.load(locale_file)
    return translations


TRANSLATIONS = load_translations()

app = Flask(__name__)
jobs: dict[str, JobRecord] = {}
jobs_lock = threading.Lock()


class ExploreLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[SongRecord] = []
        self.current: SongRecord | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href and "/tab/" in href:
            self.current = {"url": normalize_tab_url(href), "text": []}

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current is not None:
            title = " ".join("".join(self.current["text"]).split())
            self.links.append({"url": self.current["url"], "explore_title": title})
            self.current = None


@app.template_filter("song_url")
def song_url_filter(value: str) -> str:
    return encode_url(value)


@app.template_filter("source_label")
def source_label_filter(value: str) -> str:
    return source_label(value)


@app.context_processor
def template_context() -> dict[str, Any]:
    lang = current_lang()

    def translate(key: str) -> str:
        return TRANSLATIONS[lang].get(key, key)

    def lang_url(endpoint: str, **values: Any) -> str:
        values.setdefault("lang", lang)
        return url_for(endpoint, **values)

    def status_text(status: str) -> str:
        return translate(f"status_{status}")

    def kind_text(kind: str) -> str:
        return translate(f"kind_{kind}")

    return {
        "t": translate,
        "current_lang": lang,
        "languages": SUPPORTED_LANGUAGES,
        "lang_url": lang_url,
        "status_text": status_text,
        "kind_text": kind_text,
    }


def current_lang() -> str:
    lang = request.args.get("lang", DEFAULT_LANG)
    return lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANG


def localized_transpose_label(shift: int, lang: str) -> str:
    labels = TRANSLATIONS[lang]
    if shift == 0:
        return labels["no_transpose"]
    amount = abs(shift)
    direction = labels["transpose_up"] if shift > 0 else labels["transpose_down"]
    semitone = labels["semitone_one"] if amount == 1 else labels["semitone_other"]
    return f"{direction} {amount} {semitone}"


def build_medley_context(source_id: str) -> DynamicRecord:
    target_root = request.args.get("target_root", DEFAULT_TARGET_ROOT)
    if target_root not in PITCH_CLASSES:
        target_root = DEFAULT_TARGET_ROOT
    limit = parse_int(request.args.get("limit"), MEDLEY_LIMIT)
    show_original = request.args.get("show_original", "0") != "0"
    sort = request.args.get("sort", DEFAULT_MEDLEY_SORT)
    if sort not in MEDLEY_SORTS:
        sort = DEFAULT_MEDLEY_SORT
    songs = source_songs(source_id)
    exclusions = comparison_exclusions(source_id, songs)
    output = build_output(songs, TOP_PAIR_COUNT, target_root, sort=sort)
    medley_songs = []
    for song in output["medley"]["songs"][:limit]:
        shift = song.get("global_transpose_by", 0)
        medley_songs.append(
            {
                **song,
                "medley_chords": transpose_chords(song["chorus_chords"], shift),
                "transpose_label": localized_transpose_label(shift, current_lang()),
                "original_tab_lines": format_chorus_lines(song.get("chorus_lines", [])),
                "medley_tab_lines": format_chorus_lines(song.get("chorus_lines", []), shift),
            }
        )
    transitions = output["medley"]["transitions"][: max(0, limit - 1)]
    return {
        "source_id": source_id,
        "output": output,
        "songs": medley_songs,
        "transitions": transitions,
        "limit": limit,
        "target_root": target_root,
        "target_roots": sorted(PITCH_CLASSES),
        "show_original": show_original,
        "sort": sort,
        "exclusions": exclusions,
    }


def create_job(kind: str, source_id: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "kind": kind,
            "source_id": source_id,
            "status": "queued",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "summary": None,
            "error": None,
            "total": None,
            "processed": 0,
            "skipped": 0,
            "scraped": 0,
            "failed": 0,
        }
    return job_id


def update_job(job_id: str, **fields: Any) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job.update(fields)
        job["updated_at"] = now_iso()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_background(job_id: str, target: Callable[..., Any], *args: Any) -> None:
    thread = threading.Thread(target=run_job, args=(job_id, target, args), daemon=True)
    thread.start()


def run_job(job_id: str, target: Callable[..., Any], args: tuple[Any, ...]) -> None:
    update_job(job_id, status="running")
    try:
        summary = target(*args, progress=lambda **fields: update_job(job_id, **fields))
        update_job(job_id, status="complete", summary=summary)
    except Exception as exc:
        update_job(job_id, status="failed", error=str(exc))


def analyze_url(
    source_url: str,
    limit: int | None,
    delay_ms: int,
    refresh: bool,
    progress: ProgressCallback | None = None,
) -> DynamicRecord:
    return update_db(DB_PATH, source_url, limit, delay_ms, refresh, progress)


def analyze_upload(
    html_text: str,
    source_id: str,
    limit: int | None,
    delay_ms: int,
    refresh: bool,
    progress: ProgressCallback | None = None,
) -> DynamicRecord:
    discovered = extract_uploaded_links(html_text, limit)
    return analyze_songs(discovered, source_id, delay_ms, refresh, progress)


def analyze_url_list(
    urls: list[str],
    source_id: str,
    delay_ms: int,
    refresh: bool,
    progress: ProgressCallback | None = None,
) -> DynamicRecord:
    discovered = [
        {
            "explore_rank": index,
            "explore_title": url.rsplit("/", 1)[-1],
            "url": url,
        }
        for index, url in enumerate(urls, 1)
    ]
    return analyze_songs(discovered, source_id, delay_ms, refresh, progress)


def analyze_group(
    group: str,
    source_id: str,
    limit: int,
    delay_ms: int,
    refresh: bool,
    progress: ProgressCallback | None = None,
) -> DynamicRecord:
    discovered = discover_group_songs(group, limit, delay_ms)
    store_medley(source_id, group, [song["url"] for song in discovered])
    summary = analyze_songs(discovered, source_id, delay_ms, refresh, progress)
    summary["search_url"] = build_group_search_url(group)
    return summary


def analyze_songs(
    discovered: list[SongRecord],
    source_id: str,
    delay_ms: int,
    refresh: bool,
    progress: ProgressCallback | None = None,
) -> DynamicRecord:
    from playwright.sync_api import sync_playwright

    db = load_db(DB_PATH)
    known_urls = db_urls(db)
    skipped = []
    scraped = []
    failures = []

    if progress:
        progress(total=len(discovered), processed=0, skipped=0, scraped=0, failed=0)

    with sync_playwright() as playwright:
        for song in discovered:
            if not refresh and song["url"] in known_urls:
                mark_seen(db, song, source_id)
                skipped.append(song)
                if progress:
                    progress(
                        total=len(discovered),
                        processed=len(skipped) + len(scraped) + len(failures),
                        skipped=len(skipped),
                        scraped=len(scraped),
                        failed=len(failures),
                    )
                continue
            try:
                scraped_song = scrape_song_with_retry(playwright, song, delay_ms)
                merge_song(db, scraped_song, source_id)
                scraped.append(scraped_song)
                known_urls.add(song["url"])
            except Exception as exc:
                error = str(exc)
                record_failure(db, song, source_id, error)
                failures.append({**song, "error": error})
            if progress:
                progress(
                    total=len(discovered),
                    processed=len(skipped) + len(scraped) + len(failures),
                    skipped=len(skipped),
                    scraped=len(scraped),
                    failed=len(failures),
                )

    save_db(DB_PATH, db)
    eligible_count = sum(
        1
        for song in db["songs"].values()
        if source_id in song.get("sources", []) and song.get("chorus_chords")
    )
    return {
        "db": str(DB_PATH),
        "source": source_id,
        "discovered_count": len(discovered),
        "skipped_count": len(skipped),
        "scraped_count": len(scraped),
        "failure_count": len(failures),
        "eligible_count": eligible_count,
        "total_db_songs": len(db["songs"]),
        "failures": failures,
    }


def extract_uploaded_links(html_text: str, limit: int | None) -> list[SongRecord]:
    parser = ExploreLinkParser()
    parser.feed(html_text)
    seen = set()
    songs: list[SongRecord] = []
    for link in parser.links:
        url = link["url"]
        if url in seen:
            continue
        seen.add(url)
        songs.append(
            {
                "explore_rank": len(songs) + 1,
                "explore_title": link["explore_title"] or url.rsplit("/", 1)[-1],
                "url": url,
            }
        )
        if limit and len(songs) >= limit:
            break
    return songs


def db_stats() -> DynamicRecord:
    db = load_db(DB_PATH)
    songs = db_songs_as_list(db)
    sources = {source for song in songs for source in song.get("sources", [])} | set(
        db.get("medleys", {})
    )
    return {
        "song_count": len(songs),
        "chorus_count": sum(1 for song in songs if song.get("chorus_chords")),
        "source_count": len(sources),
        "updated_at": db.get("updated_at"),
        "sources": sorted(sources),
    }


def sorted_songs(sort: str = DEFAULT_SONGS_SORT) -> list[SongRecord]:
    songs = db_songs_as_list(load_db(DB_PATH))
    if sort == "favorites":
        return sorted(
            songs,
            key=lambda song: (
                -(song.get("favorites_count") or 0),
                (song.get("artist") or "").casefold(),
                (song.get("title") or "").casefold(),
            ),
        )
    return sorted(
        songs,
        key=lambda song: (
            (song.get("artist") or "").casefold(),
            (song.get("title") or "").casefold(),
        ),
    )


def source_songs(source_id: str) -> list[SongRecord]:
    return load_songs(DB_PATH, source_id)


def comparison_exclusions(
    source_id: str, comparable_songs: list[SongRecord] | None = None
) -> list[SongRecord]:
    source_entries = [
        song for song in db_songs_as_list(load_db(DB_PATH)) if source_id in song.get("sources", [])
    ]
    comparable_songs = comparable_songs if comparable_songs is not None else source_songs(source_id)
    comparable_urls = {song.get("url") for song in comparable_songs}
    comparable_keys = {canonical_song_key(song) for song in comparable_songs}
    exclusions = []
    for song in source_entries:
        if song.get("url") in comparable_urls:
            continue
        if song.get("errors") and not song.get("chorus_chords"):
            reason = "scrape_failed"
            detail = song["errors"][-1].get("error")
        elif not song.get("chorus_chords"):
            reason = "no_chorus"
            detail = None
        elif canonical_song_key(song) in comparable_keys:
            reason = "duplicate"
            detail = None
        else:
            continue
        exclusions.append({**song, "exclusion_reason": reason, "exclusion_detail": detail})
    return sorted(exclusions, key=lambda song: song.get("explore_rank") or 999999)


def delete_source(source_id: str) -> None:
    db = load_db(DB_PATH)
    for song in db.get("songs", {}).values():
        song["sources"] = [source for source in song.get("sources", []) if source != source_id]
    db.get("medleys", {}).pop(source_id, None)
    save_db(DB_PATH, db)


def store_medley(source_id: str, name: str, urls: list[str]) -> None:
    db = load_db(DB_PATH)
    kept_urls = set(urls)
    for url, song in db.get("songs", {}).items():
        if url not in kept_urls:
            song["sources"] = [source for source in song.get("sources", []) if source != source_id]
    save_medley(db, source_id, name, urls)
    save_db(DB_PATH, db)


def medley_definition(db: SongDatabase, source_id: str) -> DynamicRecord | None:
    definition = db.get("medleys", {}).get(source_id)
    if definition:
        return cast(DynamicRecord, definition)
    if not source_id.startswith("list:"):
        return None
    songs = sorted(
        (song for song in db_songs_as_list(db) if source_id in song.get("sources", [])),
        key=lambda song: song.get("explore_rank") or 999999,
    )
    urls = [song["url"] for song in songs if song.get("url")]
    if not urls:
        return None
    return {"name": source_id.split(":", 2)[-1], "urls": urls}


def medley_overview_rows() -> list[DynamicRecord]:
    db = load_db(DB_PATH)
    songs_by_url = db.get("songs", {})
    sources = {
        source for song in songs_by_url.values() for source in song.get("sources", [])
    } | set(db.get("medleys", {}))

    rows = []
    for source_id in sources:
        definition = medley_definition(db, source_id)
        if not definition:
            continue
        urls = definition.get("urls", [])
        eligible = len(load_songs(DB_PATH, source_id))
        no_chorus = scrape_failed = missing = duplicate = 0
        seen_keys: set[str] = set()
        for url in urls:
            song = songs_by_url.get(url)
            if not song or source_id not in song.get("sources", []):
                missing += 1
                continue
            if song.get("errors") and not song.get("chorus_chords"):
                scrape_failed += 1
            elif not song.get("chorus_chords"):
                no_chorus += 1
            else:
                key = canonical_song_key(song)
                if key in seen_keys:
                    duplicate += 1
                else:
                    seen_keys.add(key)
        rows.append(
            {
                "source_id": source_id,
                "name": definition.get("name", source_id),
                "created_at": definition.get("created_at"),
                "updated_at": definition.get("updated_at"),
                "total": len(urls),
                "eligible": eligible,
                "skipped": len(urls) - eligible,
                "no_chorus": no_chorus,
                "scrape_failed": scrape_failed,
                "missing": missing,
                "duplicate": duplicate,
            }
        )
    rows.sort(key=lambda row: row["created_at"] or "", reverse=True)
    return rows


def sort_medley_overview_rows(
    rows: list[DynamicRecord], sort: str, direction: str
) -> list[DynamicRecord]:
    reverse = direction == "desc"
    if sort == "name":
        return sorted(rows, key=lambda row: str(row["name"]).casefold(), reverse=reverse)
    if sort in {"total", "eligible", "skipped"}:
        return sorted(rows, key=lambda row: int(row[sort]), reverse=reverse)
    return sorted(rows, key=lambda row: row[sort] or "", reverse=reverse)


@app.get("/")
def index() -> Any:
    recent_jobs = sorted(snapshot_jobs(), key=lambda job: job["created_at"], reverse=True)[:8]
    return render_template("index.html", stats=db_stats(), jobs=recent_jobs)


@app.get("/medleys")
def medleys_overview() -> Any:
    rows = medley_overview_rows()
    sort = request.args.get("sort", DEFAULT_MEDLEYS_SORT)
    direction = request.args.get("direction", DEFAULT_MEDLEYS_DIRECTION)
    if sort not in MEDLEYS_SORTS:
        sort = DEFAULT_MEDLEYS_SORT
        direction = DEFAULT_MEDLEYS_DIRECTION
    elif direction not in SORT_DIRECTIONS:
        direction = DEFAULT_MEDLEYS_DIRECTION
    rows = sort_medley_overview_rows(rows, sort, direction)
    totals = {
        "total": sum(row["total"] for row in rows),
        "eligible": sum(row["eligible"] for row in rows),
        "skipped": sum(row["skipped"] for row in rows),
    }
    return render_template(
        "medleys.html", medleys=rows, totals=totals, sort=sort, direction=direction
    )


@app.post("/analyze/url")
def analyze_url_route() -> Any:
    source_url = request.form.get("source_url", "").strip()
    if not source_url:
        abort(400, "source_url is required")
    limit = parse_optional_int(request.form.get("limit"))
    delay_ms = parse_int(request.form.get("delay_ms"), DEFAULT_DELAY_MS)
    refresh = request.form.get("refresh") == "on"
    job_id = create_job("url", source_url)
    run_background(job_id, analyze_url, source_url, limit, delay_ms, refresh)
    return redirect(url_for("job_detail", job_id=job_id, lang=current_lang()))


@app.post("/analyze/upload")
def analyze_upload_route() -> Any:
    uploaded = request.files.get("explore_html")
    if not uploaded or not uploaded.filename:
        abort(400, "explore_html is required")
    html_text = uploaded.read().decode("utf-8", errors="replace")
    source_id = f"upload:{uuid.uuid4().hex[:8]}:{Path(uploaded.filename).name}"
    limit = parse_optional_int(request.form.get("limit"))
    delay_ms = parse_int(request.form.get("delay_ms"), DEFAULT_DELAY_MS)
    refresh = request.form.get("refresh") == "on"
    job_id = create_job("upload", source_id)
    run_background(job_id, analyze_upload, html_text, source_id, limit, delay_ms, refresh)
    return redirect(url_for("job_detail", job_id=job_id, lang=current_lang()))


@app.post("/analyze/url-list")
def analyze_url_list_route() -> Any:
    try:
        urls = parse_tab_urls(request.form.get("tab_urls", ""))
    except ValueError as exc:
        abort(400, str(exc))
    if not urls:
        abort(400, "At least one tab URL is required")
    name = request.form.get("medley_name", "").strip()[:80] or f"URL list {uuid.uuid4().hex[:8]}"
    source_id = f"list:{uuid.uuid4().hex[:8]}:{name}"
    delay_ms = parse_int(request.form.get("delay_ms"), DEFAULT_DELAY_MS)
    refresh = request.form.get("refresh") == "on"
    store_medley(source_id, name, urls)
    job_id = create_job("url_list", source_id)
    run_background(job_id, analyze_url_list, urls, source_id, delay_ms, refresh)
    return redirect(url_for("job_detail", job_id=job_id, lang=current_lang()))


@app.post("/analyze/group")
def analyze_group_route() -> Any:
    group = request.form.get("group", "").strip()[:80]
    if not group:
        abort(400, "group is required")
    limit = max(1, min(parse_int(request.form.get("limit"), GROUP_SEARCH_LIMIT), 50))
    delay_ms = parse_int(request.form.get("delay_ms"), DEFAULT_DELAY_MS)
    refresh = request.form.get("refresh") == "on"
    source_id = f"list:{uuid.uuid4().hex[:8]}:{group}"
    job_id = create_job("group", source_id)
    run_background(job_id, analyze_group, group, source_id, limit, delay_ms, refresh)
    return redirect(url_for("job_detail", job_id=job_id, lang=current_lang()))


@app.get("/jobs/<job_id>")
def job_detail(job_id: str) -> Any:
    job = get_job(job_id)
    if not job:
        abort(404)
    return render_template("job.html", job=job)


@app.get("/medley/<path:source_id>/export.html")
def medley_export(source_id: str) -> Any:
    context = build_medley_context(source_id)
    html = render_template("medley_export.html", **context)
    filename = export_filename(source_id, context["target_root"])
    return Response(
        html,
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.get("/medley/<path:source_id>")
def medley(source_id: str) -> Any:
    context = build_medley_context(source_id)
    target_root = context["target_root"]
    medley_songs = context["songs"]
    show_original = context["show_original"]
    whatsapp_text = build_whatsapp_text(source_id, medley_songs, target_root, show_original)
    return render_template(
        "medley.html",
        **context,
        whatsapp_url=f"https://wa.me/?text={quote(whatsapp_text)}",
        export_url=url_for(
            "medley_export",
            source_id=source_id,
            lang=current_lang(),
            target_root=target_root,
            limit=context["limit"],
            show_original="1" if show_original else "0",
            sort=context["sort"],
        ),
        export_filename=export_filename(source_id, target_root),
    )


@app.route("/medley/<path:source_id>/edit", methods=["GET", "POST"])
def edit_medley(source_id: str) -> Any:
    db = load_db(DB_PATH)
    definition = medley_definition(db, source_id)
    if not definition:
        abort(404)
    if request.method == "GET":
        songs = [
            {
                "url": url,
                **{
                    key: db["songs"].get(url, {}).get(key)
                    for key in ("artist", "title", "explore_title", "favorites_count")
                },
            }
            for url in definition["urls"]
        ]
        return render_template(
            "edit_medley.html", source_id=source_id, medley=definition, songs=songs
        )

    try:
        urls = parse_tab_urls(request.form.get("tab_urls", ""))
    except ValueError as exc:
        abort(400, str(exc))
    if not urls:
        abort(400, "At least one tab URL is required")
    name = request.form.get("medley_name", "").strip()[:80] or definition["name"]
    delay_ms = parse_int(request.form.get("delay_ms"), DEFAULT_DELAY_MS)
    refresh = request.form.get("refresh") == "on"
    store_medley(source_id, name, urls)
    job_id = create_job("url_list", source_id)
    run_background(job_id, analyze_url_list, urls, source_id, delay_ms, refresh)
    return redirect(url_for("job_detail", job_id=job_id, lang=current_lang()))


@app.post("/medley/<path:source_id>/delete")
def delete_medley(source_id: str) -> Any:
    delete_source(source_id)
    return redirect(url_for("index", lang=current_lang()))


@app.get("/songs")
def songs() -> Any:
    query = request.args.get("q", "").strip().casefold()
    sort = request.args.get("sort", DEFAULT_SONGS_SORT)
    if sort not in SONGS_SORTS:
        sort = DEFAULT_SONGS_SORT
    songs_list = sorted_songs(sort)
    if query:
        songs_list = [
            song
            for song in songs_list
            if query
            in " ".join(
                [song.get("artist") or "", song.get("title") or "", song.get("url") or ""]
            ).casefold()
        ]
    return render_template(
        "songs.html", songs=songs_list, query=request.args.get("q", "").strip(), sort=sort
    )


@app.get("/api/songs")
def search_songs_api() -> Any:
    query = request.args.get("q", "").strip().casefold()
    songs_list = sorted_songs()
    if query:
        songs_list = [
            song
            for song in songs_list
            if query
            in " ".join(
                str(song.get(key) or "") for key in ("artist", "title", "explore_title", "url")
            ).casefold()
        ]
    return jsonify(
        {
            "songs": [
                {
                    key: song.get(key)
                    for key in ("url", "artist", "title", "explore_title", "favorites_count")
                }
                for song in songs_list[:20]
                if song.get("url")
            ]
        }
    )


@app.get("/songs/<encoded_url>")
def song_detail(encoded_url: str) -> Any:
    url = decode_url(encoded_url)
    song = load_db(DB_PATH).get("songs", {}).get(url)
    if not song:
        abort(404)
    return render_template(
        "song.html", song={**song, "tab_lines": format_chorus_lines(song.get("chorus_lines", []))}
    )


def parse_optional_int(value: str | None) -> int | None:
    if not value:
        return None
    return int(value)


def parse_int(value: str | None, default: int) -> int:
    if not value:
        return default
    return int(value)


def snapshot_jobs() -> list[JobRecord]:
    with jobs_lock:
        return [job.copy() for job in jobs.values()]


def get_job(job_id: str) -> JobRecord | None:
    with jobs_lock:
        job = jobs.get(job_id)
        return job.copy() if job else None


def main() -> None:
    app.run(host="0.0.0.0", debug=True, port=5001, use_reloader=False)


if __name__ == "__main__":
    main()
