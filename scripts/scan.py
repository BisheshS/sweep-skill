#!/usr/bin/env python3
"""disk-cleanup scan: READ ONLY. Finds projects and rebuildable space, judges how
important each project is, and writes scan.json. Deletes nothing.

usage: scan.py --out scan.json [--roots DIR ...] [--stale-days 180] [--old-days 365]
"""
import argparse, json, os, shutil, sys, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (HOME, DAY, ARTIFACTS, run, is_project_dir, git_info, activity, live_cwds, is_live,
                    protected_by_user, tracked_files_inside, artifact_kind_ok, trash_project_ok)

NOW = time.time()
MIN_ART = 100 * 1024 * 1024
MIN_PROJ = 50 * 1024 * 1024
DEFAULT_ROOTS = ["Projects", "Developer", "Code", "code", "dev", "src", "work", "repos", "IdeaProjects",
                 "Documents", "Desktop", "Practice", "GitHub", "git"]
ITEMS = []
SKIP = set()
PROJ_PATHS = []


def du(path):
    if not os.path.exists(path):
        return 0
    rc, out = run(["du", "-sk", path], 240)
    try:
        return int(out.split()[0]) * 1024
    except Exception:
        return 0


def add(**kw):
    kw.setdefault("note", "")
    ITEMS.append(kw)


def pmap(fn, xs, workers=8):
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, xs))


# ---------- fixed-location caches ----------
def fixed_items():
    P = lambda *a: os.path.join(HOME, *a)
    rm = lambda p: {"type": "rm", "path": p}
    table = [
        ("xcode-derived", "Xcode", "Xcode DerivedData", P("Library/Developer/Xcode/DerivedData"), "safe",
         "Xcode rebuilds it. The first build after is slower. Close Xcode first.", None),
        ("xcode-devsupport", "Xcode", "Xcode iOS DeviceSupport", P("Library/Developer/Xcode/iOS DeviceSupport"), "check",
         "Xcode copies it again from your iPhone the next time you plug it in (5 to 20 minutes). Keep it if you test on a cable-connected device.", None),
        ("xcode-archives", "Xcode", "Xcode Archives", P("Library/Developer/Xcode/Archives"), "ask",
         "Holds dSYM files for apps you shipped. Keep it if you need crash symbols.", None),
        ("coresim-caches", "Xcode", "CoreSimulator caches", P("Library/Developer/CoreSimulator/Caches"), "safe",
         "Xcode rebuilds it.", None),
        ("npm", "Package caches", "npm cache", P(".npm/_cacache"), "safe", "npm downloads packages again when needed.",
         {"type": "cmd", "argv": ["npm", "cache", "clean", "--force"], "needs": "npm"}),
        ("yarn", "Package caches", "Yarn cache", P("Library/Caches/Yarn"), "safe", "Yarn downloads again when needed.", None),
        ("pnpm", "Package caches", "pnpm store", P("Library/pnpm/store"), "safe",
         "pnpm downloads again. Projects may need 'pnpm install'.", None),
        ("bun", "Package caches", "Bun cache", P(".bun/install/cache"), "safe", "Bun downloads again when needed.", None),
        ("pip", "Package caches", "pip cache", P("Library/Caches/pip"), "safe", "pip downloads again when needed.", None),
        ("cargo", "Package caches", "Cargo registry", P(".cargo/registry"), "safe", "Cargo downloads crates again.", None),
        ("gomod", "Package caches", "Go module cache", P("go/pkg/mod"), "safe", "Go downloads modules again.",
         {"type": "cmd", "argv": ["go", "clean", "-modcache"], "needs": "go"}),
        ("homebrew", "Package caches", "Homebrew downloads", P("Library/Caches/Homebrew"), "safe", "brew downloads again.",
         {"type": "cmd", "argv": ["brew", "cleanup", "--prune=all", "-s"], "needs": "brew"}),
        ("cocoapods", "Package caches", "CocoaPods cache", P("Library/Caches/CocoaPods"), "safe", "pod install downloads again.", None),
        ("gradle", "Package caches", "Gradle caches", P(".gradle/caches"), "check", "Gradle downloads again. The next Android build is slow.", None),
        ("trash", "Trash and backups", "Trash", P(".Trash"), "check", "This cannot be undone. Look in the Trash first.", None),
        ("ios-backups", "Trash and backups", "iPhone/iPad backups", P("Library/Application Support/MobileSync/Backup"), "ask",
         "Device backups. Delete only if you have a newer backup.", None),
        ("logs", "Trash and backups", "Library/Logs", P("Library/Logs"), "check", "Mostly logs, but also some app state (Wi-Fi list, Claude logs, crash reports). Frees little.", None),
    ]
    sizes = pmap(lambda r: du(r[3]), table)
    for (id_, group, label, path, risk, rebuild, action), sz in zip(table, sizes):
        if sz < 20 * 1024 * 1024:
            continue
        if action and action.get("needs") and not shutil.which(action["needs"]):
            action = None
        add(id=id_, group=group, label=label, path=path, size=sz, risk=risk, rebuild=rebuild, action=action or rm(path))


