"""One-off probe (Nov 24 2026, NBA Edge v2 data check): do Sportsbook Review's pages for PAST dates still carry each
game's opening and closing spread in their embedded data? Grading/development data only; never a model input.

Reads five past dates (2023-24, 2024-25 and 2025-26 seasons), politely (3 s apart), with sbr_snap's reader plus a
per-book listing, and writes:
    sbr/probe/games.csv   date, game_id, away, home, open_home_spread, close_home_spread_dk (sbr_snap's reader)
    sbr/probe/books.csv   date, game_id, book, open_home, open_total, cur_home, cur_total (every odds view)
    sbr/probe/<date>.html.gz   the raw page, for checking the layout
Run once by the workflow when this file is pushed. Standard library only."""
import csv, gzip, json, os, time
import sbr_snap as S

DATES = ["2023-11-15", "2024-02-07", "2024-12-10", "2025-03-12", "2026-01-21"]
OUT = os.path.join(S.SBR, "probe")


def books(data, date):
    rows = []

    def walk(o):
        if isinstance(o, dict):
            gv, ov = o.get("gameView"), o.get("oddsViews")
            if isinstance(gv, dict) and isinstance(ov, list):
                for v in ov:
                    if isinstance(v, dict):
                        op, cu = v.get("openingLine") or {}, v.get("currentLine") or {}
                        rows.append([date, gv.get("gameId", ""), v.get("sportsbook", ""), op.get("homeSpread"),
                                     op.get("total"), cu.get("homeSpread"), cu.get("total")])
                return
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(data)
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    G, B = [], []
    for d in DATES:
        status, body = S.get(S.URL.format(d=d))
        print(d, status, len(body))
        if status == 200 and body:
            with gzip.open(os.path.join(OUT, d + ".html.gz"), "wb") as f:
                f.write(body)
            m = S.NEXT.search(body)
            if m:
                data = json.loads(m.group(1))
                for g in S.games_from_next(data):
                    G.append([d, g["game_id"], g["away"], g["home"], g["open_home_spread"], g["cur_home_spread"]])
                B += books(data, d)
            else:
                G.append([d, "", "", "", "", "no __NEXT_DATA__"])
        else:
            G.append([d, "", "", "", "", f"status {status}"])
        time.sleep(3)
    with open(os.path.join(OUT, "games.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "game_id", "away", "home", "open_home_spread", "close_home_spread_dk"]); w.writerows(G)
    with open(os.path.join(OUT, "books.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "game_id", "book", "open_home", "open_total", "cur_home", "cur_total"]); w.writerows(B)
    print(len(G), "games,", len(B), "book rows")


if __name__ == "__main__":
    main()
