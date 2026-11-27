"""One-off probe (Nov 27 2026, NBA Edge plan Phase 1b): what does ESPN's public odds data keep for PAST NBA games?

Grading/development data only; never a model input. It reads a few past games across seasons, politely, and saves the
raw answers so the layout can be read before the full pull is written:
    archive/espn_probe/<game_id>_odds.json.gz              the game's odds list (every provider)
    archive/espn_probe/<game_id>_props_<provider>.json.gz  the first page of that provider's prop bets, if it has any
    archive/espn_probe/summary.csv                         game_id, date, what, provider, http status, bytes, count, page size
Run once by .github/workflows/archive.yml when this file is pushed. Standard library only."""
import csv, gzip, json, os, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "archive", "espn_probe")
BASE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{g}/competitions/{g}/odds"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
GAMES = [("2022-01-12", "401360437"), ("2023-11-15", "401584820"), ("2024-01-15", "401585183"), ("2024-10-22", "401704627"),
         ("2025-03-12", "401705511"), ("2025-11-12", "401810068"), ("2026-01-21", "401810475"), ("2026-11-25", "401909317")]
GAP, TIMEOUT = 1.0, 30


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:
        return type(e).__name__, b""


def prop_links(o, found):
    """Every {provider id: propBets link} in the odds answer, wherever it sits."""
    if isinstance(o, dict):
        pb, pv = o.get("propBets"), o.get("provider")
        if isinstance(pb, dict) and pb.get("$ref") and isinstance(pv, dict):
            found[str(pv.get("id"))] = pb["$ref"]
        for v in o.values():
            prop_links(v, found)
    elif isinstance(o, list):
        for v in o:
            prop_links(v, found)
    return found


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for d, g in GAMES:
        status, body = get(BASE.format(g=g) + "?lang=en&region=us&limit=100")
        time.sleep(GAP)
        cnt = size = ""
        links = {}
        if status == 200 and body:
            with open(os.path.join(OUT, f"{g}_odds.json.gz"), "wb") as f:
                f.write(gzip.compress(body, mtime=0))
            try:
                j = json.loads(body); cnt, size = j.get("count", ""), j.get("pageSize", ""); links = prop_links(j, {})
            except ValueError:
                pass
        rows.append([g, d, "odds", "", status, len(body), cnt, size])
        for pid, ref in sorted(links.items()):
            url = ref.replace("http://", "https://")
            url += ("&" if "?" in url else "?") + "limit=1000"
            status, body = get(url)
            time.sleep(GAP)
            cnt = size = ""
            if status == 200 and body:
                with open(os.path.join(OUT, f"{g}_props_{pid}.json.gz"), "wb") as f:
                    f.write(gzip.compress(body, mtime=0))
                try:
                    j = json.loads(body); cnt, size = j.get("count", ""), j.get("pageSize", "")
                except ValueError:
                    pass
            rows.append([g, d, "props", pid, status, len(body), cnt, size])
    with open(os.path.join(OUT, "summary.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["game_id", "date", "what", "provider", "status", "bytes", "count", "page_size"]); w.writerows(rows)
    for r in rows:
        print(*r)


if __name__ == "__main__":
    main()