def app_cache_items():
    base = os.path.join(HOME, "Library", "Caches")
    try:
        paths = [os.path.join(base, n) for n in sorted(os.listdir(base)) if os.path.isdir(os.path.join(base, n))]
    except OSError:
        return
    skip = {"Homebrew", "CocoaPods", "pip", "Yarn"}
    user_state = {"JetBrains": "Also holds IDE local history and per-project workspace files. These cannot be rebuilt."}
    paths = [p for p in paths if os.path.basename(p) not in skip]
    for p, sz in zip(paths, pmap(du, paths)):
        if sz >= 300 * 1024 * 1024:
            add(id="cache:" + os.path.basename(p), group="App caches", label=os.path.basename(p), path=p, size=sz,
                risk="check" if os.path.basename(p) in user_state else "safe",
                rebuild=user_state.get(os.path.basename(p), "The app rebuilds its cache. It may start slower once. Quit the app first." if os.path.basename(p) == "Google" else "The app rebuilds its cache. It may start slower once."),
                action={"type": "rm", "path": p})


# ---------- simulators ----------
def simulator_items():
    if not shutil.which("xcrun"):
        return
    try:
        runtimes = json.loads(run(["xcrun", "simctl", "runtime", "list", "-j"])[1])
        devices = json.loads(run(["xcrun", "simctl", "list", "devices", "-j"])[1])["devices"]
    except Exception:
        return
    pretty = lambda s: s.replace("-", " ", 1).replace("-", ".")
    by_rt = {}
    for key, lst in devices.items():
        by_rt.setdefault(key.split("SimRuntime.")[-1], []).extend(lst)
    for rid, r in runtimes.items():
        ident = r.get("runtimeIdentifier", "").split("SimRuntime.")[-1]
        devs = by_rt.get(ident, [])
        dev_bytes = sum(d.get("dataPathSize", 0) for d in devs)
        booted = [d["name"] for d in devs if d.get("state") == "Booted"]
        last = max([d.get("lastBootedAt", "") for d in devs] or [""])[:10] or "never"
        add(id="sim-runtime:" + rid, group="Simulators", label="Runtime " + pretty(ident), path=None,
            size=r.get("sizeBytes", 0) + dev_bytes, risk="check" if booted else "ask",
            rebuild="Xcode downloads a runtime again if you need it (about 8 GB).",
            note="%d devices (%.1f GB). Last booted %s.%s" % (len(devs), dev_bytes / 1e9, last,
                                                                 " Booted now: " + ", ".join(booted) + " (skipped)." if booted else ""),
            last_used=last, action={"type": "simctl_runtime", "runtime_id": rid,
                                    "udids": [d["udid"] for d in devs if d.get("state") != "Booted"]})
    small = []
    for ident, devs in by_rt.items():
        for d in devs:
            if d.get("state") == "Booted":
                continue
            sz = d.get("dataPathSize", 0)
            if sz >= 300 * 1024 * 1024:
                last = d.get("lastBootedAt", "")[:10] or "never"
                add(id="sim-device:" + d["udid"], group="Simulators", label="%s (%s)" % (d["name"], pretty(ident)), path=None,
                    size=sz, risk="ask", rebuild="Apps and data in this simulator are lost. Xcode makes a new one when needed.",
                    note="Last booted %s." % last, last_used=last, action={"type": "simctl_device", "udid": d["udid"]})
            else:
                small.append(d)
    if small:
        add(id="sim-device:small", group="Simulators", label="%d small simulator devices" % len(small), path=None,
            size=sum(d.get("dataPathSize", 0) for d in small), risk="ask",
            rebuild="Small devices (under 300 MB each). Recreated on demand.", note="",
            action={"type": "simctl_devices", "udids": [d["udid"] for d in small]})


