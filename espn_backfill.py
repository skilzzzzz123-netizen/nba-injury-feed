"""ESPN's public opening and closing prices for PAST NBA games: spreads, totals, moneylines and player props.

Added Nov 27 2026 (the user's go: "begin the replay with the free data"; NBA Edge plan of Nov 27, Phase 1b).
Grading/development data only: it lets past model prices and projections be compared with real market prices. Nothing
here is ever a model input (until the user's v2 ships, and then only in its final layer).

Run by .github/workflows/archive.yml in batches, politely (one request at a time, GAP seconds apart). For every game in
archive/games.csv not yet done it reads
    https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/<id>/competitions/<id>/odds
and, for every provider there that links prop bets (providers named "... Live Odds" are skipped: in-game prices), the
pages of that link.

Writes (archive/espn/):
    odds_<season>.csv        one row per game and provider: date, game_id, provider_id, provider, and for the home
                             spread, both moneylines and the total their open / close / current values with prices,
                             as printed (blank where the provider has none; 2021-22 has current values only)
    props/<season>/<game_id>.csv.gz   one row per prop price: provider_id, athlete_id, team_id, type_id, type, side
                             (over / under / blank when the answer names none), line_open, line_cur, odds_open,
                             odds_cur (American), last_updated
    index.csv                game_id, date, season, status (done / retry / failed), providers, prop_rows, tries, utc
"current" on a finished game is the last price ESPN kept; whether that is the closing price is checked in the model's
code, not assumed here. A game counts as failed after MAX_TRIES runs. When every game is done the script does nothing.
Standard library only.
    python3 espn_backfill.py [--limit-seconds N]
"""
import csv, gzip, io, json, os, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ARCH = os.path.join(HERE, "archive")
OUT = os.path.join(ARCH, "espn")
BASE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{g}/competitions/{g}/odds"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
SEASON_ORDER = [2025, 2024, 2026, 2027, 2023, 2022]
GAP, TIMEOUT, MAX_TRIES, MAX_PAGES = 0.35, 30, 3, 12
TIME_LIMIT = 12 * 60
ODDS_COLS = ["date", "game_id", "provider_id", "provider", "home_spread_open", "home_spread_close", "home_spread_cur",
             "home_spread_odds_open", "home_spread_odds_close", "home_spread_odds_cur",
             "away_spread_odds_open", "away_spread_odds_close", "away_spread_odds_cur",
             "home_ml_open", "home_ml_close", "home_ml_cur", "away_ml_open", "away_ml_close", "away_ml_cur",
             "total_open", "total_close", "total_cur", "over_odds_open", "over_odds_close", "over_odds_cur",
             "under_odds_open", "under_odds_close", "under_odds_cur", "spread_top", "over_under_top"]
PROP_COLS = ["provider_id", "athlete_id", "team_id", "type_id", "type", "side", "line_open", "line_cur", "odds_open",
             "odds_cur", "last_updated"]
INDEX_COLS = ["game_id", "date", "season", "status", "providers", "prop_rows", "tries", "utc"]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:
        return type(e).__name__, b""


def _am(o, *path):
    """The value at a path as printed (its "american" form when it is a price object), or ""."""
    for k in path:
        if not isinstance(o, dict):
            return ""
        o = o.get(k)
    if isinstance(o, dict):
        o = o.get("american", o.get("alternateDisplayValue", o.get("value", "")))
    return "" if o is None else str(o)


def _id(ref):
    """The last path element of a $ref link, e.g. .../athletes/3209?lang=en -> 3209."""
    if not isinstance(ref, dict) or not ref.get("$ref"):
        return ""
    return ref["$ref"].split("?")[0].rstrip("/").split("/")[-1]


def odds_rows(j, date, gid):
    """One row per provider from the game's odds answer, and {provider id: prop link} for providers to read."""
    rows, links = [], {}
    for it in j.get("items") or []:
        pv = it.get("provider") or {}
        pid, name = str(pv.get("id", "")), str(pv.get("name", ""))
        h, a = it.get("homeTeamOdds") or {}, it.get("awayTeamOdds") or {}
        row = [date, gid, pid, name]
        row += [_am(h, s, "pointSpread") for s in ("open", "close", "current")]
        row += [_am(h, s, "spread") for s in ("open", "close", "current")]
        row += [_am(a, s, "spread") for s in ("open", "close", "current")]
        row += [_am(h, s, "moneyLine") for s in ("open", "close", "current")]
        row += [_am(a, s, "moneyLine") for s in ("open", "close", "current")]
        row += [_am(it, s, "total") for s in ("open", "close", "current")]
        row += [_am(it, s, "over") for s in ("open", "close", "current")]
        row += [_am(it, s, "under") for s in ("open", "close", "current")]
        row += [_am(it, "spread"), _am(it, "overUnder")]
        rows.append(row)
        pb = it.get("propBets")
        if isinstance(pb, dict) and pb.get("$ref") and "live" not in name.lower():
            links[pid] = pb["$ref"]
    return rows, links


