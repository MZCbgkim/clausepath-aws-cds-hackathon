"""Review material for the evaluators of a cut (they cannot play video): one frame per second, a contact sheet per
scene (a frame every 2 s, timestamped), the timeline with each narration sentence's text and times, audio stats.

Run: .venv/bin/python demo/make_review.py [demo/edit/rough_cut.mp4]
Out: demo/edit/review/{frames/fNNN.png, sheets/sceneN.png, timeline.json, audio_stats.txt}
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
EDIT = ROOT / "demo" / ("edit_ko" if os.environ.get("DEMO_LANG") == "ko" else "edit")
REV = EDIT / "review"


def sentences():
    blk = (ROOT / "docs" / ("narration-ko.md" if os.environ.get("DEMO_LANG") == "ko" else "narration-en.md")).read_text()
    blk = blk[blk.index("```xml") + 6: blk.index("```", blk.index("```xml") + 6)]
    out = {}
    for line in blk.strip().splitlines():
        m = re.match(r'<!-- (\w+) --><speak><prosody rate="\d+%">(.*)</prosody></speak>', line.strip())
        if m:
            for i, part in enumerate(re.split(r'<break time="\d+ms"/>', m.group(2)), 1):
                out[f"{m.group(1)}.{i}"] = re.sub(r"<[^>]+>", "", re.sub(r'<sub alias="([^"]+)">[^<]*</sub>', r"\1", part)).strip()
    return out


def main():
    video = Path(sys.argv[1]) if len(sys.argv) > 1 else EDIT / "rough_cut.mp4"
    shutil.rmtree(REV, ignore_errors=True)
    (REV / "frames").mkdir(parents=True)
    (REV / "sheets").mkdir()
    tl = json.loads((EDIT / "timeline.json").read_text())
    text = sentences()
    total = tl["total"]
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", "fps=1,scale=960:-2", "-start_number", "1",
                    str(REV / "frames" / "f%03d.png")], check=True)  # fNNN = the frame at NNN-0.5 s
    narr = [{"key": k, "start": s, "end": e, "text": text.get(k, "")} for k, s, e in tl["audio"]]
    # a scene starts at the start of the shot its first sentence falls in (scene 1 at 0)
    scenes, shots = {}, tl["shots"]
    for n in narr:
        sc = re.match(r"\d+", n["key"]).group()
        sc = "1" if sc == "0" else sc  # the cold open's line belongs to scene 1
        if sc not in scenes:
            scenes[sc] = 0.0 if sc == "1" else max(s for s in shots if s <= n["start"] + 0.01)
    keys = sorted(scenes, key=int)
    bounds = {k: (scenes[k], scenes[keys[i + 1]] if i + 1 < len(keys) else total) for i, k in enumerate(keys)}
    (REV / "timeline.json").write_text(json.dumps({"total": round(total, 2), "scenes": {k: [round(a, 2), round(b, 2)] for k, (a, b) in bounds.items()},
                                                   "shot_starts": shots, "narration": narr}, indent=1, ensure_ascii=False))
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 22)
    for k, (a, b) in bounds.items():
        ts = [t for t in range(int(a), int(b) + 1, 2) if t < total]
        cols, w, h = 4, 480, 270
        sheet = Image.new("RGB", (cols * w, ((len(ts) + cols - 1) // cols) * (h + 30)), (255, 255, 255))
        for i, t in enumerate(ts):
            fr = REV / "frames" / f"f{t + 1:03d}.png"
            if not fr.exists():
                continue
            im = Image.open(fr).resize((w, h))
            x, y = (i % cols) * w, (i // cols) * (h + 30)
            sheet.paste(im, (x, y + 30))
            ImageDraw.Draw(sheet).text((x + 6, y + 3), f"{t // 60}:{t % 60:02d}", font=font, fill=(0, 0, 0))
        sheet.save(REV / "sheets" / f"scene{k}.png")
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(video), "-af", "silencedetect=n=-40dB:d=1.5,volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    lines = [ln.split("] ", 1)[-1] for ln in r.splitlines() if "silence_" in ln or "mean_volume" in ln or "max_volume" in ln]
    (REV / "audio_stats.txt").write_text(f"total {total:.1f} s\n" + "\n".join(lines) + "\n")
    print(REV, len(list((REV / "frames").glob("*.png"))), "frames", list(bounds.items()))


if __name__ == "__main__":
    main()