# ---------- projects ----------
def find_projects(roots):
    """top-most project folders under the roots."""
    out, seen = [], set()
    for root in roots:
        root = os.path.realpath(os.path.expanduser(root))
        if not os.path.isdir(root) or root in seen:
            continue
        seen.add(root)
        base = root.count("/")
        for dp, dns, _ in os.walk(root, topdown=True):
            if dp != root and is_project_dir(dp):
                out.append(dp)
                dns[:] = []
                continue
            dns[:] = [d for d in dns if not d.startswith(".") and d not in ("Library", "node_modules", "Pods", "build")
                      and dp.count("/") - base < 4]
    return sorted(set(out))


def find_artifacts(proj):
    found = []
    base = proj.count("/")
    for dp, dns, _ in os.walk(proj, topdown=True):
        keep = []
        for d in dns:
            if d in ARTIFACTS:
                found.append(os.path.join(dp, d))
            elif not d.startswith(".") or d in (".next", ".venv"):
                if dp.count("/") - base < 4 and d != ".git":
                    keep.append(d)
        dns[:] = keep
    return found


def analyse_project(proj, cwds, stale_days, old_days):
    g = git_info(proj)
    act = activity(proj, g)
    age = int((NOW - act) / DAY) if act else 9999
    live = is_live(proj, cwds)
    keep = protected_by_user(proj)
    state = "active" if age < 30 else ("warm" if age < stale_days else "stale")
    reasons = []
    if keep:
        reasons.append("you marked it as keep")
    if not g["is_repo"]:
        reasons.append("no git: the only copy is on this Mac")
    else:
        if g["dirty"]:
            reasons.append("%d uncommitted files" % g["dirty_count"])
        if g["unpushed"] != 0:
            reasons.append("commits not on a remote" if g["unpushed"] > 0 else "push state unknown")
        if not g["has_remote"]:
            reasons.append("no git remote")
        if g["stash"]:
            reasons.append("stashed changes")
    ok_trash, why_trash = trash_project_ok(proj, old_days, cwds) if not keep else (False, "protected")
    arts = []
    for a in find_artifacts(proj):
        arts.append({"path": a, "size": du(a)})
    return {"path": proj, "name": os.path.relpath(proj, HOME), "git": g, "age": age, "state": state, "live": live,
            "keep": keep, "only_copy": bool(reasons), "reasons": reasons, "restorable": ok_trash, "trash_reason": why_trash,
            "artifacts": arts, "activity": act}


