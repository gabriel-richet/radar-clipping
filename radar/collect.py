"""Collect every public campaign listed on Content Rewards (Whop) with its budget and metrics.

Read-only (public API of the Discover page, no account needed).
Output: campaigns.json + a dated copy in history/ (used to measure how fast budgets are spent).
"""
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://contentrewards.com/api/campaign/campaigns/discover"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129 Safari/537.36"

rows = {}
cursor = None
for page in range(60):
    params = {"collapseGroups": "false", "limit": "50", "view": "summary", "sortBy": "newest"}
    if cursor:
        params["cursor"] = cursor
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    for c in d["data"]:
        m = c["metrics"]
        # keep the file small: the charts and top lists are not used by the ranking
        for k in ("chartPoints", "topClips", "topEarners"):
            m.pop(k, None)
        rows[c["id"]] = c
    print(f"page {page}: {len(d['data'])} (total {len(rows)})", file=sys.stderr)
    cursor = d["pagination"].get("nextCursor")
    if not cursor or not d["data"]:
        break
    time.sleep(1)

out = {"collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
       "campaigns": list(rows.values())}
with open(os.path.join(HERE, "campaigns.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
os.makedirs(os.path.join(HERE, "history"), exist_ok=True)
with open(os.path.join(HERE, "history", datetime.date.today().isoformat() + ".json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
print(len(rows))
