#!/usr/bin/env python3
"""Apply a disk-cleanup plan. DRY RUN unless --yes AND the matching --token.

usage: apply.py --scan scan.json --plan 'DISK-CLEANUP-PLAN {...}'                 (dry run, prints a token)
       apply.py --scan scan.json --plan '...' --yes --token <token>             (execute)

Safety design: scan.json is NOT trusted. For every item this script re-derives from the
disk that the target is an allowed kind of thing, then re-checks the facts that matter
(git state, running processes, changes since the scan).
"""
import argparse, hashlib, json, os, shutil, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (HOME, DAY, UDID_RE, run, git_info, activity, live_cwds, is_live, protected_by_user,
                    tracked_files_inside, artifact_kind_ok, trash_project_ok, is_project_dir)

LOG_DIR = os.path.join(HOME, ".disk-cleanup", "logs")
P = lambda *a: os.path.join(HOME, *a)
# exact cache folders this tool may remove (the folder is emptied or removed, never its parents)
KNOWN_CACHES = {P(x) for x in (
    "Library/Developer/Xcode/DerivedData", "Library/Developer/Xcode/iOS DeviceSupport",
    "Library/Developer/CoreSimulator/Caches", "Library/Caches/Yarn", "Library/pnpm/store", ".bun/install/cache",
    "Library/Caches/pip", ".cargo/registry", "go/pkg/mod", "Library/Caches/Homebrew", "Library/Caches/CocoaPods",
    ".gradle/caches", ".Trash", "Library/Logs", ".npm/_cacache")}
CLEAR_CONTENTS_ONLY = {P(".Trash"), P("Library/Logs"), P("Library/Developer/Xcode/DerivedData")}
ALLOWED_CMDS = [["npm", "cache", "clean", "--force"], ["go", "clean", "-modcache"], ["brew", "cleanup", "--prune=all", "-s"]]
FORBIDDEN = {HOME} | {P(x) for x in ("Library", "Documents", "Desktop", "Downloads", "Pictures", "Movies", "Music",
                                      "Applications", "Public", ".ssh", ".gnupg", ".claude", ".config", ".local", "Projects",
                                      "Library/Keychains", "Library/Application Support", "Library/Caches", "Library/Developer",
                                      "Library/Mobile Documents", "Library/Messages", "Library/Mail")}
LOG = []
DEST = None
RUNNING_BLOCKS = {"Google": "Google Chrome"}   # cache folder name -> process that must be closed first


def nearest_project(path):
    cur = os.path.dirname(path)
    while cur.startswith(HOME + os.sep):
        if is_project_dir(cur):
            return cur
        cur = os.path.dirname(cur)
    return None


def basic_path_check(path):
    rp = os.path.realpath(path)
    if not rp.startswith(HOME + os.sep):
        return None, "outside your home folder"
    if rp in FORBIDDEN:
        return None, "protected folder"
    if os.path.islink(path):
        return None, "is a symlink"
    if not os.path.exists(rp):
        return None, "already gone"
    if protected_by_user(rp):
        return None, "protected by you (keep marker or protect.txt)"
    return rp, None