def project_items(roots, stale_days, old_days):
    projs = find_projects(roots)
    cwds = live_cwds()
    print("  %d projects found" % len(projs), file=sys.stderr)
    PROJ_PATHS[:] = projs
    infos = pmap(lambda p: analyse_project(p, cwds, stale_days, old_days), projs, 6)
    sizes = pmap(du, [i["path"] for i in infos])
    for i, s in zip(infos, sizes):
        i["size"] = s
        i["artifact_size"] = sum(a["size"] for a in i["artifacts"])
    for i in infos:
        for a in i["artifacts"]:
            if a["size"] < MIN_ART or not artifact_kind_ok(a["path"]):
                continue
            kind = os.path.basename(a["path"])
            sib = set(os.listdir(os.path.dirname(a["path"])))
            has_lock = bool(sib & {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock",
                                   "Podfile.lock", "poetry.lock", "uv.lock", "Cargo.lock"})
            tracked = tracked_files_inside(a["path"])
            if tracked:
                continue  # git tracks files in here: it is source, not an artifact
            stale = i["state"] == "stale"
            if kind == "build":
                risk, why = "check", "Build output. It rebuilds with your build tool."
            elif kind in (".venv", "venv"):
                risk, why = ("safe" if stale else "check"), "Python environment. Recreate it from requirements."
            elif kind == ".next":
                risk, why = ("safe" if stale else "check"), "Next.js build cache. 'next build' recreates it."
            else:
                risk = "safe" if (stale and has_lock) else "check"
                why = {"node_modules": "Run 'npm install' (or yarn/pnpm/bun) to restore it.", "Pods": "Run 'pod install' to restore it.",
                       "target": "Cargo rebuilds it."}.get(kind, "Rebuilds from the project.")
            if i["live"]:
                risk = "check"
            note = "Project idle %d days." % i["age"] if i["age"] < 9999 else ""
            if i["live"]:
                note += " A process is running in this project now."
            if not has_lock and kind in ("node_modules", "Pods"):
                note += " No lockfile: versions may change on reinstall."
            add(id="art:" + a["path"], group="Project folders", label=os.path.relpath(os.path.dirname(a["path"]), HOME) + " / " + kind,
                path=a["path"], size=a["size"], risk=risk, rebuild=why, note=note.strip(), project=i["path"], project_age=i["age"],
                action={"type": "rm", "path": a["path"]})
        # whole project: restorable from git
        if i["restorable"] and i["size"] >= MIN_PROJ:
            add(id="proj:" + i["path"], group="Old projects (restorable from git)", label=i["name"], path=i["path"], size=i["size"],
                risk="ask", rebuild="Moves the folder to the Trash. Restore with 'git clone' or from the Trash.",
                note="Idle %d days. Clean, fully pushed (as of last fetch). Remote: %s" % (i["age"], i["git"].get("remote_url") or "?"), project=i["path"], project_age=i["age"],
                action={"type": "trash_project", "path": i["path"]})
        elif i["only_copy"] and i["size"] >= MIN_PROJ:
            add(id="keep:" + i["path"], group="Keep: only copy of your work", label=i["name"], path=i["path"], size=i["size"],
                risk="keep", offload=(i["state"] != "active"), source_size=max(0, i["size"] - i["artifact_size"]), has_remote=i["git"].get("has_remote", False), is_repo=i["git"].get("is_repo", False), rebuild="Not offered for deletion.", note="; ".join(i["reasons"]).capitalize() + ". Idle %d days." % i["age"],
                project=i["path"], project_age=i["age"], action=None)
    return infos


# ---------- user files (ask) ----------
def user_file_items():
    """big personal files and folders (not projects). They go to the Trash or to a hard disk, never rm."""
    skip_projects = set()
    for d in ("Downloads", "Movies", "Pictures", "Music", "Desktop", "Documents"):
        base = os.path.join(HOME, d)
        if not os.path.isdir(base):
            continue
        entries = [os.path.join(base, n) for n in os.listdir(base) if not n.startswith(".")]
        entries = [p for p in entries if not (os.path.isdir(p) and (is_project_dir(p) or any(q.startswith(p + os.sep) for q in PROJ_PATHS)))]
        for p, sz in zip(entries, pmap(du, entries)):
            ext = os.path.splitext(p)[1].lower()
            inst = d == "Downloads" and ext in (".dmg", ".pkg", ".iso", ".ipsw") and sz >= 50 * 1024 * 1024
            if sz >= 300 * 1024 * 1024 or inst:
                age = int((NOW - os.stat(p).st_mtime) / DAY)
                add(id="file:" + p, group="Your files (you decide)", label="%s / %s" % (d, os.path.basename(p)), path=p, size=sz,
                    risk="ask", rebuild="Moves to the Trash, or to a hard disk. You can restore it until you empty the Trash.",
                    note="Changed %d days ago.%s%s" % (age, " Installer file." if inst else "", " Move it with the Photos app (Photos closed), not by dragging files." if p.endswith(".photoslibrary") else ""), offload=True, folder=d,
                    action={"type": "trash", "path": p})


# ---------- insights ----------
def gbs(b):
    return "%.1f GB" % (b / 1e9) if b >= 1e8 else "%d MB" % (b / 1e6)


