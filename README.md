# nba-injury-feed

Copies of the NBA's public official injury reports (the 15-minute PDFs and a text version of each), fetched on a schedule for a private stats-only model.
Nothing else is stored here.

## Layout

- `.github/workflows/fetch.yml`: runs `fetch.py` every 15 minutes from about 11 AM to 1 AM ET (and by hand from the Actions tab), then commits only if something changed.
- `fetch.py`: tries the four latest quarter-hour report links (each at least 10 minutes old, US Eastern clock) and any link in `backfill.txt` not yet saved. A report is kept only when the NBA's server answers 200 with a PDF.
- `reports/YYYY-MM-DD/Injury-Report_….pdf`: the PDFs, deleted after 3 days.
- `text/YYYY-MM-DD/Injury-Report_….txt`: the text of each report, kept. CSV columns `report_date, report_hour_et, url, page, line_no, text`, one row per line of each page (pypdf), the same layout the model's own report-history file uses.
- `attempts.csv`: every request (utc, url, status, bytes, saved).