def validate(it, scan, cwds):
    """returns (ok, reason). Decides from the DISK, not from scan.json."""
    a = it.get("action")
    if not a:
        return False, "this item cannot be acted on"
    t = a["type"]
    if t == "rm":
        rp, why = basic_path_check(a["path"])
        if not rp:
            return False, why
        if rp in KNOWN_CACHES:
            if os.path.basename(rp) == "DerivedData" and run(["pgrep", "-x", "Xcode"])[0] == 0:
                return False, "Xcode is running; close it first"
            return True, "known cache folder"
        if os.path.dirname(rp) == P("Library", "Caches"):
            proc = RUNNING_BLOCKS.get(os.path.basename(rp))
            if proc and run(["pgrep", "-x", proc])[0] == 0:
                return False, "%s is running; quit it first" % proc
            return True, "app cache folder"
        if artifact_kind_ok(rp):
            if tracked_files_inside(rp):
                return False, "git tracks files in this folder (it is source code)"
            proj = nearest_project(rp)
            if proj:
                if is_live(proj, cwds):
                    return False, "a running process (dev server or agent session) uses this project"
                if activity(proj) > scan["generated"]:
                    return False, "the project changed after the scan"
            return True, "artifact folder (%s)" % os.path.basename(rp)
        return False, "not an allowed cleanup target"
    if t == "trash":
        rp, why = basic_path_check(a["path"])
        if not rp:
            return False, why
        if os.path.dirname(rp) not in {P(x) for x in ("Downloads", "Movies", "Pictures", "Music", "Desktop", "Documents")} or is_project_dir(rp):
            return False, "only big files directly in Downloads, Movies, Pictures, Music, Desktop or Documents can be trashed this way"
        return True, "personal file"
    if t == "trash_project":
        rp, why = basic_path_check(a["path"])
        if not rp:
            return False, why
        ok, why = trash_project_ok(rp, scan.get("old_days", 365), cwds)
        return (ok, "old project, pushed and clean" if ok else why)
    if t == "cmd":
        return (a["argv"] in ALLOWED_CMDS and bool(shutil.which(a["argv"][0])),
                "allowed command" if a["argv"] in ALLOWED_CMDS else "command is not on the allowlist")
    if t in ("simctl_device", "simctl_devices", "simctl_runtime"):
        udids = [a["udid"]] if t == "simctl_device" else a.get("udids", [])
        if not all(UDID_RE.match(u) for u in udids):
            return False, "bad simulator id"
        if t == "simctl_runtime" and not UDID_RE.match(a["runtime_id"]):
            return False, "bad runtime id"
        return True, "simulator"
    return False, "unknown action"


def check_dest(dest):
    """the pendrive: a mounted external volume, writable, not the startup disk. Returns (path, error)."""
    if not dest:
        return None, None
    rp = os.path.realpath(dest)
    if os.path.dirname(rp) != "/Volumes" or not os.path.ismount(rp):
        return None, "--to must be a mounted drive such as /Volumes/NAME"
    if os.path.realpath("/") == rp or os.path.samefile(rp, "/System/Volumes/Data"):
        return None, "--to is the startup disk"
    if not os.access(rp, os.W_OK):
        return None, "the drive is not writable"
    return rp, None


def tree_files(root):
    """{relative path: size} for a file or folder; None if it holds a symlink (exFAT cannot keep one)."""
    if os.path.isfile(root) and not os.path.islink(root):
        return {"": os.path.getsize(root)}
    out = {}
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            if os.path.islink(os.path.join(d, n)):
                return None
        for n in files:
            p = os.path.join(d, n)
            out[os.path.relpath(p, root)] = os.path.getsize(p)
    return out


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_verify(src, dst):
    """copy src to dst, then compare every file by size and sha256. Returns an error text or None."""
    want = tree_files(src)
    if want is None:
        return "holds a symlink; the drive cannot keep it"
    if os.path.isdir(src):
        shutil.copytree(src, dst, symlinks=False)
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    got = tree_files(dst)
    if got != want:
        return "copy differs from the original (file list or sizes)"
    for rel in want:
        a_, b_ = (os.path.join(src, rel), os.path.join(dst, rel)) if rel else (src, dst)
        if sha(a_) != sha(b_):
            return "checksum mismatch on " + (rel or os.path.basename(src))
    return None


def booted_udids():
    try:
        d = json.loads(run(["xcrun", "simctl", "list", "devices", "-j"])[1])["devices"]
        return {x["udid"] for lst in d.values() for x in lst if x.get("state") == "Booted"}
    except Exception:
        return set()


