# Medleys

Tools for scraping Ultimate Guitar Explore pages, storing chorus chords in a local JSON DB, comparing chorus similarity, and rendering a top-20 medley HTML report.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .
.venv/bin/python -m playwright install chromium
```

## Local Web App

Start the Flask app:

```bash
.venv/bin/medleys-web
```

Open on this computer:

```text
http://127.0.0.1:5001
```

From another device on the same local network, replace `127.0.0.1` with this computer's
local IP address, for example `http://192.168.1.10:5001`.

The web app can analyze an Ultimate Guitar Explore URL or a saved Explore HTML page. URL analysis may open a visible Chrome window because Ultimate Guitar blocks headless browser access. Uploaded Explore HTML is parsed locally for tab links, then missing song pages are scraped and merged into `data/songs_db.json`.

After each job completes, open the job's medley link to render a medley using only songs from that URL or upload source. The Songs page browses the stored DB and links to per-song chord/source details.

## Update the Song DB

Use `medleys-update` with any Ultimate Guitar Explore URL. Songs already in `data/songs_db.json` are skipped unless `--refresh` is passed.

```bash
.venv/bin/medleys-update \
  --url 'https://www.ultimate-guitar.com/explore?country_chart=1&order=hitstotal_desc' \
  --db data/songs_db.json \
  --delay-ms 2000
```

This writes/updates:

```text
data/songs_db.json
```

The DB stores each song by URL, including title, artist, chorus chords, source Explore URLs, scrape timestamps, and errors.

## Chorus Detection

Each scraped song is passed through `detect_chorus()` (`src/medleys/ultimate_guitar/chorus_detection.py`), which fills in `chorus_chords`, `chorus_lines`, `has_chorus`, `chorus_detection`, and `chorus_confidence`.

**Explicit detection** looks for a heading that names a chorus-equivalent section: `Chorus`, `Chorus 1`/`Chorus 2`/etc., `Refrain`, `Hook`, `Estribillo`, or `Coro` (case-insensitive). Headings are recognized in `[Name]` bracket form as well as the unbracketed conventions some tabs use instead - `(Name)`, `Name:`, or a bare `Name` line, e.g. Spanish tabs' `Coro:` / `(Coro)`. `Pre-Chorus` is deliberately excluded. When found, `chorus_detection` is `"explicit"` and `chorus_confidence` is `1.0`.

**Inferred detection** runs only when no explicit heading exists. It parses the tab into blocks, normalizes lyric lines (lowercased, punctuation and tab/chord markup stripped, repetition annotations like `x2`/`(repeat)` removed), and searches for a lyric passage that repeats elsewhere in the song - first by exact match, then by conservative fuzzy matching (`difflib.SequenceMatcher`) so a single changed word or an extra repeated line in the final chorus still counts. A repeated chord progression under the matching lyrics adds supporting evidence but is never sufficient on its own. Each candidate is scored (0.0-1.0) using named signals - lyric repetition strength, chord support, occurrence count, candidate length, and position in the song - with penalties for generic filler, passages that cover most of the song, intro/outro-only repeats, and passages that are really just a repeated `Verse` section. Only a candidate at or above `INFERENCE_CONFIDENCE_THRESHOLD` (0.55) is accepted, and it is reported as `chorus_detection: "inferred"` with the candidate's score as `chorus_confidence`. When nothing clears the bar, `chorus_detection` is `"none"`, `chorus_confidence` is `0.0`, and the song is excluded from comparison (`has_chorus` is `false`).

Explicit headings always take precedence - inference never overrides or second-guesses a labelled chorus. The detector is fully deterministic (no LLM or external service call): given the same tab text it always returns the same result.

**Limitations:** inference is tuned to avoid false positives (an incorrect chorus would corrupt medley similarity scoring), so it is conservative and will report `"none"` for songs whose chorus isn't a clean, sufficiently-repeated lyric passage - for example a chorus that changes substantially between repeats, an instrumental-only "chorus", or a very short (one-line) unlabelled refrain. Detection is entirely per-song; it does not use any information from other songs in the database.

## Compare Songs From One Explore Page

To build a medley only from songs listed in a specific Explore URL, filter by that same URL:

```bash
.venv/bin/medleys-compare data/songs_db.json \
  --source-url 'https://www.ultimate-guitar.com/explore?country_chart=1&order=hitstotal_desc' \
  -o output/medley_candidates_country_hits_only.json \
  --top 50
```

This writes:

```text
output/medley_candidates_country_hits_only.json
```

The comparison uses chorus chords, chord intervals, chord qualities, chord overlap, and suggested transpose shifts.

## Render the HTML Report

```bash
.venv/bin/medleys-render output/medley_candidates_country_hits_only.json \
  -o output/medley_top20_country_hits_only.html \
  --limit 20
```

Open:

```text
output/medley_top20_country_hits_only.html
```

The report shows the ordered songs, chorus chords, transition score to the next song, transpose hint, and Ultimate Guitar source link.

## Full Current Workflow

```bash
.venv/bin/medleys-update \
  --url 'https://www.ultimate-guitar.com/explore?country_chart=1&order=hitstotal_desc' \
  --db data/songs_db.json \
  --delay-ms 2000

.venv/bin/medleys-compare data/songs_db.json \
  --source-url 'https://www.ultimate-guitar.com/explore?country_chart=1&order=hitstotal_desc' \
  -o output/medley_candidates_country_hits_only.json \
  --top 50

.venv/bin/medleys-render output/medley_candidates_country_hits_only.json \
  -o output/medley_top20_country_hits_only.html \
  --limit 20
```

## Useful Options

```bash
# Re-scrape songs even if they already exist in the DB
.venv/bin/medleys-update --refresh --url '<explore-url>' --db data/songs_db.json

# Test with fewer Explore songs
.venv/bin/medleys-update --limit 10 --url '<explore-url>' --db data/songs_db.json

# Render more or fewer songs
.venv/bin/medleys-render output/medley_candidates_country_hits_only.json --limit 30
```
