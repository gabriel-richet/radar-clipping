"""Agent Clipping: local dashboard opened from the desktop shortcut.

Serves agent/index.html on http://127.0.0.1:8765 (this PC only) and exposes the radar, the followed campaigns,
the finished clips and a button that opens Claude Code in the clipping folder.
"""
import datetime
import glob
import json
import os
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

HERE = os.path.dirname(os.path.abspath(__file__))
CLIP = os.path.dirname(HERE)
RADAR = os.path.join(CLIP, "radar")
SUIVI = os.path.join(HERE, "suivi.json")
PORT = 8765
TOKEN = secrets.token_urlsafe(24)
NO_WINDOW = 0x08000000
NEW_CONSOLE = 0x00000010

sys.path.insert(0, RADAR)
import classement  # noqa: E402


def python_exe():
    # pythonw has no console: the scripts are run with the python.exe next to it
    return os.path.join(os.path.dirname(sys.executable), "python.exe")


def load_followed():
    if os.path.exists(SUIVI):
        return json.load(open(SUIVI, encoding="utf-8"))
    return []


def load_rows(path):
    data = json.load(open(path, encoding="utf-8"))
    now = datetime.datetime.fromisoformat(data["collected_at"])
    return data["collected_at"], [classement.row(c, now) for c in data["campaigns"]]


def previous_snapshot():
    """Latest dated copy older than today, to show what moved since the last visit."""
    today = datetime.date.today().isoformat()
    files = sorted(f for f in glob.glob(os.path.join(RADAR, "history", "*.json"))
                   if os.path.basename(f)[:10] < today)
    return files[-1] if files else None


def clip_folders():
    out = []
    for d in os.listdir(CLIP):
        full = os.path.join(CLIP, d)
        if not os.path.isdir(full) or "clips" not in d:
            continue
        files = sorted(f for f in os.listdir(full) if f.lower().endswith(".mp4"))
        if not files:
            continue
        desc = os.path.join(full, "description.txt")
        out.append({
            "dossier": d, "modifie": os.path.getmtime(full),
            "description": open(desc, encoding="utf-8").read().strip() if os.path.exists(desc) else None,
            "clips": [{"fichier": f, "mo": round(os.path.getsize(os.path.join(full, f)) / 1e6, 1)} for f in files],
        })
    return sorted(out, key=lambda x: -x["modifie"])


