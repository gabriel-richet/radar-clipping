"""Collect the best paid clips of every Content Rewards campaign (up to 10 per campaign).

Read-only (public API of the Discover page). Each clip comes with its platform, view count, caption and video.
Output: formats/top_clips.json
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "formats")
API = "https://contentrewards.com/api/campaign/campaigns/discover"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129 Safari/537.36"

sys.path.insert(0, os.path.join(os.path.dirname(HERE), "radar"))
from classement import language  # noqa: E402

clips = []
seen = set()
cursor = None
for page in range(60):
    params = {"collapseGroups": "false", "limit": "50", "view": "full", "sortBy": "newest"}
    if cursor:
        params["cursor"] = cursor
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    for c in d["data"]:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        for t in c["metrics"].get("topClips") or []:
            clips.append({
                "id": t["submissionId"], "campagne_id": c["id"], "campagne": c["name"].strip(),
                "langue": language(c), "categories": [k["id"] for k in c["categories"]],
                "taux": c["primaryPayoutCents"] / 100, "plateforme": t["platform"], "rang": t["rank"],
                "vues": t["totalViews"], "auteur": t.get("username"), "legende": (t.get("title") or "").strip(),
                "lien": t["postUrl"], "video": t.get("videoUrl"), "vignette": t.get("thumbnailUrl"),
            })
    print(f"page {page}: {len(seen)} campagnes, {len(clips)} clips", file=sys.stderr)
    cursor = d["pagination"].get("nextCursor")
    if not cursor or not d["data"]:
        break
    time.sleep(1)

os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "top_clips.json"), "w", encoding="utf-8") as f:
    json.dump(clips, f, ensure_ascii=False, indent=1)
print(len(clips))
