"""Snapshot scoresandodds.com's NBA player-prop pages a few times a day (grading only).

Added Nov 24 2026 by the user's decision, for the NBA Edge props test (nba_edge/props_test.py in the model's code):
it records the best over and best under across books, with prices, for each player and market, so the model's
projections can later be scored against the prices that were on offer. Nothing here is ever a model input.

Run by .github/workflows/fetch.yml on every scheduled run. It takes one snapshot per slot per day (America/New_York):
    morning    11:00-13:59    lines as first posted for the day
    afternoon  16:00-17:59    before the model's 6:08 PM evening run
    evening    18:40-19:59    after the evening run (for the later games)
and does nothing outside those windows or when that slot is done. By hand:  python3 props_snap.py [--force SLOT]

Each snapshot requests https://www.scoresandodds.com/nba/props/<market> for the markets in MARKETS and keeps:
    props/raw/YYYY-MM-DD/<slot>_<market>.html.gz   the page as served (deleted after 2 days)
    props/snapshots.csv   one row per player and market: fetched_utc, fetched_et, slot, market, event, player,
                          matchup (as printed, e.g. "DEN vs DAL"), state, over_line, over_odds, over_book,
                          under_line, under_odds, under_book
    props/attempts.csv    utc, url, status, bytes, rows
    props/done.json       {date: [slots done]}
The page's own projection column (a third party's number) is not kept. Only the standard library is used.
"""
import csv, gzip, html, json, os, re, shutil, sys, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "props")
BASE = "https://www.scoresandodds.com/nba/props/"
MARKETS = ["points", "rebounds", "assists", "3-pointers", "points,-rebounds,-&-assists", "points-&-rebounds",
           "points-&-assists", "rebounds-&-assists"]
SLOTS = {"morning": ((11, 0), (13, 59)), "afternoon": ((16, 0), (17, 59)), "evening": ((18, 40), (19, 59))}
ET = ZoneInfo("America/New_York")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
KEEP_RAW_DAYS = 2
TIMEOUT = 30
COLS = ["fetched_utc", "fetched_et", "slot", "market", "event", "player", "matchup", "state", "over_line",
        "over_odds", "over_book", "under_line", "under_odds", "under_book"]

ROW = re.compile(r'<li class="border"[^>]*data-name="([^"]*)"[^>]*data-state="([^"]*)"[^>]*>(.*?)(?=<li class="border"|</ul>)',
                 re.S)
EVENT = re.compile(r'data-event="([^"]+)"')
NAME = re.compile(r'<div class="props-name">\s*<a [^>]*>([^<]+)</a><br\s*/?>\s*<span[^>]*>([^<]+)</span>')
SIDE = re.compile(r'<span class="data-moneyline">\s*([ou])\s*([0-9]+(?:\.[0-9]+)?)\s*</span>\s*'
                  r'<small class="data-odds[^"]*">\s*([+\-]?[0-9]+|even|EVEN)\s*</small>.*?alt="([^"]*)"', re.S)


def parse(page):
    """Rows of one market page: list of dicts with the COLS fields from event on."""
    out = []
    for m in ROW.finditer(page):
        body = m.group(3)
        ev, nm = EVENT.search(body), NAME.search(body)
        if not nm:
            continue
        r = {"event": ev.group(1) if ev else "", "player": html.unescape(nm.group(1)).strip(),
             "matchup": html.unescape(nm.group(2)).strip(), "state": m.group(2),
             "over_line": "", "over_odds": "", "over_book": "", "under_line": "", "under_odds": "", "under_book": ""}
        for s in SIDE.finditer(body):
            side = "over" if s.group(1) == "o" else "under"
            if r[side + "_line"] == "":
                odds = s.group(3)
                r[side + "_line"], r[side + "_odds"] = s.group(2), ("+100" if odds.lower() == "even" else odds)
                r[side + "_book"] = s.group(4)
        if r["over_line"] or r["under_line"]:
            out.append(r)
    return out


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:
        print("error", url, e)
        return 0, b""


def append(path, header, rows):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)


def slot_now(now_et):
    hm = (now_et.hour, now_et.minute)
    for name, (a, b) in SLOTS.items():
        if a <= hm <= b:
            return name
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    now = datetime.now(timezone.utc); now_et = now.astimezone(ET); day = now_et.date().isoformat()
    forced = sys.argv[sys.argv.index("--force") + 1] if "--force" in sys.argv else None
    slot = forced or slot_now(now_et)
    if not slot:
        print("outside the snapshot windows: nothing to do")
        return 0
    dpath = os.path.join(OUT, "done.json")
    done = json.load(open(dpath)) if os.path.exists(dpath) else {}
    if not forced and slot in done.get(day, []):
        print(f"{slot} snapshot already taken today")
        return 0
    rawdir = os.path.join(OUT, "raw", day); os.makedirs(rawdir, exist_ok=True)
    total = 0
    for mk in MARKETS:
        url = BASE + mk
        status, body = get(url)
        rows = parse(body.decode("utf-8", "replace")) if status == 200 and body else []
        append(os.path.join(OUT, "attempts.csv"), ["utc", "url", "status", "bytes", "rows"],
               [[now.strftime("%Y-%m-%dT%H:%M:%SZ"), url, status, len(body), len(rows)]])
        if body:
            with gzip.open(os.path.join(rawdir, f"{slot}_{re.sub(r'[^a-z0-9]+', '-', mk)}.html.gz"), "wb") as g:
                g.write(body)
        base = [now.strftime("%Y-%m-%dT%H:%M:%SZ"), now_et.strftime("%Y-%m-%d %H:%M"), slot, mk]
        append(os.path.join(OUT, "snapshots.csv"), COLS, [base + [r[c] for c in COLS[4:]] for r in rows])
        total += len(rows)
        print(mk, status, len(body), "bytes,", len(rows), "rows")
    done.setdefault(day, [])
    if slot not in done[day]:
        done[day].append(slot)
    json.dump({d: v for d, v in done.items() if d >= (now_et.date() - timedelta(days=14)).isoformat()},
              open(dpath, "w"), indent=0)
    cut = (now_et.date() - timedelta(days=KEEP_RAW_DAYS)).isoformat()
    for d in os.listdir(os.path.join(OUT, "raw")):
        if d < cut:
            shutil.rmtree(os.path.join(OUT, "raw", d), ignore_errors=True)
    print(slot, "snapshot:", total, "rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
