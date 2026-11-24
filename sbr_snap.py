"""Snapshot Sportsbook Review's NBA spreads page about once an hour, so each game's opener gets a first-seen time.

Grading data only for the NBA Edge model (plan of Nov 18 2026; added Nov 24 2026 by the user's decision): it
records WHEN each game's opening spread first appears, so the night-vs-opener measure can tell whether the opener
came before or after the model's night price. Nothing here is ever a model input.

Run by .github/workflows/fetch.yml on every scheduled run; it does nothing if the last snapshot is under 45 minutes
old. Also runs by hand:  python3 sbr_snap.py [--force]

Each snapshot requests the spreads page for today and tomorrow (America/New_York dates):
    https://www.sportsbookreview.com/betting-odds/nba-basketball/?date=YYYY-MM-DD
and keeps:
    sbr/raw/YYYY-MM-DD/<fetched ET YYYYmmddTHHMM>.html.gz   the page as served (deleted after 2 days)
    sbr/snapshots.csv   one row per game per snapshot: fetched_utc, fetched_et, date, game_id, away, home,
                        open_home_spread, open_away_spread, cur_home_spread, cur_book, parse
                        (parse = "next_data" when read from the page's embedded data, "none" when the page could
                        not be read; then only the raw file is kept)
    sbr/attempts.csv    utc, url, status, bytes
    sbr/last_snap.txt   UTC time of the last snapshot
Version 1 (Nov 24 2026): the reader looks for the page's embedded __NEXT_DATA__ JSON and walks it for game
objects with an opening line. If the page's layout is not what it expects, it writes parse = "none" rows and the
raw page is kept for a fixed reader. Only the standard library is used.
"""
import csv, gzip, json, os, re, shutil, sys, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SBR = os.path.join(HERE, "sbr")
URL = "https://www.sportsbookreview.com/betting-odds/nba-basketball/?date={d}"
ET = ZoneInfo("America/New_York")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = timedelta(minutes=45)
KEEP_RAW_DAYS = 2
TIMEOUT = 30
COLS = ["fetched_utc", "fetched_et", "date", "game_id", "away", "home", "open_home_spread", "open_away_spread",
        "cur_home_spread", "cur_book", "parse"]
NEXT = re.compile(rb'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def append(path, header, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:                                     # network error: status 0
        print("error", url, e)
        return 0, b""


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def games_from_next(data):
    """Walk the embedded JSON for game objects. A game object is a dict with a game view (ids and the two teams)
    and a list of odds views, each with an opening and a current line per book. Returns a list of dicts with the
    COLS fields after date. Written against SBR's layout as of Nov 2026 (gameView / oddsViews / openingLine /
    currentLine); anything else yields no games."""
    out = []

    def team(v, side):
        t = v.get(side + "Team") or {}
        return t.get("shortName") or t.get("name") or t.get("displayName") or ""

    def walk(o):
        if isinstance(o, dict):
            gv, ov = o.get("gameView"), o.get("oddsViews")
            if isinstance(gv, dict) and isinstance(ov, list):
                row = {"game_id": gv.get("gameId", ""), "away": team(gv, "away"), "home": team(gv, "home"),
                       "open_home_spread": None, "open_away_spread": None, "cur_home_spread": None, "cur_book": ""}
                for v in ov:
                    if not isinstance(v, dict):
                        continue
                    op, cu = v.get("openingLine") or {}, v.get("currentLine") or {}
                    if row["open_home_spread"] is None and _num(op.get("homeSpread")) is not None:
                        row["open_home_spread"] = _num(op.get("homeSpread"))
                        row["open_away_spread"] = _num(op.get("awaySpread"))
                    if (v.get("sportsbook") or "").lower() == "draftkings" and _num(cu.get("homeSpread")) is not None:
                        row["cur_home_spread"], row["cur_book"] = _num(cu.get("homeSpread")), "draftkings"
                out.append(row)
                return
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(data)
    return out


def prune(now_et):
    raw = os.path.join(SBR, "raw")
    if not os.path.isdir(raw):
        return
    cut = (now_et - timedelta(days=KEEP_RAW_DAYS)).date().isoformat()
    for d in os.listdir(raw):
        if d < cut:
            shutil.rmtree(os.path.join(raw, d), ignore_errors=True)


def main():
    os.makedirs(SBR, exist_ok=True)
    now = datetime.now(timezone.utc)
    last = os.path.join(SBR, "last_snap.txt")
    if "--force" not in sys.argv and os.path.exists(last):
        try:
            prev = datetime.fromisoformat(open(last).read().strip())
            if now - prev < MIN_GAP:
                print("last snapshot", prev.isoformat(), "is under 45 minutes old: nothing to do")
                return 0
        except ValueError:
            pass
    now_et = now.astimezone(ET)
    stamp = now_et.strftime("%Y%m%dT%H%M")
    for d in (now_et.date(), now_et.date() + timedelta(days=1)):
        ds = d.isoformat()
        url = URL.format(d=ds)
        status, body = get(url)
        append(os.path.join(SBR, "attempts.csv"), ["utc", "url", "status", "bytes"],
               [now.strftime("%Y-%m-%dT%H:%M:%SZ"), url, status, len(body)])
        if status != 200 or not body:
            continue
        rawdir = os.path.join(SBR, "raw", ds)
        os.makedirs(rawdir, exist_ok=True)
        with gzip.open(os.path.join(rawdir, stamp + ".html.gz"), "wb") as f:
            f.write(body)
        games, how = [], "none"
        m = NEXT.search(body)
        if m:
            try:
                games = games_from_next(json.loads(m.group(1)))
                how = "next_data" if games else "none"
            except ValueError:
                games = []
        base = [now.strftime("%Y-%m-%dT%H:%M:%SZ"), now_et.strftime("%Y-%m-%d %H:%M"), ds]
        if not games:
            append(os.path.join(SBR, "snapshots.csv"), COLS, base + ["", "", "", "", "", "", "", "none"])
        for g in games:
            append(os.path.join(SBR, "snapshots.csv"), COLS,
                   base + [g["game_id"], g["away"], g["home"], g["open_home_spread"], g["open_away_spread"],
                           g["cur_home_spread"], g["cur_book"], how])
        print(ds, status, len(body), "bytes,", len(games), "games,", how)
    with open(last, "w") as f:
        f.write(now.isoformat())
    prune(now_et)
    return 0


if __name__ == "__main__":
    sys.exit(main())
