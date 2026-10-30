"""Measure how the best paid clips are edited: length, cut rhythm, voice, opening frames.

Input: formats/top_clips.json (formats_collect.py). Scope: followed campaigns, French campaigns, gaming campaigns.
Each video is fetched, measured, turned into a strip of 5 frames (formats/planches/<id>.jpg) then deleted.
Output: formats/mesures.json (already measured clips are skipped on the next run).
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "formats")
STRIPS = os.path.join(OUT, "planches")
SUIVI = os.path.join(os.path.dirname(HERE), "agent", "suivi.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129 Safari/537.36"
GAMING = {"gaming", "streamer", "roblox", "kick", "esports"}
PER_CAMPAIGN = 5
LIMIT = 210
NO_WINDOW = 0x08000000


def tool(name):
    p = shutil.which(name)
    if p:
        return p
    hits = glob.glob(os.path.join(os.environ["LOCALAPPDATA"], "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*",
                                  "*", "bin", name + ".exe"))
    if not hits:
        sys.exit(name + " introuvable")
    return hits[0]


FFMPEG, FFPROBE = tool("ffmpeg"), tool("ffprobe")


def run(args):
    return subprocess.run(args, capture_output=True, creationflags=NO_WINDOW)


def measure(clip):
    tmp = os.path.join(tempfile.gettempdir(), "fmt_" + clip["id"] + ".mp4")
    try:
        req = urllib.request.Request(clip["video"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        info = json.loads(run([FFPROBE, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                               "-of", "json", tmp]).stdout)
        dur = float(info["format"]["duration"])
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        has_audio = any(s["codec_type"] == "audio" for s in info["streams"])

        # a "cut" is a frame that differs strongly from the previous one
        err = run([FFMPEG, "-v", "info", "-i", tmp, "-an", "-vf", "scale=180:-2,select='gt(scene,0.30)',showinfo",
                   "-f", "null", "-"]).stderr.decode("utf-8", "replace")
        cuts = [float(t) for t in re.findall(r"pts_time:([\d.]+)", err)]

        # opening seconds matter most: 3 of the 5 frames are taken there
        times = [0.0, min(1.0, dur * .1), min(3.0, dur * .25), dur * .5, dur * .85]
        tiles = []
        for t in times:
            raw = run([FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", tmp, "-frames:v", "1", "-vf",
                       "scale=216:384:force_original_aspect_ratio=decrease,pad=216:384:(ow-iw)/2:(oh-ih)/2",
                       "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout
            if len(raw) == 216 * 384 * 3:
                tiles.append(Image.frombytes("RGB", (216, 384), raw))
        if tiles:
            strip = Image.new("RGB", (216 * len(tiles) + 4 * (len(tiles) - 1), 384), "white")
            for i, im in enumerate(tiles):
                strip.paste(im, (i * 220, 0))
            strip.save(os.path.join(STRIPS, clip["id"] + ".jpg"), quality=78)

        pcm = b""
        if has_audio:
            pcm = run([FFMPEG, "-v", "error", "-i", tmp, "-t", "20", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"]).stdout
        return {"id": clip["id"], "duree": round(dur, 1), "largeur": video["width"], "hauteur": video["height"],
                "coupes": len(cuts), "coupes_3s": sum(1 for t in cuts if t <= 3),
                "coupes_par_10s": round(len(cuts) / dur * 10, 2) if dur else None, "son": has_audio}, pcm
    except Exception as e:  # a dead link must not stop the batch
        return {"id": clip["id"], "erreur": str(e)[:200]}, b""
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main():
    clips = json.load(open(os.path.join(OUT, "top_clips.json"), encoding="utf-8"))
    followed = set(json.load(open(SUIVI, encoding="utf-8"))) if os.path.exists(SUIVI) else set()
    path = os.path.join(OUT, "mesures.json")
    done = {m["id"]: m for m in json.load(open(path, encoding="utf-8"))} if os.path.exists(path) else {}
    os.makedirs(STRIPS, exist_ok=True)

    scope = [c for c in clips if c["video"] and (
        c["campagne_id"] in followed or (c["rang"] <= PER_CAMPAIGN and (c["langue"] == "fr" or GAMING & set(c["categories"]))))]
    scope.sort(key=lambda c: (c["campagne_id"] not in followed, -c["vues"]))
    todo = [c for c in scope[:LIMIT] if c["id"] not in done]
    print(f"{len(scope[:LIMIT])} clips dans le périmètre, {len(todo)} à mesurer", file=sys.stderr)
    if not todo:
        return

    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8")
    with ThreadPoolExecutor(4) as pool:
        for n, (m, pcm) in enumerate(pool.map(measure, todo), 1):
            if len(pcm) > 16000:
                audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
                segs, info = model.transcribe(audio, vad_filter=True, beam_size=1)
                segs = list(segs)
                m["voix_langue"] = info.language if segs else None
                m["voix_debut"] = segs[0].start if segs else None
                # share of the first 20 seconds where someone speaks
                m["voix_part"] = round(sum(s.end - s.start for s in segs) / (len(pcm) / 32000), 2)
                m["voix_texte"] = " ".join(s.text.strip() for s in segs)[:300]
            done[m["id"]] = m
            print(f"{n}/{len(todo)} {m.get('duree', m.get('erreur'))}", file=sys.stderr)
            if n % 10 == 0:
                json.dump(list(done.values()), open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(list(done.values()), open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(len(done))


if __name__ == "__main__":
    main()
