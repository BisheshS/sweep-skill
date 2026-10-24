#!/usr/bin/env python3
"""Text dashboard for chat, and plan maker. Read only.

usage: summary.py scan.json                     print the dashboard (paste it in a code block)
       summary.py scan.json --plan safe         print a plan line for every item marked safe
       summary.py scan.json --plan 3,5,7        print a plan line for those item numbers
       summary.py scan.json --old               list old projects with git verdicts
"""
import json, sys, time

TAG = {"safe": "SAFE ", "check": "CHECK", "ask": "ASK  ", "keep": "KEEP "}
W = 30


def gb(b):
    return "%5.1f GB" % (b / 1e9) if b >= 1e8 else "%5d MB" % (b / 1e6)


def bar(frac, w=W):
    n = max(0, min(w, int(round(frac * w))))
    return "█" * n + "░" * (w - n)


def short(s, n):
    return s if len(s) <= n else s[: n - 1] + "…"


def dashboard(d):
    items, disk = d["items"], d["disk"]
    out = []
    out.append("DISK CLEANUP  ·  scanned %s" % time.strftime("%d %b %H:%M", time.localtime(d["generated"])))
    out.append("")
    out.append(" Disk  %s  %d / %d GB used · %d GB free" % (bar(disk["used"] / disk["total"]), disk["used"] / 1e9, disk["total"] / 1e9, disk["free"] / 1e9))
    tot = {k: sum(i["size"] for i in items if i["risk"] == k) for k in TAG}
    rec = tot["safe"] + tot["check"] + tot["ask"]
    out.append("")
    out.append(" Reclaimable %s  (the %d projects marked KEEP are not counted)" % (gb(rec).strip(), sum(1 for i in items if i["risk"] == "keep")))
    for k, label in (("safe", "Safe: tools rebuild it"), ("check", "Check first: depends on your work"), ("ask", "You decide: personal or lossy")):
        out.append("   [%s] %s %s  %s" % (TAG[k], gb(tot[k]), bar(tot[k] / max(rec, 1), 20), label))
    out.append("   [KEEP ] %s  only copy of your work, never offered" % gb(tot["keep"]))
    out.append("")
    out.append(" INSIGHTS")
    for n, ins in enumerate(d["insights"], 1):
        out.append("  %s %s" % (ins["icon"], ins["title"]))
        out.append("      %s" % short(ins["detail"], 100))
    out.append("")
    out.append(" TOP ITEMS            (reply with numbers, e.g. “do 0, 3, 7”, or “all safe”)")
    out.append("  #    size      tag    what")
    shown = [(n, i) for n, i in enumerate(items) if i["risk"] != "keep"][:15]
    mx = max([i["size"] for _, i in shown] or [1])
    for n, i in shown:
        out.append("  %-3d %s [%s] %s %s" % (n, gb(i["size"]), TAG[i["risk"]], bar(i["size"] / mx, 8), short(i["label"], 52)))
    keep = [(n, i) for n, i in enumerate(items) if i["risk"] == "keep"][:5]
    if keep:
        out.append("")
        out.append(" KEEP: only copy of your work (back these up, I will not touch them)")
        for n, i in keep:
            out.append("   %s  %s  %s" % (gb(i["size"]), short(i["label"], 44), short(i["note"].split(".")[0], 34)))
    safe_n = sum(1 for i in items if i["risk"] == "safe")
    out.append("")
    out.append(" NEXT: “all safe” frees %s across %d items. Or open the visual report to tick items yourself." % (gb(tot["safe"]).strip(), safe_n))
    return "\n".join(out)


def old_projects(d):
    ps = [p for p in d["projects"] if p["age"] >= d["old_days"] and p["size"] >= 50e6]
    ps.sort(key=lambda p: -p["size"])
    out = ["OLD PROJECTS (idle %d+ days, 50 MB+)" % d["old_days"], "", "  size       idle   verdict                      project"]
    for p in ps[:25]:
        if p["restorable"]:
            v = "SAFE: on GitHub, clean"
        else:
            v = "KEEP: " + short(p["trash_reason"] if not p["reasons"] else p["reasons"][0], 22)
        out.append("  %s  %4dd  %-28s %s" % (gb(p["size"]), p["age"], v, short(p["name"], 40)))
    return "\n".join(out)


