# nba-injury-feed

Copies of the NBA's public official injury reports (the 15-minute PDFs and a text version of each), fetched on a schedule for a private stats-only model.
Nothing else is stored here.

## Layout

- `.github/workflows/fetch.yml`: runs `fetch.py` every 15 minutes from about 11 AM to 1 AM ET (and by hand from the Actions tab), then commits only if something changed.
- `fetch.py`: tries the four latest quarter-hour report links (each at least 10 minutes old, US Eastern clock) and any link in `backfill.txt` not yet saved. A report is kept only when the NBA's server answers 200 with a PDF.
- `reports/YYYY-MM-DD/Injury-Report_….pdf`: the PDFs, deleted after 3 days.
- `text/YYYY-MM-DD/Injury-Report_….txt`: the text of each report, kept. CSV columns `report_date, report_hour_et, url, page, line_no, text`, one row per line of each page (pypdf), the same layout the model's own report-history file uses.
- `attempts.csv`: every request (utc, url, status, bytes, saved).

## Sportsbook Review snapshots (added Nov 24 2026)
`sbr_snap.py` saves the NBA spreads page for today and tomorrow about once an hour (`sbr/`), so each game's opening
line gets a first-seen time. Grading data only: it tells whether the opener came out before or after the model's
night price. It is never a model input. Raw pages are kept for 2 days; `sbr/snapshots.csv` keeps one row per game
per snapshot.
