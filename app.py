#!/usr/bin/env python3
from __future__ import annotations

import base64
import threading
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from flask import Flask, abort, redirect, render_template, request, url_for

from chord_utils import PITCH_CLASSES
from chord_utils import transpose_chords
from compare_choruses import build_output, load_songs
from song_db import db_songs_as_list, db_urls, load_db, mark_seen, merge_song, record_failure, save_db
from ug_explore_scrape import scrape_song_with_retry
from update_song_db import update_db


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "songs_db.json"
DEFAULT_DELAY_MS = 2000
DEFAULT_TARGET_ROOT = "C"
TOP_PAIR_COUNT = 50
MEDLEY_LIMIT = 20
DEFAULT_LANG = "ca"
SUPPORTED_LANGUAGES = {
    "ca": "Català",
    "es": "Español",
}
TRANSLATIONS = {
    "ca": {
        "app_name": "Medleys",
        "analyze_nav": "Analitza",
        "songs_nav": "Cançons",
        "language": "Idioma",
        "dashboard_title": "Anàlisi de medleys",
        "dashboard_intro": "Analitza una pàgina Explore d'Ultimate Guitar, actualitza la base de dades local i crea un medley limitat a aquesta font.",
        "songs_count": "Cançons",
        "chorus_count": "Amb acords de tornada",
        "source_count": "Fonts",
        "db_updated": "BD actualitzada",
        "never": "Mai",
        "analyze_url": "Analitza URL d'Explore",
        "visible_chrome_notice": "Pot obrir-se una finestra visible de Chrome mentre es carreguen pàgines d'Ultimate Guitar.",
        "explore_url": "URL d'Explore",
        "limit": "Límit",
        "all": "totes",
        "delay_ms": "Retard ms",
        "refresh_known": "Torna a analitzar cançons conegudes",
        "start_url_analysis": "Inicia anàlisi d'URL",
        "analyze_upload": "Analitza HTML d'Explore desat",
        "upload_notice": "La pujada es tracta com un llistat d'Explore; les pàgines de cançons que faltin encara s'analitzen.",
        "explore_html": "HTML d'Explore",
        "start_upload_analysis": "Inicia anàlisi de pujada",
        "stored_sources": "Fonts desades",
        "no_sources": "Encara no hi ha fonts.",
        "open_medley": "Obre medley",
        "recent_jobs": "Tasques recents",
        "status": "Estat",
        "source": "Font",
        "updated": "Actualitzada",
        "view": "Veure",
        "analysis_job": "Tasca d'anàlisi",
        "kind": "Tipus",
        "created": "Creada",
        "discovered": "Trobades",
        "skipped": "Omeses",
        "scraped": "Analitzades",
        "failures": "Errors",
        "db_songs": "Cançons a la BD",
        "open_source_medley": "Obre el medley de la font",
        "song": "Cançó",
        "error": "Error",
        "job_refresh": "La pàgina de la tasca es refresca mentre l'anàlisi està en marxa.",
        "source_medley": "Medley de la font",
        "comparable_songs": "Cançons comparables",
        "average_transition": "Transició mitjana",
        "target_root": "Tonalitat objectiu",
        "target_key": "Tonalitat objectiu",
        "show_original_choruses": "Mostra les tornades originals",
        "apply": "Aplica",
        "original_chorus": "Tornada original",
        "medley_chorus": "Tornada del medley en",
        "next_transition_score": "Puntuació de la transició següent",
        "end_of_list": "Final de la llista",
        "ug_source": "Font a Ultimate Guitar",
        "no_chorus_source": "No s'han trobat cançons amb acords de tornada per a aquesta font.",
        "songs_title": "Cançons",
        "stored_songs": "cançons desades",
        "matching": "que coincideixen amb",
        "search_placeholder": "Cerca artista, títol o URL",
        "search": "Cerca",
        "artist": "Artista",
        "title": "Títol",
        "chorus": "Tornada",
        "sources": "Fonts",
        "explore_title": "Títol d'Explore",
        "explore_rank": "Rànquing d'Explore",
        "first_seen": "Vista per primer cop",
        "last_seen": "Vista per últim cop",
        "last_scraped": "Analitzada per últim cop",
        "scrape_count": "Nombre d'anàlisis",
        "original_chorus_chords": "Acords de la tornada original",
        "no_chorus_stored": "No hi ha acords de tornada desats.",
        "errors": "Errors",
        "at": "A",
        "status_queued": "en cua",
        "status_running": "en marxa",
        "status_complete": "completada",
        "status_failed": "fallida",
        "kind_url": "URL",
        "kind_upload": "pujada",
        "no_transpose": "sense transposició",
        "transpose_up": "transposa amunt",
        "transpose_down": "transposa avall",
        "semitone_one": "semitò",
        "semitone_other": "semitons",
    },
    "es": {
        "app_name": "Medleys",
        "analyze_nav": "Analizar",
        "songs_nav": "Canciones",
        "language": "Idioma",
        "dashboard_title": "Análisis de medleys",
        "dashboard_intro": "Analiza una página Explore de Ultimate Guitar, actualiza la base de datos local y crea un medley limitado a esa fuente.",
        "songs_count": "Canciones",
        "chorus_count": "Con acordes de estribillo",
        "source_count": "Fuentes",
        "db_updated": "BD actualizada",
        "never": "Nunca",
        "analyze_url": "Analizar URL de Explore",
        "visible_chrome_notice": "Puede abrirse una ventana visible de Chrome mientras se cargan páginas de Ultimate Guitar.",
        "explore_url": "URL de Explore",
        "limit": "Límite",
        "all": "todas",
        "delay_ms": "Retardo ms",
        "refresh_known": "Volver a analizar canciones conocidas",
        "start_url_analysis": "Iniciar análisis de URL",
        "analyze_upload": "Analizar HTML de Explore guardado",
        "upload_notice": "La subida se trata como un listado de Explore; las páginas de canciones que falten todavía se analizan.",
        "explore_html": "HTML de Explore",
        "start_upload_analysis": "Iniciar análisis de subida",
        "stored_sources": "Fuentes guardadas",
        "no_sources": "Todavía no hay fuentes.",
        "open_medley": "Abrir medley",
        "recent_jobs": "Tareas recientes",
        "status": "Estado",
        "source": "Fuente",
        "updated": "Actualizada",
        "view": "Ver",
        "analysis_job": "Tarea de análisis",
        "kind": "Tipo",
        "created": "Creada",
        "discovered": "Encontradas",
        "skipped": "Omitidas",
        "scraped": "Analizadas",
        "failures": "Fallos",
        "db_songs": "Canciones en la BD",
        "open_source_medley": "Abrir el medley de la fuente",
        "song": "Canción",
        "error": "Error",
        "job_refresh": "La página de la tarea se actualiza mientras el análisis está en marcha.",
        "source_medley": "Medley de la fuente",
        "comparable_songs": "Canciones comparables",
        "average_transition": "Transición media",
        "target_root": "Tonalidad objetivo",
        "target_key": "Tonalidad objetivo",
        "show_original_choruses": "Mostrar los estribillos originales",
        "apply": "Aplicar",
        "original_chorus": "Estribillo original",
        "medley_chorus": "Estribillo del medley en",
        "next_transition_score": "Puntuación de la siguiente transición",
        "end_of_list": "Final de la lista",
        "ug_source": "Fuente en Ultimate Guitar",
        "no_chorus_source": "No se han encontrado canciones con acordes de estribillo para esta fuente.",
        "songs_title": "Canciones",
        "stored_songs": "canciones guardadas",
        "matching": "que coinciden con",
        "search_placeholder": "Buscar artista, título o URL",
        "search": "Buscar",
        "artist": "Artista",
        "title": "Título",
        "chorus": "Estribillo",
        "sources": "Fuentes",
        "explore_title": "Título de Explore",
        "explore_rank": "Ranking de Explore",
        "first_seen": "Vista por primera vez",
        "last_seen": "Vista por última vez",
        "last_scraped": "Analizada por última vez",
        "scrape_count": "Número de análisis",
        "original_chorus_chords": "Acordes del estribillo original",
        "no_chorus_stored": "No hay acordes de estribillo guardados.",
        "errors": "Errores",
        "at": "En",
        "status_queued": "en cola",
        "status_running": "en marcha",
        "status_complete": "completada",
        "status_failed": "fallida",
        "kind_url": "URL",
        "kind_upload": "subida",
        "no_transpose": "sin transposición",
        "transpose_up": "transponer arriba",
        "transpose_down": "transponer abajo",
        "semitone_one": "semitono",
        "semitone_other": "semitonos",
    },
}

