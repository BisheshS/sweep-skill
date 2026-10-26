#!/usr/bin/env python3
"""Draw demo.gif from the real tool output that run_demo.sh recorded.
Needs Pillow (maintainers only): python3 -m venv v && v/bin/pip install pillow

usage: render_gif.py <work dir used by run_demo.sh> <out.gif>
The only edit made to the recorded text is to show the fake home folder as "~".
"""
import os, re, sys
from PIL import Image, ImageDraw, ImageFont

W, OUT = sys.argv[1], sys.argv[2]
HOME = os.path.join(os.path.realpath(W), "home")
FONT = "/System/Library/Fonts/Menlo.ttc"
SIZE, COLS, ROWS, PAD = 13, 104, 42, 18
reg, bold = ImageFont.truetype(FONT, SIZE, index=0), ImageFont.truetype(FONT, SIZE, index=1)
cw = int(reg.getlength("M") + 0.5)
lh = SIZE + 5
IMG_W, IMG_H = COLS * cw + 2 * PAD, ROWS * lh + 2 * PAD + 26
BG, FG, DIM = (22, 24, 30), (214, 218, 226), (122, 130, 146)
COL = {"SAFE": (92, 214, 142), "CHECK": (240, 196, 80), "ASK": (245, 150, 90), "KEEP": (110, 170, 250),
       "OK": (92, 214, 142), "SKIP": (240, 196, 80), "FAIL": (240, 100, 100), "DRY": (150, 160, 180),
       "TOKEN": (110, 220, 230), "prompt": (190, 150, 250)}


def read(name):
    t = open(os.path.join(W, "out", name)).read().replace(os.path.realpath(HOME), "~").replace(HOME, "~")
    t = re.sub(r"/private/tmp/\S*?/home", "~", t)
    return [l[:COLS] for l in t.rstrip("\n").split("\n")]


def color_runs(line):
    """split a line into (text, color, bold) pieces."""
    m = re.match(r"^(\s*\d*\s*)(\S.*)$", line)
    runs, i = [], 0
    for mt in re.finditer(r"\[(SAFE |CHECK|ASK  |KEEP )\]|\b(SKIP|OK  |FAIL|DRY |TOKEN)\b|^(DISK CLEANUP|MODE:.*|DISK-CLEANUP-PLAN.*)$", line):
        if mt.start() > i:
            runs.append((line[i:mt.start()], FG, False))
        key = (mt.group(1) or mt.group(2) or "").strip()
        if mt.group(3):
            runs.append((mt.group(0), (255, 255, 255), True))
        else:
            runs.append((mt.group(0), COL.get(key, FG), True))
        i = mt.end()
    runs.append((line[i:], FG, False))
    return runs


def frame(lines, caption, cursor=False):
    im = Image.new("RGB", (IMG_W, IMG_H), BG)
    d = ImageDraw.Draw(im)
    for k, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        d.ellipse([PAD + k * 20, 11, PAD + k * 20 + 12, 23], fill=c)
    d.text((IMG_W // 2 - 150, 9), caption, font=reg, fill=DIM)
    y = 26 + PAD
    for ln in lines[-ROWS:]:
        x = PAD
        if ln.startswith("❯ "):
            d.text((x, y), "❯", font=bold, fill=COL["prompt"])
            d.text((x + 2 * cw, y), ln[2:], font=bold, fill=(255, 255, 255))
        else:
            for txt, col, b in color_runs(ln):
                d.text((x, y), txt, font=bold if b else reg, fill=col)
                x += cw * len(txt)
        y += lh
    if cursor:
        cy = 26 + PAD + min(len(lines), ROWS) * lh - lh
        d.rectangle([PAD + cw * (len(lines[-1])), cy + 1, PAD + cw * (len(lines[-1]) + 1) - 2, cy + lh - 3], fill=(230, 230, 240))
    return im


frames, durs = [], []
marks = []
buf = []
caption = "demo: made-up files in a fake home folder"


def snap(ms, cursor=False):
    frames.append(frame(buf, caption, cursor)); durs.append(ms)


def title(lines, ms):
    global buf
    buf = [""] * 6 + lines
    snap(ms)
    buf = []


def type_cmd(text, ms_end=500):
    buf.append("❯ ")
    step = max(1, len(text) // 5)
    for k in range(step, len(text) + step, step):
        buf[-1] = "❯ " + text[:k]
        snap(120, True)
    snap(ms_end)


def out(lines, chunk=7, ms=110, hold=1400):
    for k in range(0, len(lines), chunk):
        buf.extend(lines[k:k + chunk])
        snap(ms)
    snap(hold)


title(["      sweep-skill", "      a safe disk cleanup skill for Claude Code", "",
       "      this demo runs the real tool on a fake home folder",
       "      every file name here is made up"], 2600)
type_cmd("free up space")
out(read("1_dashboard.txt"), chunk=6, hold=2800)
marks.append(len(frames) - 1)
buf.clear()
type_cmd("delete the only copy of side-hustle")
out(read("4_keep_refused.txt")[:5], hold=2200)
buf.clear()
type_cmd("ok. clean the caches and the old project that is on GitHub")
out(read("3_dryrun.txt"), chunk=5, hold=3200)
buf.append("")
type_cmd("yes")
out(read("5_execute.txt")[:-2], chunk=5, hold=3600)
marks.append(len(frames) - 1)
title(["      nothing runs without a dry run and a token", "      your only copy of a project is never offered",
       "      old pushed projects go to the Trash, not rm", "",
       "      github.com/BisheshS/sweep-skill"], 3800)

# one shared palette keeps the file small
pal = frames[1].convert("P", palette=Image.ADAPTIVE, colors=64)
q = [f.quantize(palette=pal, dither=Image.NONE) for f in frames]
q[0].save(OUT, save_all=True, append_images=q[1:], duration=durs, loop=0, optimize=True, disposal=1)
print("wrote", OUT, "%d frames, %.0f KB" % (len(q), os.path.getsize(OUT) / 1024))
for k, m in enumerate(marks):
    frames[m].save(OUT.replace(".gif", "-preview%d.png" % k))