def prop_rows(j, pid):
    rows = []
    for it in j.get("items") or []:
        cur, op, od = it.get("current") or {}, it.get("open") or {}, it.get("odds") or {}
        side = "over" if ("over" in cur or "over" in op) else "under" if ("under" in cur or "under" in op) else ""
        ty = it.get("type") or {}
        rows.append([pid, _id(it.get("athlete")), _id(it.get("team")), str(ty.get("id", "")), str(ty.get("name", "")), side,
                     _am(od, "total", "open") or _am(op, "target", "value"), _am(od, "total", "value") or _am(cur, "target", "value"),
                     _am(od, "american", "open") or _am(op, side or "over"), _am(od, "american", "value") or _am(cur, side or "over"),
                     str(it.get("lastUpdated", ""))])
    return rows


def read_game(date, gid):
    """(odds rows, prop rows, number of providers) for one game, or None when the odds answer could not be read."""
    status, body = get(BASE.format(g=gid) + "?lang=en&region=us&limit=100")
    time.sleep(GAP)
    if status != 200 or not body:
        return None
    try:
        orows, links = odds_rows(json.loads(body), date, gid)
    except ValueError:
        return None
    props = []
    for pid, ref in sorted(links.items()):
        url = ref.replace("http://", "https://")
        url += ("&" if "?" in url else "?") + "limit=1000"
        for page in range(1, MAX_PAGES + 1):
            status, body = get(url + f"&page={page}")
            time.sleep(GAP)
            if status != 200 or not body:
                return None                                   # an unfinished provider: the whole game is retried
            try:
                j = json.loads(body)
            except ValueError:
                return None
            props += prop_rows(j, pid)
            if page >= int(j.get("pageCount") or 1):
                break
    return orows, props, len(orows)


def load_index():
    p = os.path.join(OUT, "index.csv")
    if not os.path.exists(p):
        return {}
    with open(p, newline="", encoding="utf-8") as f:
        return {r["game_id"]: r for r in csv.DictReader(f)}


def main():
    limit = TIME_LIMIT
    if "--limit-seconds" in sys.argv:
        limit = float(sys.argv[sys.argv.index("--limit-seconds") + 1])
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(ARCH, "games.csv"), newline="", encoding="utf-8") as f:
        games = list(csv.DictReader(f))
    rank = {s: i for i, s in enumerate(SEASON_ORDER)}
    games.sort(key=lambda r: (rank.get(int(r["season"]), 99), r["date"], r["game_id"]))
    ix, t0, new_odds, n_done, n_props = load_index(), time.time(), {}, 0, 0
    for g in games:
        if time.time() - t0 > limit:
            break
        gid, rec = g["game_id"], ix.get(g["game_id"])
        if rec and rec["status"] in ("done", "failed"):
            continue
        tries = int(rec["tries"]) + 1 if rec else 1
        got = read_game(g["date"], gid)
        rec = dict(game_id=gid, date=g["date"], season=g["season"], status="retry", providers=0, prop_rows=0, tries=tries,
                   utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        if got is not None:
            orows, props, npv = got
            new_odds.setdefault(g["season"], []).extend(orows)
            if props:
                folder = os.path.join(OUT, "props", g["season"])
                os.makedirs(folder, exist_ok=True)
                buf = io.StringIO()
                w = csv.writer(buf); w.writerow(PROP_COLS); w.writerows(props)
                with open(os.path.join(folder, gid + ".csv.gz"), "wb") as f:
                    f.write(gzip.compress(buf.getvalue().encode("utf-8"), compresslevel=9, mtime=0))
            rec.update(status="done", providers=npv, prop_rows=len(props)); n_done += 1; n_props += len(props)
        elif tries >= MAX_TRIES:
            rec["status"] = "failed"
        ix[gid] = rec
    # odds rows and the index are written together at the end, so a game is never half recorded
    for season, rows in new_odds.items():
        p = os.path.join(OUT, f"odds_{season}.csv")
        fresh = not os.path.exists(p)
        with open(p, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if fresh:
                w.writerow(ODDS_COLS)
            w.writerows(rows)
    p = os.path.join(OUT, "index.csv")
    with open(p + ".tmp", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=INDEX_COLS); w.writeheader()
        for k in sorted(ix, key=lambda k: (ix[k]["date"], k)):
            w.writerow(ix[k])
    os.replace(p + ".tmp", p)
    done = sum(1 for r in ix.values() if r["status"] == "done")
    print(f"ESPN PRICES: {n_done} games read ({n_props} prop rows) in {time.time() - t0:.0f} s; {done} of {len(games)} games done")


if __name__ == "__main__":
    main()