def offload_map(d):
    """three buckets: delete (rebuilds), move to a hard disk (irreplaceable or personal), keep on the Mac."""
    items = d["items"]
    out = ["MAP: what to delete, what to move to a hard disk, what to keep", ""]
    nonsim = [i for i in items if i["group"] != "Simulators" and i["risk"] in ("safe", "check")]
    safe = [i for i in nonsim if i["risk"] == "safe"]
    chk = [i for i in nonsim if i["risk"] == "check"]
    sims = [i for i in items if i["group"] == "Simulators"]
    runtimes = [i for i in sims if i["label"].startswith("Runtime")]
    devs = [i for i in sims if not i["label"].startswith("Runtime")]
    out.append(" 1. DELETE: it rebuilds by itself (no hard disk needed)")
    out.append("    a) SAFE now: %s  (%d items: caches, DerivedData, idle node_modules)" % (gb(sum(i["size"] for i in safe)).strip(), len(safe)))
    by = {}
    for i in safe:
        by.setdefault(i["group"], 0)
        by[i["group"]] += i["size"]
    for g, b in sorted(by.items(), key=lambda kv: -kv[1]):
        out.append("         %s  %s" % (gb(b), g))
    out.append("    b) CHECK first: %s  (build folders and active projects; wait until your agents are idle)" % gb(sum(i["size"] for i in chk)).strip())
    out.append("    c) Simulators: you keep the 2 runtimes (%s). Extra devices you could drop: %s" % (gb(sum(i["size"] for i in runtimes)).strip(), gb(sum(i["size"] for i in devs)).strip()))
    out.append("")
    files = sorted([i for i in items if i.get("offload") and i["risk"] == "ask"], key=lambda i: -i["size"])
    out.append(" 2. MOVE TO A HARD DISK: personal files you may want later")
    byf = {}
    for i in files:
        byf.setdefault(i.get("folder", "Other"), []).append(i)
    tf = 0
    for fld, lst in sorted(byf.items(), key=lambda kv: -sum(x["size"] for x in kv[1])):
        b = sum(x["size"] for x in lst)
        tf += b
        out.append("    %s  %s" % (gb(b), fld))
        for x in lst[:5]:
            out.append("         %s  %s" % (gb(x["size"]), short(x["label"].split(" / ", 1)[-1], 54)))
        if len(lst) > 5:
            out.append("         … %d more" % (len(lst) - 5))
    out.append("    total personal files: %s" % gb(tf).strip())
    out.append("")
    projs = [i for i in items if i["risk"] == "keep" and i.get("offload")]
    out.append(" 3. OLD PROJECTS THAT EXIST ONLY ON THIS MAC (idle 30+ days)")
    out.append("    Back up the source (small), then the whole folder can leave the Mac.")
    out.append("    frees    source   idle   where to back it up                project")
    tfree = tsrc = 0
    for i in sorted(projs, key=lambda i: -i["size"])[:15]:
        src = i.get("source_size", i["size"])
        tfree += i["size"]
        tsrc += src
        if i.get("is_repo") and i.get("has_remote"):
            how = "git push (free, GitHub)"
        elif src < 100 * 1024 * 1024:
            how = "tiny: GitHub or iCloud/Drive zip"
        else:
            how = "GitHub repo, or hard disk"
        out.append("    %s %s %5s  %-33s %s" % (gb(i["size"]), gb(src), i["note"].split("Idle ")[-1].split(" ")[0] + "d", how, short(i["label"], 36)))
    out.append("")
    act = [p for p in d["projects"] if p["state"] == "active"]
    out.append(" 4. KEEP ON THE MAC: %d active projects (%s) and the 2 simulator runtimes you use" % (len(act), gb(sum(p["size"] for p in act)).strip()))
    out.append("")
    out.append(" ADDS UP TO")
    out.append("    Delete safe now ........ %s" % gb(sum(i["size"] for i in safe)).strip())
    out.append("    Move personal files .... %s   (needs a hard disk with that much space)" % gb(tf).strip())
    out.append("    Back up old projects ... needs %s, frees %s" % (gb(tsrc).strip(), gb(tfree).strip()))
    return "\n".join(out)


def make_plan(d, spec):
    items = d["items"]
    if spec == "safe":
        ns = [n for n, i in enumerate(items) if i["risk"] == "safe"]
    else:
        ns = [int(x) for x in spec.split(",") if x.strip()]
    return "DISK-CLEANUP-PLAN " + json.dumps({"v": 1, "scan": d["generated"], "n": ns})


if __name__ == "__main__":
    d = json.load(open(sys.argv[1]))
    if "--plan" in sys.argv:
        print(make_plan(d, sys.argv[sys.argv.index("--plan") + 1]))
    elif "--map" in sys.argv:
        print(offload_map(d))
    elif "--old" in sys.argv:
        print(old_projects(d))
    else:
        print(dashboard(d))