app = Flask(__name__)
jobs = {}
jobs_lock = threading.Lock()


class ExploreLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href and "/tab/" in href:
            self.current = {"url": normalize_tab_url(href), "text": []}

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            title = " ".join("".join(self.current["text"]).split())
            self.links.append({"url": self.current["url"], "explore_title": title})
            self.current = None


def normalize_tab_url(url: str) -> str:
    if url.startswith("//"):
        return f"https:{url}"
    return urljoin("https://www.ultimate-guitar.com", url)


def encode_url(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def decode_url(value: str) -> str:
    padded = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


@app.template_filter("song_url")
def song_url_filter(value: str) -> str:
    return encode_url(value)


@app.template_filter("source_label")
def source_label_filter(value: str) -> str:
    if value.startswith("upload:"):
        return value.rsplit(":", 1)[-1]
    return value


@app.context_processor
def template_context() -> dict:
    lang = current_lang()

    def translate(key: str) -> str:
        return TRANSLATIONS[lang].get(key, key)

    def lang_url(endpoint: str, **values) -> str:
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
        }
    return job_id


def update_job(job_id: str, **fields) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job.update(fields)
        job["updated_at"] = now_iso()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_background(job_id: str, target, *args) -> None:
    thread = threading.Thread(target=run_job, args=(job_id, target, args), daemon=True)
    thread.start()


