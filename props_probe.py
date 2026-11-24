"""One-off probe (Nov 24 2026): save the raw scoresandodds props pages so the market layout can be read.
Writes probe/<name>.html.gz and probe/probe.csv (url, status, bytes). Removed once props_snap.py exists."""
import csv, gzip, os, urllib.error, urllib.request
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
URLS = {"base": "https://www.scoresandodds.com/nba/props",
        "path_rebounds": "https://www.scoresandodds.com/nba/props/rebounds",
        "path_assists": "https://www.scoresandodds.com/nba/props/assists",
        "q_market": "https://www.scoresandodds.com/nba/props?market=rebounds",
        "q_type": "https://www.scoresandodds.com/nba/props?type=rebounds"}
os.makedirs("probe", exist_ok=True)
with open("probe/probe.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["name", "url", "status", "bytes"])
    for k, u in URLS.items():
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30) as r:
                st, b = r.status, r.read()
        except urllib.error.HTTPError as e:
            st, b = e.code, b""
        except Exception as e:
            st, b = 0, str(e).encode()
        w.writerow([k, u, st, len(b)])
        if b:
            with gzip.open(f"probe/{k}.html.gz", "wb") as g:
                g.write(b)
