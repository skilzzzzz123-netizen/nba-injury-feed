"""Archive of the NBA's official injury reports for PAST dates (2021-22 on), as text.

Added Nov 27 2026 (the user's go: "begin the replay with the free data"; NBA Edge plan of Nov 27, Phase 1a). The NBA
keeps every report on its server, so "who was listed, and when" can be replayed for past seasons. Development data for
the model's replay and its who-plays work; the reports are official availability information, not market information.

Run by .github/workflows/archive.yml in batches. For every date with NBA games (archive/games.csv) and the day before
it, the reports of 11:30 AM to 10:30 PM ET are requested, one per hour (the :30 report). File names changed over time,
so two forms are tried for each:
    Injury-Report_YYYY-MM-DD_HH_30PM.pdf    (15-minute era, from about early 2026)
    Injury-Report_YYYY-MM-DD_HHPM.pdf       (hourly era; the report's own header says HH:30)
A day without games of its own (only "the day before") gets the evening hours only (5:30 PM on).

Writes (archive/):
    reports/YYYY-MM-DD/HH30.csv.gz   the report's text, CSV: report_date, report_time_et, url, page, line_no, text
                                     (one row per non-blank line of pypdf's extract_text() per page, as fetch.py)
    reports_index.csv                date, hour, status (saved / missing / retry), url, bytes, lines, tries, utc
A report counts as missing after MAX_TRIES requests on separate runs (the NBA's archive has gaps). Order: newest
complete seasons first. When everything is saved or missing the script does nothing. Standard library and pypdf.
    python3 archive_reports.py [--limit-seconds N]
"""
import csv, gzip, io, os, sys, time, urllib.error, urllib.request
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "archive")
BASE = "https://ak-static.cms.nba.com/referee/injury/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
HOURS = list(range(11, 23))                 # 11:30 AM ... 10:30 PM ET
EVENING = [h for h in HOURS if h >= 17]     # for a day that only precedes a game day
SEASON_ORDER = [2025, 2026, 2024, 2027, 2023, 2022]
QUARTER_ERA_FROM = "2025-12-15"             # which name form to try first; both are always tried
GAP, TIMEOUT, MAX_TRIES = 0.5, 30, 2
TIME_LIMIT = 35 * 60
INDEX_COLS = ["date", "hour", "status", "url", "bytes", "lines", "tries", "utc"]


def names(d, h):
    hh, ap = (h % 12) or 12, ("PM" if h >= 12 else "AM")
    quarter, hourly = f"Injury-Report_{d}_{hh:02d}_30{ap}.pdf", f"Injury-Report_{d}_{hh:02d}{ap}.pdf"
    return [quarter, hourly] if d >= QUARTER_ERA_FROM else [hourly, quarter]


def targets():
    """(date, hour) pairs in the order they are fetched."""
    by_season = {}
    with open(os.path.join(OUT, "games.csv"), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            by_season.setdefault(int(r["season"]), set()).add(r["date"])
    out, seen = [], set()
    for s in SEASON_ORDER + sorted(set(by_season) - set(SEASON_ORDER)):
        days = by_season.get(s, set())
        want = {}
        for d in days:
            want[d] = HOURS
            prev = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
            want.setdefault(prev, EVENING)
        for d in sorted(want):
            hours = HOURS if d in days else want[d]
            for h in hours:
                if (d, h) not in seen:
                    seen.add((d, h)); out.append((d, h))
    return out


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/pdf,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:                                     # network error, timeout: logged, not fatal
        return type(e).__name__, b""


def pdf_rows(data, d, h, url):
    from pypdf import PdfReader
    rows = []
    for pno, page in enumerate(PdfReader(io.BytesIO(data)).pages, 1):
        for lno, line in enumerate((page.extract_text() or "").splitlines(), 1):
            if line.strip():
                rows.append([d, f"{h:02d}:30", url, pno, lno, line.strip()])
    return rows


def load_index():
    p = os.path.join(OUT, "reports_index.csv")
    if not os.path.exists(p):
        return {}
    with open(p, newline="", encoding="utf-8") as f:
        return {(r["date"], int(r["hour"])): r for r in csv.DictReader(f)}


def save_index(ix):
    p = os.path.join(OUT, "reports_index.csv")
    with open(p + ".tmp", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=INDEX_COLS); w.writeheader()
        for k in sorted(ix):
            w.writerow(ix[k])
    os.replace(p + ".tmp", p)


def main():
    limit = TIME_LIMIT
    if "--limit-seconds" in sys.argv:
        limit = float(sys.argv[sys.argv.index("--limit-seconds") + 1])
    ix, t0, n_saved, n_tried = load_index(), time.time(), 0, 0
    today = datetime.now(timezone.utc).date().isoformat()
    for d, h in targets():
        if time.time() - t0 > limit:
            break
        if d >= today:
            continue
        rec = ix.get((d, h))
        if rec and (rec["status"] in ("saved", "missing")):
            continue
        tries = int(rec["tries"]) + 1 if rec else 1
        got = None
        for name in names(d, h):
            status, body = get(BASE + name)
            time.sleep(GAP)
            if status == 200 and body[:4] == b"%PDF":
                got = (name, body); break
        n_tried += 1
        rec = dict(date=d, hour=h, status="retry", url="", bytes=0, lines=0, tries=tries,
                   utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        if got:
            try:
                rows = pdf_rows(got[1], d, h, BASE + got[0])
            except Exception as e:                             # an unreadable PDF: try again on a later run
                print("unreadable", got[0], repr(e)[:120]); rows = None
            if rows is not None:
                folder = os.path.join(OUT, "reports", d)
                os.makedirs(folder, exist_ok=True)
                buf = io.StringIO()
                w = csv.writer(buf); w.writerow(["report_date", "report_time_et", "url", "page", "line_no", "text"]); w.writerows(rows)
                with open(os.path.join(folder, f"{h:02d}30.csv.gz"), "wb") as f:
                    f.write(gzip.compress(buf.getvalue().encode("utf-8"), compresslevel=9, mtime=0))
                rec.update(status="saved", url=BASE + got[0], bytes=len(got[1]), lines=len(rows)); n_saved += 1
        if rec["status"] != "saved" and tries >= MAX_TRIES:
            rec["status"] = "missing"
        ix[(d, h)] = rec
    save_index(ix)
    left = sum(1 for k in targets() if k[0] < today and ix.get(k, {}).get("status") not in ("saved", "missing"))
    print(f"REPORT ARCHIVE: {n_saved} saved of {n_tried} tried in {time.time() - t0:.0f} s; "
          f"{sum(1 for r in ix.values() if r['status'] == 'saved')} saved in all, {left} still to do")


if __name__ == "__main__":
    main()