def temp_leak_insight():
    """many same-named folders in the system temp dir (a test tool that never cleans up). Insight only: outside ~, never deleted by this tool."""
    import glob, re, collections
    out = []
    for T in glob.glob("/private/var/folders/*/*/T"):
        try:
            names = [n for n in os.listdir(T) if os.path.isdir(os.path.join(T, n))]
        except OSError:
            continue
        groups = collections.defaultdict(list)
        for n in names:
            groups[re.sub(r"[-_.][A-Za-z0-9]{4,8}$", "", n)].append(os.path.join(T, n))
        for pre, ps in groups.items():
            if len(ps) < 10:
                continue
            b = sum(pmap(du, ps))
            if b >= 2 * 1024 ** 3:
                out.append({"kind": "temp", "icon": "▲", "title": "%d leaked temp folders named %s-* hold %s" % (len(ps), pre, gbs(b)),
                            "detail": "A tool made one copy per run and never deleted it. They sit outside your home folder, so this tool will not delete them. Close the tool, then run: rm -rf %s/%s-*" % (T, pre),
                            "bytes": b, "ids": []})
    return out


def unused_app_insight():
    """big apps not opened for a year. Insight only."""
    old = []
    cut = time.time() - 365 * 86400
    base = "/Applications"
    for n in sorted(os.listdir(base)):
        p = os.path.join(base, n)
        if not n.endswith(".app"):
            continue
        rc, out = run(["mdls", "-name", "kMDItemLastUsedDate", "-raw", p], 10)
        try:
            t = time.mktime(time.strptime(out.strip()[:10], "%Y-%m-%d"))
        except ValueError:
            continue
        if time.localtime(t).tm_year < 2000 or t >= cut:
            continue
        old.append((p, t))
    if not old:
        return []
    sizes = pmap(du, [p for p, _ in old])
    rows = sorted([(sz, p, t) for (p, t), sz in zip(old, sizes) if sz >= 300 * 1024 * 1024], reverse=True)
    if not rows:
        return []
    b = sum(r[0] for r in rows)
    return [{"kind": "apps", "icon": "○", "title": "%d apps (%s) not opened for a year" % (len(rows), gbs(b)),
             "detail": "; ".join("%s %s (%s)" % (os.path.basename(p)[:-4], gbs(sz), time.strftime("%b %Y", time.localtime(t))) for sz, p, t in rows[:6]) + ". The last-used date is a Spotlight guess and can be wrong (a daily-use app may look old). Check before you delete an app from /Applications; this tool does not remove apps.",
             "bytes": b, "ids": []}]


