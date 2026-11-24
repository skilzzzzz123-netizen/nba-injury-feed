"""Backfill Sportsbook Review's opening and closing spreads for the 2023-24, 2024-25 and 2025-26 NBA seasons.

Development/grading data only for the NBA Edge model (Nov 24 2026, the user's decision: pull the data now, fit the
v2 weight after the Dec 14 review). Nothing here is ever a stats-engine input. No look is taken: this only stores
lines; it reads no model price.

Run by .github/workflows/backfill.yml (its own workflow, so it never holds up the injury-report fetch). Each run
reads up to BATCH dates not yet done, 3 s apart, oldest first, and stops early if close to TIME_LIMIT. When every
date is done it does nothing. Dates that fail (no page, no embedded data) are retried on later runs, at most
MAX_TRIES times; a page with no games is read a second time before it counts as a day without games. By hand:  python3 sbr_backfill.py [--batch N]

Writes (sbr/history/):
  books.csv  one row per game and book: date, game_id, start_utc, away, home, away_score, home_score, status,
             book, open_home_spread, close_home_spread   (close = the page's current line once the game is final)
  games.csv  one row per game, rebuilt from books.csv each run: date, game_id, start_utc, away, home, away_score,
             home_score, status, n_books, open_home_median, close_home_median, close_home_dk, open_range
             (median across books: book openers are not timestamped and often differ by a point or more)
  dates.csv  date, tries, status (done / empty / failed), games, utc
Spreads only: SBR's spreads page carries no totals. Standard library only.
"""
import csv, json, os, sys, time
from datetime import date, datetime, timedelta, timezone
from statistics import median
import sbr_snap as S

SEASONS = [("2023-10-24", "2024-06-17"), ("2024-10-22", "2025-06-22"), ("2025-10-21", "2026-06-21")]
OUT = os.path.join(S.SBR, "history")
BATCH, GAP, TIME_LIMIT, MAX_TRIES = 160, 3.0, 15 * 60, 4
BOOK_COLS = ["date", "game_id", "start_utc", "away", "home", "away_score", "home_score", "status", "book",
             "open_home_spread", "close_home_spread"]


def all_dates():
    for a, b in SEASONS:
        d, end = date.fromisoformat(a), date.fromisoformat(b)
        while d <= end:
            yield d.isoformat()
            d += timedelta(days=1)


def read_dates():
    p = os.path.join(OUT, "dates.csv")
    if not os.path.exists(p):
        return {}
    with open(p, newline="") as f:
        return {r["date"]: r for r in csv.DictReader(f)}


def write_dates(D):
    with open(os.path.join(OUT, "dates.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, ["date", "tries", "status", "games", "utc"])
        w.writeheader()
        for k in sorted(D):
            w.writerow(D[k])


def rows_from(data, d):
    out = []

    def team(v, side):
        t = v.get(side + "Team") or {}
        return t.get("shortName") or t.get("name") or t.get("displayName") or ""

    def walk(o):
        if isinstance(o, dict):
            gv, ov = o.get("gameView"), o.get("oddsViews")
            if isinstance(gv, dict) and isinstance(ov, list):
                base = [d, gv.get("gameId", ""), gv.get("startDate", ""), team(gv, "away"), team(gv, "home"),
                        gv.get("awayTeamScore", ""), gv.get("homeTeamScore", ""), gv.get("gameStatusText", "")]
                for v in ov:
                    if isinstance(v, dict):
                        op, cu = v.get("openingLine") or {}, v.get("currentLine") or {}
                        out.append(base + [v.get("sportsbook", ""), S._num(op.get("homeSpread")),
                                           S._num(cu.get("homeSpread"))])
                if not ov:
                    out.append(base + ["", None, None])
                return
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(data)
    return out


def rebuild_games():
    p = os.path.join(OUT, "books.csv")
    if not os.path.exists(p):
        return
    G, seen = {}, set()
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            if (r["date"], r["game_id"], r["book"]) in seen:      # a re-read date after an interrupted run
                continue
            seen.add((r["date"], r["game_id"], r["book"]))
            k = (r["date"], r["game_id"])
            g = G.setdefault(k, dict(r, opens=[], closes=[], dk=""))
            if r["open_home_spread"] not in ("", "None"):
                g["opens"].append(float(r["open_home_spread"]))
            if r["close_home_spread"] not in ("", "None"):
                g["closes"].append(float(r["close_home_spread"]))
                if r["book"].lower() == "draftkings":
                    g["dk"] = r["close_home_spread"]
    cols = ["date", "game_id", "start_utc", "away", "home", "away_score", "home_score", "status", "n_books",
            "open_home_median", "close_home_median", "close_home_dk", "open_range"]
    with open(os.path.join(OUT, "games.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for k in sorted(G):
            g = G[k]
            o, c = g["opens"], g["closes"]
            w.writerow([g["date"], g["game_id"], g["start_utc"], g["away"], g["home"], g["away_score"],
                        g["home_score"], g["status"], len(o), median(o) if o else "", median(c) if c else "",
                        g["dk"], (max(o) - min(o)) if o else ""])


def main():
    os.makedirs(OUT, exist_ok=True)
    batch = int(sys.argv[sys.argv.index("--batch") + 1]) if "--batch" in sys.argv else BATCH
    D = read_dates()
    def wanted(r):                                        # failed: up to MAX_TRIES; no games on the page: read twice
        st, tries = r.get("status", ""), int(r.get("tries") or 0)
        return st != "done" and (tries < 2 if st == "empty" else tries < MAX_TRIES)
    todo = [d for d in all_dates() if wanted(D.get(d, {}))]
    if not todo:
        print("backfill complete: nothing to do")
        return 0
    t0, n = time.time(), 0
    for d in todo[:batch]:
        if time.time() - t0 > TIME_LIMIT:
            break
        status, body = S.get(S.URL.format(d=d))
        rec = D.setdefault(d, {"date": d, "tries": "0", "status": "", "games": "0", "utc": ""})
        rec["tries"] = str(int(rec.get("tries") or 0) + 1)
        rec["utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        m = S.NEXT.search(body) if status == 200 and body else None
        if not m:
            rec["status"] = f"failed ({status})"
        else:
            try:
                rows = rows_from(json.loads(m.group(1)), d)
            except ValueError:
                rows = None
            if rows is None:
                rec["status"] = "failed (json)"
            else:
                games = len({r[1] for r in rows})
                rec["status"], rec["games"] = ("done" if games else "empty"), str(games)
                for r in rows:
                    S.append(os.path.join(OUT, "books.csv"), BOOK_COLS, r)
        n += 1
        write_dates(D)
        time.sleep(GAP)
    write_dates(D)
    rebuild_games()
    left = sum(1 for d in all_dates() if wanted(D.get(d, {})))
    print(f"read {n} dates; {left} left")
    return 0


if __name__ == "__main__":
    sys.exit(main())