def execute(it, go, booted):
    a, t = it["action"], it["action"]["type"]
    out = []
    if t == "rm":
        rp = os.path.realpath(a["path"])
        contents = rp in CLEAR_CONTENTS_ONLY
        if not go:
            return ["DRY  remove %s%s" % (a["path"], "/*" if contents else "")]
        for tgt in ([os.path.join(rp, n) for n in os.listdir(rp)] if contents else [rp]):
            try:
                shutil.rmtree(tgt) if os.path.isdir(tgt) and not os.path.islink(tgt) else os.remove(tgt)
            except Exception as e:
                return ["FAIL %s (%s)" % (a["path"], e)]
        return ["OK   removed %s" % a["path"]]
    if t == "trash" and DEST:
        rp = os.path.realpath(a["path"])
        dst = os.path.join(DEST, "Mac-offload", it.get("folder") or "Other", os.path.basename(rp))
        if os.path.exists(dst):
            return ["FAIL %s (already exists on the drive: %s)" % (a["path"], dst)]
        if not go:
            return ["DRY  copy to %s, verify, then move the original to the Trash" % dst]
        try:
            err = copy_verify(rp, dst)
        except Exception as e:
            err = str(e)
        if err:
            shutil.rmtree(dst, ignore_errors=True) if os.path.isdir(dst) else (os.path.exists(dst) and os.remove(dst))
            return ["FAIL %s (%s). Original kept." % (a["path"], err)]
        trash = os.path.join(HOME, ".Trash", os.path.basename(rp))
        if os.path.exists(trash):
            trash += time.strftime(" %H.%M.%S")
        os.makedirs(os.path.dirname(trash), exist_ok=True)
        shutil.move(rp, trash)
        LOG.append({"undo": "copy back from the drive, or move back from Trash", "drive_copy": dst, "trash": trash, "from": rp})
        return ["OK   copied and verified on the drive: %s" % dst, "OK   original moved to Trash (empty it to free space)"]
    if t in ("trash", "trash_project"):
        rp = os.path.realpath(a["path"])
        dest = os.path.join(HOME, ".Trash", os.path.basename(rp))
        if os.path.exists(dest):
            dest += time.strftime(" %H.%M.%S")
        if not go:
            return ["DRY  move to Trash: %s" % a["path"]]
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(rp, dest)
        except Exception as e:
            return ["FAIL %s (%s)" % (a["path"], e)]
        LOG.append({"undo": "move back", "from": dest, "to": rp})
        return ["OK   moved to Trash: %s  (undo: move it back from ~/.Trash)" % a["path"]]
    if t == "cmd":
        if not go:
            return ["DRY  run: " + " ".join(a["argv"])]
        rc, msg = run(a["argv"], 900)
        return [("OK   " if rc == 0 else "FAIL ") + " ".join(a["argv"]) + ("" if rc == 0 else ": " + msg[-200:])]
    udids = [a["udid"]] if t == "simctl_device" else a.get("udids", [])
    done = skipped = 0
    for u in udids:
        if u in booted:
            skipped += 1
        elif go:
            done += run(["xcrun", "simctl", "delete", u])[0] == 0
        else:
            done += 1
    out.append("%s %d simulator devices%s" % ("OK  " if go else "DRY ", done, " (%d booted, skipped)" % skipped if skipped else ""))
    if t == "simctl_runtime":
        if skipped:
            out.append("SKIP runtime %s (a booted simulator uses it)" % a["runtime_id"])
        elif go:
            rc, msg = run(["xcrun", "simctl", "runtime", "delete", a["runtime_id"]])
            out.append(("OK   runtime is deleting in the background" if rc == 0 else "FAIL runtime: " + msg[-160:]))
        else:
            out.append("DRY  xcrun simctl runtime delete " + a["runtime_id"])
    return out


