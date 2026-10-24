#!/usr/bin/env python3
"""Build the GUI report: embeds scan.json into assets/report.html. Local file only,
nothing is uploaded. usage: build_report.py --scan scan.json --out report.html [--open]
"""
import argparse, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--scan", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--open", action="store_true", help="open the report in the default browser")
a = ap.parse_args()

tpl = open(os.path.join(HERE, "..", "assets", "report.html"), encoding="utf-8").read()
scan = json.load(open(a.scan, encoding="utf-8"))
# Paths in the page are only used as hover titles. The page never executes anything.
blob = json.dumps(scan).replace("</", "<\\/")
if "/*SCAN_JSON*/null" not in tpl:
    sys.exit("template marker not found")
open(a.out, "w", encoding="utf-8").write(tpl.replace("/*SCAN_JSON*/null", blob, 1))
print("wrote", a.out)
if a.open:
    subprocess.run(["open", a.out])
