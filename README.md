# Medleys

Tools for scraping Ultimate Guitar Explore pages, storing chorus chords in a local JSON DB, comparing chorus similarity, and rendering a top-20 medley HTML report.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

## Local Web App

Start the Flask app:

```bash
.venv/bin/python app.py
```

Open:

```text
http://127.0.0.1:5000
```

The web app can analyze an Ultimate Guitar Explore URL or a saved Explore HTML page. URL analysis may open a visible Chrome window because Ultimate Guitar blocks headless browser access. Uploaded Explore HTML is parsed locally for tab links, then missing song pages are scraped and merged into `data/songs_db.json`.

After each job completes, open the job's medley link to render a medley using only songs from that URL or upload source. The Songs page browses the stored DB and links to per-song chord/source details.

## Update the Song DB

Use `update_song_db.py` with any Ultimate Guitar Explore URL. Songs already in `data/songs_db.json` are skipped unless `--refresh` is passed.

```bash
.venv/bin/python update_song_db.py \
  --url 'https://www.ultimate-guitar.com/explore?country_chart=1&order=hitstotal_desc' \
  --db data/songs_db.json \
  --delay-ms 2000
```

This writes/updates:

```text
data/songs_db.json
```

The DB stores each song by URL, including title, artist, chorus chords, source Explore URLs, scrape timestamps, and errors.

## Compare Songs From One Explore Page

To build a medley only from songs listed in a specific Explore URL, filter by that same URL:

```bash
.venv/bin/python compare_choruses.py data/songs_db.json \
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
.venv/bin/python render_medley_html.py output/medley_candidates_country_hits_only.json \
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
.venv/bin/python update_song_db.py \
  --url 'https://www.ultimate-guitar.com/explore?country_chart=1&order=hitstotal_desc' \
  --db data/songs_db.json \
  --delay-ms 2000

.venv/bin/python compare_choruses.py data/songs_db.json \
  --source-url 'https://www.ultimate-guitar.com/explore?country_chart=1&order=hitstotal_desc' \
  -o output/medley_candidates_country_hits_only.json \
  --top 50

.venv/bin/python render_medley_html.py output/medley_candidates_country_hits_only.json \
  -o output/medley_top20_country_hits_only.html \
  --limit 20
```

## Useful Options

```bash
# Re-scrape songs even if they already exist in the DB
.venv/bin/python update_song_db.py --refresh --url '<explore-url>' --db data/songs_db.json

# Test with fewer Explore songs
.venv/bin/python update_song_db.py --limit 10 --url '<explore-url>' --db data/songs_db.json

# Render more or fewer songs
.venv/bin/python render_medley_html.py output/medley_candidates_country_hits_only.json --limit 30
```