def token_for(scan, ns, dest=""):
    acts = [json.dumps(scan["items"][n].get("action"), sort_keys=True) for n in sorted(ns)]
    return hashlib.sha256(json.dumps([scan["generated"], sorted(ns), dest, acts]).encode()).hexdigest()[:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--token", default="")
    ap.add_argument("--to", default="", help="copy personal files to this mounted drive (/Volumes/NAME), verify, then Trash the original")
    ap.add_argument("--max-gb", type=float, default=50, help="refuse a plan larger than this (default 50)")
    ap.add_argument("--max-items", type=int, default=60)
    ap.add_argument("--max-projects", type=int, default=10, help="most whole projects to trash in one run")
    ap.add_argument("--max-scan-age-hours", type=float, default=24)
    a = ap.parse_args()

    scan = json.load(open(a.scan))
    raw = (open(a.plan).read() if os.path.isfile(a.plan) else a.plan).strip()
    if raw.startswith("DISK-CLEANUP-PLAN"):
        raw = raw[len("DISK-CLEANUP-PLAN"):].strip()
    plan = json.loads(raw)
    ns = plan["n"]
    items = scan["items"]
    if plan.get("scan") != scan["generated"]:
        sys.exit("REFUSED: the plan was made from a different scan. Run the scan and report again.")
    if (time.time() - scan["generated"]) / 3600 > a.max_scan_age_hours:
        sys.exit("REFUSED: the scan is older than %d hours. Scan again." % a.max_scan_age_hours)
    if not isinstance(ns, list) or len(set(ns)) != len(ns) or any((not isinstance(n, int)) or n < 0 or n >= len(items) for n in ns):
        sys.exit("REFUSED: the plan has bad item numbers.")
    if len(ns) > a.max_items:
        sys.exit("REFUSED: %d items is more than the limit of %d per run. Split the plan." % (len(ns), a.max_items))
    total = sum(items[n]["size"] for n in ns)
    if total > a.max_gb * 1e9:
        sys.exit("REFUSED: plan is %.0f GB; the limit is %.0f GB per run. Split it or pass --max-gb." % (total / 1e9, a.max_gb))
    nproj = sum(1 for n in ns if (items[n].get("action") or {}).get("type") == "trash_project")
    if nproj > a.max_projects:
        sys.exit("REFUSED: %d whole projects in one run; the limit is %d." % (nproj, a.max_projects))

    global DEST
    DEST, derr = check_dest(a.to)
    if derr:
        sys.exit("REFUSED: " + derr)
    if DEST:
        need = sum(items[n]["size"] for n in ns if (items[n].get("action") or {}).get("type") == "trash")
        if need * 1.05 > shutil.disk_usage(DEST).free:
            sys.exit("REFUSED: the drive has %.1f GB free; the plan needs %.1f GB." % (shutil.disk_usage(DEST).free / 1e9, need / 1e9))
    expect = token_for(scan, ns, DEST or "")
    if a.yes and a.token != expect:
        sys.exit("REFUSED: run the dry run first and pass its token (--token %s)." % expect)

    cwds, booted = live_cwds(), booted_udids()
    before = shutil.disk_usage("/System/Volumes/Data" if os.path.exists("/System/Volumes/Data") else "/").free
    print("MODE:", "EXECUTE" if a.yes else "DRY RUN (nothing is changed)")
    freed_est, skipped = 0, []
    for n in ns:
        it = items[n]
        ok, why = validate(it, scan, cwds)
        print("\n[%d] %s  (%.1f GB)" % (n, it["label"], it["size"] / 1e9))
        if not ok:
            print("    SKIP:", why)
            skipped.append((it["label"], why))
            LOG.append({"n": n, "label": it["label"], "status": "skipped", "why": why})
            continue
        lines = execute(it, a.yes, booted)
        for line in lines:
            print("   ", line[:300])
        if any(l.startswith("FAIL") for l in lines):
            skipped.append((it["label"], "failed"))
            LOG.append({"n": n, "label": it["label"], "status": "failed", "why": lines[0][:300]})
            continue
        freed_est += it["size"]
        LOG.append({"n": n, "label": it["label"], "path": (it["action"] or {}).get("path"), "size": it["size"], "status": "done" if a.yes else "dry"})
    print("\nWill free about %.1f GB; skipped %d item(s)." % (freed_est / 1e9, len(skipped)))
    if not a.yes:
        print("TOKEN %s   (re-run with --yes --token %s to execute exactly this plan)" % (expect, expect))
        return
    after = shutil.disk_usage("/System/Volumes/Data" if os.path.exists("/System/Volumes/Data") else "/").free
    os.makedirs(LOG_DIR, exist_ok=True)
    lf = os.path.join(LOG_DIR, time.strftime("%Y%m%d-%H%M%S") + ".json")
    json.dump({"when": time.time(), "free_before": before, "free_after": after, "entries": LOG}, open(lf, "w"), indent=1)
    print("Free before: %.1f GB   Free after: %.1f GB  (runtimes finish in the background; Trash items free space after you empty it)" % (before / 1e9, after / 1e9))
    print("Audit log:", lf)


if __name__ == "__main__":
    main()
