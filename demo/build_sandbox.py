#!/usr/bin/env python3
"""Build a fake home folder for the demo. Real files, made-up names. Refuses to touch your real home.

usage: build_sandbox.py /tmp/some-empty-dir
"""
import os, subprocess, sys, time

target = os.path.realpath(sys.argv[1])
real_home = os.path.realpath(os.path.expanduser("~"))
if target == real_home or real_home.startswith(target + os.sep) or os.path.exists(target) and os.listdir(target):
    sys.exit("refused: pass a new, empty folder that is not your home folder")
os.makedirs(target)
OLD = time.time() - 500 * 86400


def blob(path, mb, old=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    chunk = (b"demo-data-" * 100000)[:1024 * 1024]
    with open(path, "wb") as f:
        for _ in range(mb):
            f.write(chunk)
    if old:
        os.utime(path, (OLD, OLD))


def git(cwd, *a, date=None):
    env = dict(os.environ, HOME=target, GIT_AUTHOR_NAME="Demo", GIT_AUTHOR_EMAIL="demo@example.com",
               GIT_COMMITTER_NAME="Demo", GIT_COMMITTER_EMAIL="demo@example.com")
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", *a], cwd=cwd, env=env, check=True, capture_output=True)


H = lambda *a: os.path.join(target, *a)

# caches that rebuild
blob(H("Library/Caches/Google/Chrome/Default/Cache/data_0"), 360)
blob(H("Library/Caches/com.example.editor.ShipIt/update.zip"), 330)
blob(H("Library/Caches/pip/wheels/big.whl"), 90)
blob(H("Library/Developer/Xcode/iOS DeviceSupport/iPhone14,5 18.0.1/Symbols/sym"), 120)

# an old project, fully pushed: a heavy node_modules and an idle repo
for name, pushed in (("old-shop", True), ("side-hustle", False)):
    p = H("Projects", name)
    os.makedirs(p)
    open(os.path.join(p, "package.json"), "w").write('{"name":"%s"}\n' % name)
    open(os.path.join(p, "package-lock.json"), "w").write("{}\n")
    open(os.path.join(p, "index.js"), "w").write("console.log('hi')\n")
    open(os.path.join(p, ".gitignore"), "w").write("node_modules\n")
    git(p, "init", "-q", "-b", "main")
    git(p, "add", "-A")
    git(p, "commit", "-q", "-m", "init", date="2025-05-01T12:00:00")
    if pushed:
        bare = H("remotes", name + ".git")
        os.makedirs(bare)
        git(bare, "init", "-q", "--bare")
        git(p, "remote", "add", "origin", bare)
        git(p, "push", "-q", "-u", "origin", "main")
    blob(os.path.join(p, "node_modules", "big-dep", "index.js"), 180 if pushed else 140)
    for f in ("package.json", "package-lock.json", "index.js", ".gitignore"):
        os.utime(os.path.join(p, f), (OLD, OLD))
    for d, _, fs in os.walk(os.path.join(p, "node_modules")):
        for f in fs:
            os.utime(os.path.join(d, f), (OLD, OLD))

# a big download
blob(H("Downloads", "holiday-video.mp4"), 420)
blob(H("Downloads", "installer-old.dmg"), 310)
print("sandbox ready:", target)