def make_insights(projects, old_days):
    ins = []
    def ids(pred):
        return [n for n, it in enumerate(ITEMS) if pred(it)]
    stale_art = ids(lambda it: it["group"] == "Project folders" and it["risk"] == "safe")
    if stale_art:
        b = sum(ITEMS[n]["size"] for n in stale_art)
        ins.append({"kind": "node", "icon": "◆", "title": "%s of rebuildable files in idle projects" % gbs(b),
                    "detail": "%d folders (node_modules, Pods, venvs) in projects idle for months. One install command brings each back. Your code is not touched." % len(stale_art),
                    "bytes": b, "ids": stale_art})
    old = ids(lambda it: it["group"].startswith("Old projects"))
    if old:
        b = sum(ITEMS[n]["size"] for n in old)
        ins.append({"kind": "old", "icon": "◇", "title": "%d old project%s (%s) %s a safe copy on GitHub" % (len(old), "" if len(old) == 1 else "s", gbs(b), "has" if len(old) == 1 else "have"),
                    "detail": "Idle for %d+ days, no uncommitted work, every commit is pushed. They go to the Trash and 'git clone' restores them." % old_days,
                    "bytes": b, "ids": old})
    keep = [p for p in projects if p["only_copy"] and p["size"] >= 50 * 1024 * 1024]
    if keep:
        b = sum(p["size"] for p in keep)
        worst = sorted(keep, key=lambda p: -p["size"])[:3]
        ins.append({"kind": "keep", "icon": "■", "title": "%d project%s (%s) %s only on this Mac" % (len(keep), "" if len(keep) == 1 else "s", gbs(b), "exists" if len(keep) == 1 else "exist"),
                    "detail": "No remote, unpushed commits, or uncommitted changes. Never offered for deletion. Back them up: " + ", ".join(p["name"] for p in worst) + ".",
                    "bytes": b, "ids": ids(lambda it: it["group"].startswith("Keep"))})
    sims = ids(lambda it: it["group"] == "Simulators")
    if sims:
        b = sum(ITEMS[n]["size"] for n in sims)
        ins.append({"kind": "sim", "icon": "▲", "title": "Simulators hold %s" % gbs(b),
                    "detail": "Each iOS runtime is about 8 GB plus device data. The list shows when each one last ran. Remove the OS versions you no longer test on.",
                    "bytes": b, "ids": sims})
    caches = ids(lambda it: it["risk"] == "safe" and it["group"] in ("Xcode", "Package caches", "App caches", "Trash and backups"))
    if caches:
        b = sum(ITEMS[n]["size"] for n in caches)
        ins.append({"kind": "cache", "icon": "●", "title": "%s of caches that rebuild by themselves" % gbs(b),
                    "detail": "DerivedData, package caches, app caches and logs. Tools download or build them again when needed.", "bytes": b, "ids": caches})
    files = ids(lambda it: it["group"].startswith("Your files"))
    if files:
        b = sum(ITEMS[n]["size"] for n in files)
        ins.append({"kind": "files", "icon": "○", "title": "%s of big files in Downloads" % gbs(b),
                    "detail": "Movies and installers. Only you can say if you need them. They go to the Trash, not deleted.", "bytes": b, "ids": files})
    ins += ([] if "temp" in SKIP else temp_leak_insight()) + ([] if "apps" in SKIP else unused_app_insight())
    return ins


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--roots", nargs="*", default=None)
    ap.add_argument("--stale-days", type=int, default=180)
    ap.add_argument("--old-days", type=int, default=365)
    ap.add_argument("--min-artifact-mb", type=float, default=100)
    ap.add_argument("--skip", nargs="*", default=[], choices=["simulators", "temp", "apps"], help="leave out a part of the scan (used by the demo)")
    ap.add_argument("--min-project-mb", type=float, default=50)
    a = ap.parse_args()
    global MIN_ART, MIN_PROJ, SKIP
    SKIP = set(a.skip)
    MIN_ART, MIN_PROJ = a.min_artifact_mb * 1024 * 1024, a.min_project_mb * 1024 * 1024
    roots = a.roots if a.roots else [os.path.join(HOME, r) for r in DEFAULT_ROOTS]
    t0 = time.time()
    st = shutil.disk_usage("/System/Volumes/Data" if os.path.exists("/System/Volumes/Data") else "/")
    projects = []
    for label, fn in (("caches", fixed_items), ("app caches", app_cache_items), ("simulators", simulator_items),
                      ("projects (git state, activity)", lambda: projects.extend(project_items(roots, a.stale_days, a.old_days))),
                      ("downloads", user_file_items)):
        if label == "simulators" and "simulators" in SKIP:
            continue
        print("scanning " + label + "...", file=sys.stderr)
        fn()
    order = sorted(range(len(ITEMS)), key=lambda n: -ITEMS[n]["size"])
    ITEMS[:] = [ITEMS[n] for n in order]
    insights = make_insights(projects, a.old_days)
    json.dump({"generated": int(NOW), "home": HOME, "disk": {"total": st.total, "used": st.used, "free": st.free},
               "stale_days": a.stale_days, "old_days": a.old_days, "roots": roots, "items": ITEMS, "insights": insights,
               "projects": [{k: v for k, v in p.items() if k != "artifacts"} for p in projects]},
              open(a.out, "w"), indent=1)
    tot = sum(i["size"] for i in ITEMS if i["risk"] != "keep")
    print("done in %.0fs: %d items, %.1f GB reclaimable, %d projects -> %s" % (time.time() - t0, len(ITEMS), tot / 1e9, len(projects), a.out))


if __name__ == "__main__":
    main()