def state():
    path = os.path.join(RADAR, "campaigns.json")
    if not os.path.exists(path):
        return {"collected_at": None, "campagnes": [], "suivies": load_followed(), "dossiers": clip_folders(),
                "taches": load_tasks(), "planning": load_planning()}
    collected, rows = load_rows(path)
    prev = previous_snapshot()
    if prev:
        prev_date, prev_rows = load_rows(prev)
        before = {r["id"]: r for r in prev_rows}
        for r in rows:
            b = before.get(r["id"])
            r["depuis"] = prev_date[:10] if b else None
            r["depense_depuis"] = (b["restant"] - r["restant"]) if b else None
            r["nouvelle"] = b is None
    cpm = [r for r in rows if r["taux"]]
    proven = [r for r in cpm if r["clips"] >= 20 and r["clippeurs"] >= 5]
    views = classement.tot(rows, "vues")
    med = sorted(r["gain_par_clippeur"] for r in proven)
    rates = sorted(r["taux"] for r in cpm)
    return {
        "collected_at": collected, "campagnes": rows, "suivies": load_followed(), "dossiers": clip_folders(),
        "taches": load_tasks(), "planning": load_planning(),
        "marche": {
            "taux_median": rates[len(rates) // 2] if rates else None,
            "taux_reel": classement.spent(rows) / views * 1000 if views else None,
            "gain_median": med[len(med) // 2] if med else None,
            "restant": classement.tot(rows, "restant"),
        },
    }


FORMATS = os.path.join(CLIP, "analyse", "formats")
formats_job = {"en_cours": False, "erreur": None}


def formats_state():
    """Best paid clips that were measured (analyse/formats_mesure.py), with the written summary if there is one."""
    top, mes, syn = (os.path.join(FORMATS, n) for n in ("top_clips.json", "mesures.json", "SYNTHESE.md"))
    out = {"clips": [], "synthese": None, "date": None, "suivies": load_followed(), **formats_job}
    if os.path.exists(top) and os.path.exists(mes):
        clips = {c["id"]: c for c in json.load(open(top, encoding="utf-8"))}
        for m in json.load(open(mes, encoding="utf-8")):
            c = clips.get(m["id"])
            if c and "erreur" not in m:
                out["clips"].append({**{k: v for k, v in c.items() if k not in ("video", "vignette")}, **m})
        out["date"] = datetime.datetime.fromtimestamp(os.path.getmtime(mes)).isoformat(timespec="minutes")
    if os.path.exists(syn):
        out["synthese"] = open(syn, encoding="utf-8").read()
    out["fiches"] = format_sheets(out["clips"], out["suivies"])
    return out


GAMING = {"gaming", "streamer", "roblox", "kick", "esports"}


def median(values):
    s = [v for v in values if v is not None]
    return statistics.median(s) if s else None


def format_sheets(clips, followed):
    """One sheet per followed campaign and per platform it pays: numbers from the best clips + written advice.

    The numbers come from the campaign's own best clips on that platform when there are at least 3,
    otherwise from comparable campaigns (video games, or same language) on the same platform.
    """
    path = os.path.join(RADAR, "campaigns.json")
    if not os.path.exists(path):
        return []
    raw = {c["id"]: c for c in json.load(open(path, encoding="utf-8"))["campaigns"]}
    written = {}
    if os.path.exists(os.path.join(FORMATS, "fiches.json")):
        written = json.load(open(os.path.join(FORMATS, "fiches.json"), encoding="utf-8"))
    sheets = []
    for cid in followed:
        c = raw.get(cid)
        if not c:
            continue
        advice = written.get("campagnes", {}).get(cid, {})
        gaming = bool(GAMING & {k["id"] for k in c["categories"]}) or "regles" in advice
        lang = classement.language(c)
        platforms = []
        for pay in c.get("payouts") or []:
            p = pay["platform"]
            own = [x for x in clips if x["campagne_id"] == cid and x["plateforme"] == p]
            if len(own) >= 3:
                base, source = own, "les meilleurs clips de cette campagne"
            else:
                base = [x for x in clips if x["plateforme"] == p and GAMING & set(x["categories"])] if gaming else []
                source = "les meilleurs clips des campagnes de jeux vidéo"
                if len(base) < 5:
                    base = [x for x in clips if x["plateforme"] == p and x["langue"] == lang]
                    source = "les meilleurs clips des campagnes dans la même langue"
            durations = sorted(x["duree"] for x in base)
            best = sorted(own or base, key=lambda x: -x["vues"])[:3]
            platforms.append({
                "plateforme": p, "taux": pay["rateCents"] / 100,
                "seuil_vues": pay["minPayoutCents"] / pay["rateCents"] * 1000 if pay["rateCents"] else None,
                "plafond": pay["maxPayoutCents"] / 100,
                "source": source, "n": len(base), "n_propres": len(own),
                "vues": median(x["vues"] for x in base), "duree": median(durations),
                "duree_min": durations[len(durations) // 4] if durations else None,
                "duree_max": durations[3 * len(durations) // 4] if durations else None,
                "coupes": median(x["coupes_par_10s"] for x in base),
                "voix": sum(1 for x in base if (x.get("voix_part") or 0) >= .3) / len(base) if base else None,
                "exemples": [{"id": x["id"], "vues": x["vues"], "lien": x["lien"], "propre": x["campagne_id"] == cid}
                             for x in best],
                "conseil": advice.get("plateformes", {}).get(p),
                "technique": written.get("plateformes", {}).get(p),
            })
        platforms.sort(key=lambda x: -x["n_propres"])
        sheets.append({"id": cid, "nom": c["name"].strip(), "resume": advice.get("resume"),
                       "regles": advice.get("regles", []), "plateformes": platforms})
    return sheets


PLANNING = os.path.join(HERE, "planning.json")


def load_planning():
    return json.load(open(PLANNING, encoding="utf-8")) if os.path.exists(PLANNING) else []


TACHES = os.path.join(HERE, "taches.json")


def load_tasks():
    return json.load(open(TACHES, encoding="utf-8")) if os.path.exists(TACHES) else []


def save_tasks(tasks):
    json.dump(tasks, open(TACHES, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def update_formats():
    formats_job.update(en_cours=True, erreur=None)
    try:
        for script in ("formats_collect.py", "formats_mesure.py"):
            p = subprocess.run([python_exe(), "-X", "utf8", os.path.join(CLIP, "analyse", script)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=1800)
            if p.returncode:
                formats_job["erreur"] = (p.stderr or p.stdout)[-600:]
                break
    except Exception as e:
        formats_job["erreur"] = str(e)[:600]
    formats_job["en_cours"] = False


def update_radar():
    for script in ("collect.py", "classement.py"):
        p = subprocess.run([python_exe(), "-X", "utf8", os.path.join(RADAR, script)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=300)
        if p.returncode:
            return {"ok": False, "erreur": (p.stderr or p.stdout)[-600:]}
    return {"ok": True}


def open_agent(message):
    exe = shutil.which("claude") or os.path.join(os.environ["USERPROFILE"], ".local", "bin", "claude.exe")
    if not os.path.exists(exe):
        return {"ok": False, "erreur": "Claude Code est introuvable sur ce PC (commande « claude »)."}
    # no shell: the message is passed as one argument, nothing in it is interpreted as a command
    subprocess.Popen([exe, message] if message else [exe], cwd=CLIP, creationflags=NEW_CONSOLE)
    return {"ok": True}


def safe_clip_path(folder, name=None):
    """Path inside the clipping folder only, or None."""
    if not re.fullmatch(r"[\w .-]+", folder) or "clips" not in folder:
        return None
    full = os.path.join(CLIP, folder)
    if name is not None:
        if not re.fullmatch(r"[\w .-]+\.mp4", name, re.I):
            return None
        full = os.path.join(full, name)
    return full if os.path.exists(full) else None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def local(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_video(self, path):
        size = os.path.getsize(path)
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(size - int(m.group(2)), 0)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 16, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionError):
                pass

    def do_GET(self):
        if not self.local():
            return self.send_error(403)
        path = unquote(self.path.split("?")[0])
        if path == "/":
            html = open(os.path.join(HERE, "index.html"), encoding="utf-8").read().replace("__TOKEN__", TOKEN)
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(body)
        if path == "/api/etat":
            return self.send_json(state())
        if path == "/api/formats":
            return self.send_json(formats_state())
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "planche" and re.fullmatch(r"[0-9a-f-]{36}\.jpg", parts[1]):
            full = os.path.join(FORMATS, "planches", parts[1])
            if os.path.exists(full):
                body = open(full, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                return self.wfile.write(body)
        if len(parts) == 3 and parts[0] == "clip":
            full = safe_clip_path(parts[1], parts[2])
            if full:
                return self.send_video(full)
        self.send_error(404)

    def do_POST(self):
        if not self.local() or self.headers.get("X-Token") != TOKEN:
            return self.send_error(403)
        n = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/maj":
            return self.send_json(update_radar())
        if self.path == "/api/planning":
            plan = load_planning()
            for e in plan:
                if e["id"] == data.get("id") and data.get("etat") in ("prêt", "programmé", "publié", "attente", "brouillon"):
                    e["etat"] = data["etat"]
            json.dump(plan, open(PLANNING, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            return self.send_json({"ok": True, "planning": plan})
        if self.path == "/api/taches":
            tasks = load_tasks()
            if data.get("action") == "ajouter" and str(data.get("texte", "")).strip():
                tasks.append({"id": secrets.token_hex(4), "texte": str(data["texte"]).strip()[:200], "detail": "",
                              "qui": "toi", "fait": False})
            elif data.get("action") == "cocher":
                for t in tasks:
                    if t["id"] == data.get("id"):
                        t["fait"] = bool(data.get("fait"))
            elif data.get("action") == "supprimer":
                tasks = [t for t in tasks if t["id"] != data.get("id")]
            save_tasks(tasks)
            return self.send_json({"ok": True, "taches": tasks})
        if self.path == "/api/formats/maj":
            if not formats_job["en_cours"]:
                threading.Thread(target=update_formats, daemon=True).start()
            return self.send_json({"ok": True})
        if self.path == "/api/suivre":
            ids = [i for i in load_followed() if i != data["id"]]
            if data.get("on"):
                ids.append(data["id"])
            json.dump(ids, open(SUIVI, "w", encoding="utf-8"), indent=1)
            return self.send_json({"ok": True, "suivies": ids})
        if self.path == "/api/agent":
            return self.send_json(open_agent(str(data.get("message", "")).strip()[:4000]))
        if self.path == "/api/ouvrir":
            full = safe_clip_path(str(data.get("dossier", "")))
            if full:
                os.startfile(full)
            return self.send_json({"ok": bool(full)})
        if self.path == "/api/quitter":
            self.send_json({"ok": True})
            return threading.Thread(target=self.server.shutdown, daemon=True).start()
        self.send_error(404)


if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # already running: just show it
        webbrowser.open(url)
        sys.exit(0)
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    server.serve_forever()
