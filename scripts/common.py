"""Shared helpers for scan.py and apply.py. Python 3.9+, standard library only.
Every safety check lives here so that scan and apply cannot disagree.
"""
import os, re, subprocess, time

HOME = os.path.realpath(os.path.expanduser("~"))
DAY = 86400
ARTIFACTS = {"node_modules", "Pods", ".venv", "venv", ".next", "build", "target"}
PROJECT_MARKERS = {".git", "package.json", "Podfile", "Package.swift", "pyproject.toml", "requirements.txt",
                   "Cargo.toml", "go.mod", "build.gradle", "build.gradle.kts", "pubspec.yaml", "Gemfile",
                   "composer.json", "pom.xml", "ProjectSettings"}
BUILD_MANIFESTS = PROJECT_MARKERS - {".git"} | {"Makefile", "CMakeLists.txt"}
KEEP_MARKER = ".disk-cleanup-keep"          # put this empty file in a folder: the skill never touches it
PROTECT_FILE = os.path.join(HOME, ".disk-cleanup", "protect.txt")  # one protected path per line
UDID_RE = re.compile(r"^[0-9A-Fa-f]{8}-([0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}$")


def run(argv, timeout=60):
    """returns (returncode, output). Never raises."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


def is_project_dir(path):
    try:
        names = set(os.listdir(path))
    except OSError:
        return False
    return bool(names & PROJECT_MARKERS) or any(n.endswith((".xcodeproj", ".xcworkspace", ".uproject")) for n in names)


def git_info(proj):
    """git state of a project that is ITSELF a git repo (a parent repo does not count)."""
    rc, top = run(["git", "-C", proj, "rev-parse", "--show-toplevel"], 15)
    if rc != 0 or os.path.realpath(top.strip()) != os.path.realpath(proj):
        return {"is_repo": False}
    info = {"is_repo": True}
    info["dirty_count"] = len(run(["git", "-C", proj, "status", "--porcelain"], 30)[1].splitlines())
    info["dirty"] = info["dirty_count"] > 0
    info["has_remote"] = bool(run(["git", "-C", proj, "remote"], 15)[1].strip())
    info["remote_url"] = run(["git", "-C", proj, "remote", "get-url", "origin"], 15)[1].strip() if info["has_remote"] else ""
    rc, out = run(["git", "-C", proj, "rev-list", "--branches", "--not", "--remotes", "--count"], 30)
    info["unpushed"] = int(out) if rc == 0 and out.isdigit() else -1  # -1 = unknown
    info["stash"] = bool(run(["git", "-C", proj, "stash", "list"], 15)[1].strip())
    rc, out = run(["git", "-C", proj, "log", "-1", "--format=%ct"], 15)
    info["last_commit"] = int(out) if rc == 0 and out.isdigit() else 0
    return info


def recent_mtime(proj, depth=3):
    """newest file mtime in the project, ignoring build artifacts and .git."""
    best = 0
    base = proj.rstrip("/").count("/")
    for dp, dns, fns in os.walk(proj):
        dns[:] = [d for d in dns if d not in ARTIFACTS and d != ".git" and dp.count("/") - base < depth]
        for f in fns:
            try:
                best = max(best, os.lstat(os.path.join(dp, f)).st_mtime)
            except OSError:
                pass
    return best


def activity(proj, git=None):
    """epoch of the last real activity: newest of last commit and newest source file."""
    g = git if git is not None else git_info(proj)
    return max(g.get("last_commit", 0) if g.get("is_repo") else 0, recent_mtime(proj))


def live_cwds():
    """working directories of all running processes (a dev server or an agent session inside a project)."""
    rc, out = run(["lsof", "-d", "cwd", "-Fn"], 30)
    paths = set()
    for line in out.splitlines():
        if line.startswith("n/"):
            paths.add(os.path.realpath(line[1:]))
    return paths


def is_live(path, cwds):
    p = os.path.realpath(path)
    return any(c == p or c.startswith(p + os.sep) for c in cwds)


def protected_by_user(path):
    """True if the path (or a parent inside HOME) has the keep marker, or is listed in protect.txt."""
    p = os.path.realpath(path)
    listed = []
    try:
        listed = [os.path.realpath(os.path.expanduser(l.strip())) for l in open(PROTECT_FILE) if l.strip() and not l.startswith("#")]
    except OSError:
        pass
    cur = p
    while cur.startswith(HOME + os.sep):
        if os.path.exists(os.path.join(cur, KEEP_MARKER)) or cur in listed:
            return True
        cur = os.path.dirname(cur)
    return False


def tracked_files_inside(path):
    """True if git tracks any file inside path (then it is SOURCE, never delete)."""
    parent = os.path.dirname(path)
    rc, top = run(["git", "-C", parent, "rev-parse", "--show-toplevel"], 15)
    if rc != 0:
        return False
    rc, out = run(["git", "-C", parent, "ls-files", "--", path], 30)
    return bool(out.strip())


def artifact_kind_ok(path):
    """Is this really a regenerable artifact folder? Same rule used by scan and apply."""
    kind, proj = os.path.basename(path), os.path.dirname(path)
    try:
        sib = set(os.listdir(proj))
    except OSError:
        return False
    if kind not in ARTIFACTS or not os.path.isdir(path):
        return False
    if kind == "node_modules":
        return "package.json" in sib
    if kind == "Pods":
        return "Podfile" in sib
    if kind == "target":
        return "Cargo.toml" in sib
    if kind in (".venv", "venv"):
        return os.path.exists(os.path.join(path, "pyvenv.cfg"))
    if kind == ".next":
        return "package.json" in sib
    if kind == "build":
        return bool(sib & BUILD_MANIFESTS) or any(n.endswith((".xcodeproj", ".xcworkspace")) for n in sib)
    return False


SHELL_CONFIGS = [".zshrc", ".zprofile", ".zshenv", ".bashrc", ".bash_profile", ".profile"]


def referenced_in_shell_config(path):
    """A folder named in your PATH or aliases is probably a tool or SDK you use (Flutter, Android, ...)."""
    names = {path, path.replace(HOME, "~"), path.replace(HOME, "$HOME")}
    for f in SHELL_CONFIGS:
        try:
            text = open(os.path.join(HOME, f), errors="ignore").read()
        except OSError:
            continue
        if any(n in text for n in names):
            return True
    return False


def trash_project_ok(path, old_days, cwds):
    """All conditions for moving a WHOLE project to the Trash. Returns (ok, reason)."""
    if not is_project_dir(path):
        return False, "not a project folder"
    if protected_by_user(path):
        return False, "protected by you"
    g = git_info(path)
    if not g["is_repo"]:
        return False, "not a git repo: this folder may be the only copy"
    if g["dirty"]:
        return False, "uncommitted changes (%d files)" % g["dirty_count"]
    if g["unpushed"] != 0:
        return False, "commits not on any remote (unpushed=%s)" % g["unpushed"]
    if not g["has_remote"]:
        return False, "no git remote"
    if g["stash"]:
        return False, "has stashed changes"
    if is_live(path, cwds):
        return False, "a running process uses this folder"
    if referenced_in_shell_config(path):
        return False, "named in your shell config (probably a tool or SDK)"
    age = (time.time() - activity(path, g)) / DAY
    if age < old_days:
        return False, "active %d days ago" % age
    return True, "ok"