def run_job(job_id: str, target, args: tuple) -> None:
    update_job(job_id, status="running")
    try:
        summary = target(*args)
        update_job(job_id, status="complete", summary=summary)
    except Exception as exc:
        update_job(job_id, status="failed", error=str(exc))


def analyze_url(source_url: str, limit: int | None, delay_ms: int, refresh: bool) -> dict:
    return update_db(DB_PATH, source_url, limit, delay_ms, refresh)


def analyze_upload(html_text: str, source_id: str, limit: int | None, delay_ms: int, refresh: bool) -> dict:
    from playwright.sync_api import sync_playwright

    discovered = extract_uploaded_links(html_text, limit)
    db = load_db(DB_PATH)
    known_urls = db_urls(db)
    skipped = []
    scraped = []
    failures = []

    with sync_playwright() as playwright:
        for song in discovered:
            if not refresh and song["url"] in known_urls:
                mark_seen(db, song, source_id)
                skipped.append(song)
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

    save_db(DB_PATH, db)
    return {
        "db": str(DB_PATH),
        "source": source_id,
        "discovered_count": len(discovered),
        "skipped_count": len(skipped),
        "scraped_count": len(scraped),
        "failure_count": len(failures),
        "total_db_songs": len(db["songs"]),
        "failures": failures,
    }


def extract_uploaded_links(html_text: str, limit: int | None) -> list[dict]:
    parser = ExploreLinkParser()
    parser.feed(html_text)
    seen = set()
    songs = []
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


def db_stats() -> dict:
    db = load_db(DB_PATH)
    songs = db_songs_as_list(db)
    sources = {source for song in songs for source in song.get("sources", [])}
    return {
        "song_count": len(songs),
        "chorus_count": sum(1 for song in songs if song.get("chorus_chords")),
        "source_count": len(sources),
        "updated_at": db.get("updated_at"),
        "sources": sorted(sources),
    }


def sorted_songs() -> list[dict]:
    return sorted(
        db_songs_as_list(load_db(DB_PATH)),
        key=lambda song: ((song.get("artist") or "").casefold(), (song.get("title") or "").casefold()),
    )


def source_songs(source_id: str) -> list[dict]:
    return load_songs(DB_PATH, source_id)


@app.get("/")
def index():
    recent_jobs = sorted(snapshot_jobs(), key=lambda job: job["created_at"], reverse=True)[:8]
    return render_template("index.html", stats=db_stats(), jobs=recent_jobs)


@app.post("/analyze/url")
def analyze_url_route():
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
def analyze_upload_route():
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


@app.get("/jobs/<job_id>")
def job_detail(job_id: str):
    job = get_job(job_id)
    if not job:
        abort(404)
    return render_template("job.html", job=job)


@app.get("/medley/<path:source_id>")
def medley(source_id: str):
    target_root = request.args.get("target_root", DEFAULT_TARGET_ROOT)
    if target_root not in PITCH_CLASSES:
        target_root = DEFAULT_TARGET_ROOT
    limit = parse_int(request.args.get("limit"), MEDLEY_LIMIT)
    show_original = request.args.get("show_original", "1") != "0"
    songs = source_songs(source_id)
    output = build_output(songs, TOP_PAIR_COUNT, target_root)
    medley_songs = []
    for song in output["medley"]["songs"][:limit]:
        shift = song.get("global_transpose_by", 0)
        medley_songs.append(
            {
                **song,
                "medley_chords": transpose_chords(song["chorus_chords"], shift),
                "transpose_label": localized_transpose_label(shift, current_lang()),
            }
        )
    transitions = output["medley"]["transitions"][: max(0, limit - 1)]
    return render_template(
        "medley.html",
        source_id=source_id,
        output=output,
        songs=medley_songs,
        transitions=transitions,
        limit=limit,
        target_root=target_root,
        target_roots=sorted(PITCH_CLASSES),
        show_original=show_original,
    )


@app.get("/songs")
def songs():
    query = request.args.get("q", "").strip().casefold()
    songs_list = sorted_songs()
    if query:
        songs_list = [
            song
            for song in songs_list
            if query in " ".join([song.get("artist") or "", song.get("title") or "", song.get("url") or ""]).casefold()
        ]
    return render_template("songs.html", songs=songs_list, query=request.args.get("q", "").strip())


@app.get("/songs/<encoded_url>")
def song_detail(encoded_url: str):
    url = decode_url(encoded_url)
    song = load_db(DB_PATH).get("songs", {}).get(url)
    if not song:
        abort(404)
    return render_template("song.html", song=song)


def parse_optional_int(value: str | None) -> int | None:
    if not value:
        return None
    return int(value)


def parse_int(value: str | None, default: int) -> int:
    if not value:
        return default
    return int(value)


def snapshot_jobs() -> list[dict]:
    with jobs_lock:
        return [job.copy() for job in jobs.values()]


def get_job(job_id: str) -> dict | None:
    with jobs_lock:
        job = jobs.get(job_id)
        return job.copy() if job else None


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
