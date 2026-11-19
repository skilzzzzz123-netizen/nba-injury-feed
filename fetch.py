"""Fetch the NBA's official injury report PDFs and keep a text version of each.

Run by .github/workflows/fetch.yml every 15 minutes (about 11 AM to 1 AM ET); also runs by hand:
    python3 fetch.py

Candidates each run:
  (a) the latest quarter-hour that is at least 10 minutes old on the America/New_York clock, and the 3 before it,
      as https://ak-static.cms.nba.com/referee/injury/Injury-Report_YYYY-MM-DD_HH_MMAM.pdf (12-hour clock,
      two-digit hour, AM or PM);
  (b) every link in backfill.txt not yet saved.
A candidate already saved (its text file exists) is skipped. Each other one is requested once, with a browser-like
User-Agent and a 30 s timeout; it is kept only when the status is 200 and the body starts with %PDF:
    reports/YYYY-MM-DD/<name>.pdf     the PDF (deleted after 3 days)
    text/YYYY-MM-DD/<name>.txt        its text (kept), see pdf_rows()
Every attempt is appended to attempts.csv: utc, url, status, bytes, saved.

The text file is CSV with the columns report_date, report_hour_et, url, page, line_no, text: one row per non-blank
line of pypdf's extract_text() per page, exactly as the project's scripts/pdfs_to_text.py and
user_scripts/nba_edge_pull.py write injury_reports_text.csv (report_hour_et is 13 for reports before 3 PM, else 18).
Only the standard library and pypdf are used.
"""
import csv, io, os, re, shutil, sys, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://ak-static.cms.nba.com/referee/injury/"
ET = ZoneInfo("America/New_York")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
NAME = re.compile(r"(Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})_(\d{2})(AM|PM)\.pdf)$")
KEEP_PDF_DAYS = 3
TIMEOUT = 30


def pdf_rows(data, url):
    """Rows [report_date, report_hour_et, url, page, line_no, text] of one report PDF: the conversion of
    scripts/pdfs_to_text.py (one()), copied here."""
    from pypdf import PdfReader
    fname, d, hh, _mm, ap = NAME.search(url).groups()
    h = int(hh) % 12 + (12 if ap == "PM" else 0)
    slot = 13 if h < 15 else 18
    rows = []
    reader = PdfReader(io.BytesIO(data))
    for pno, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        for lno, line in enumerate(text.splitlines(), 1):
            if line.strip():
                rows.append([d, slot, BASE + fname, pno, lno, line.strip()])
    return rows


def url_for(t):
    """Report link for a quarter-hour t on the ET clock."""
    return f"{BASE}Injury-Report_{t:%Y-%m-%d}_{t:%I_%M%p}.pdf"


def generated(now_et):
    t = now_et - timedelta(minutes=10)
    t = t.replace(minute=t.minute - t.minute % 15, second=0, microsecond=0)
    return [url_for(t - timedelta(minutes=15 * k)) for k in range(4)]


def paths(url):
    fname, d = NAME.search(url).group(1), NAME.search(url).group(2)
    stem = fname[:-4]
    return (os.path.join(HERE, "reports", d, fname), os.path.join(HERE, "text", d, stem + ".txt"))


def backfill():
    p = os.path.join(HERE, "backfill.txt")
    if not os.path.exists(p):
        return []
    return [x.strip() for x in open(p, encoding="utf-8") if x.strip() and not x.lstrip().startswith("#")]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/pdf,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:                                     # network error, timeout: logged, not fatal
        return type(e).__name__, b""


def prune(today_et):
    """Delete report PDFs older than KEEP_PDF_DAYS days (by the report date in the folder name); texts are kept."""
    root = os.path.join(HERE, "reports")
    if not os.path.isdir(root):
        return 0
    cut = (today_et - timedelta(days=KEEP_PDF_DAYS)).strftime("%Y-%m-%d")
    n = 0
    for d in sorted(os.listdir(root)):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and d < cut:
            shutil.rmtree(os.path.join(root, d)); n += 1
    return n


def main():
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ET)
    seen, cands = set(), []
    for u in generated(now_et) + backfill():
        if NAME.search(u) and u not in seen:
            seen.add(u); cands.append(u)
    log = os.path.join(HERE, "attempts.csv")
    new_log = not os.path.exists(log)
    saved = 0
    with open(log, "a", newline="") as f:
        w = csv.writer(f)
        if new_log:
            w.writerow(["utc", "url", "status", "bytes", "saved"])
        for u in cands:
            pdf_path, txt_path = paths(u)
            if os.path.exists(txt_path):
                continue                                        # already saved
            status, body = get(u)
            ok = status == 200 and body.startswith(b"%PDF")
            note = ""
            if ok:
                try:
                    rows = pdf_rows(body, u)
                except Exception as e:                          # unreadable PDF: keep nothing, log why
                    ok, note = False, f"unreadable: {type(e).__name__}"
            if ok:
                os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
                os.makedirs(os.path.dirname(txt_path), exist_ok=True)
                with open(pdf_path, "wb") as fp:
                    fp.write(body)
                with open(txt_path, "w", newline="", encoding="utf-8") as ft:
                    tw = csv.writer(ft)
                    tw.writerow(["report_date", "report_hour_et", "url", "page", "line_no", "text"])
                    tw.writerows(rows)
                saved += 1
            w.writerow([now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), u, status, len(body), "yes" if ok else ("no " + note).strip()])
            print(f"{status} {len(body):>8} {'saved' if ok else 'not saved'} {u} {note}".rstrip(), flush=True)
    gone = prune(now_et)
    print(f"done at {now_et:%Y-%m-%d %H:%M} ET: {len(cands)} candidates, {saved} saved, {gone} old PDF folders deleted")


if __name__ == "__main__":
    sys.exit(main())
