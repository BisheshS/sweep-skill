#!/usr/bin/env python3
"""Attack tests for the safety layers. Runs in a throwaway HOME. Touches nothing real.
run: python3 tests/test_guards.py
"""
import json, os, shutil, subprocess, sys, tempfile, time, unittest, warnings

warnings.simplefilter("ignore", ResourceWarning)  # the test fixtures leave small files unclosed; harmless

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
OLD = "2023-01-01T12:00:00"


def sh(cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, shell=isinstance(cmd, str))


class Guards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = os.path.realpath(tempfile.mkdtemp(prefix="dc-test-"))
        cls.env = dict(os.environ, HOME=cls.home, GIT_AUTHOR_DATE=OLD, GIT_COMMITTER_DATE=OLD,
                       GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        cls.proj = os.path.join(cls.home, "Projects")
        os.makedirs(cls.proj)
        cls.remote = os.path.join(cls.home, "remote.git")
        sh(["git", "init", "--bare", "-q", cls.remote], env=cls.env)
        for name in ("safe_old", "tracked_nm", "dirty", "unpushed", "noremote", "live", "keep"):
            cls.make_project(name)
        # variations
        open(os.path.join(cls.proj, "dirty", "wip.txt"), "w").write("unsaved work")
        sh("git commit --allow-empty -qm extra", cwd=os.path.join(cls.proj, "unpushed"), env=cls.env)
        open(os.path.join(cls.proj, "keep", ".disk-cleanup-keep"), "w").close()
        sh("git add -f node_modules && git commit -qm track && git push -q origin HEAD", cwd=os.path.join(cls.proj, "tracked_nm"), env=cls.env)
        cls.old_all()
        cls.live = subprocess.Popen(["sleep", "300"], cwd=os.path.join(cls.proj, "live"))
        cls.scan = os.path.join(cls.home, "scan.json")
        r = sh([sys.executable, "-I", os.path.join(SCRIPTS, "scan.py"), "--out", cls.scan, "--roots", cls.proj,
                "--min-artifact-mb", "0", "--min-project-mb", "0"], env=cls.env)
        assert r.returncode == 0, r.stderr

    @classmethod
    def tearDownClass(cls):
        cls.live.kill()
        shutil.rmtree(cls.home, ignore_errors=True)

    @classmethod
    def make_project(cls, name):
        d = os.path.join(cls.proj, name)
        os.makedirs(os.path.join(d, "node_modules", "pkg"))
        os.makedirs(os.path.join(d, "src"))
        open(os.path.join(d, "package.json"), "w").write("{}")
        open(os.path.join(d, "package-lock.json"), "w").write("{}")
        open(os.path.join(d, "src", "app.js"), "w").write("// my code")
        open(os.path.join(d, "node_modules", "pkg", "big.bin"), "wb").write(b"x" * 200000)
        open(os.path.join(d, ".gitignore"), "w").write("node_modules\n")
        sh("git init -q && git add -A && git commit -qm init", cwd=d, env=cls.env)
        if name != "noremote":
            sh(["git", "remote", "add", "origin", cls.remote], cwd=d, env=cls.env)
            sh("git push -q origin HEAD", cwd=d, env=cls.env)

    @classmethod
    def old_all(cls):
        t = time.mktime(time.strptime(OLD, "%Y-%m-%dT%H:%M:%S"))
        for dp, dns, fns in os.walk(cls.proj):
            for n in fns + dns:
                try:
                    os.utime(os.path.join(dp, n), (t, t), follow_symlinks=False)
                except OSError:
                    pass

    # ---- helpers ----
    def items(self):
        return json.load(open(self.scan))

    def forge(self, action, size=1000, label="forged"):
        d = self.items()
        d["items"].append({"id": "forged", "group": "x", "label": label, "path": action.get("path"), "size": size, "risk": "safe",
                           "rebuild": "", "note": "", "action": action})
        f = os.path.join(self.home, "forged.json")
        json.dump(d, open(f, "w"))
        return f, len(d["items"]) - 1, d

    def apply(self, scanfile, d, ns, extra=None, execute=False, token=None):
        plan = json.dumps({"v": 1, "scan": d["generated"], "n": ns})
        base = [sys.executable, "-I", os.path.join(SCRIPTS, "apply.py"), "--scan", scanfile, "--plan", plan] + (extra or [])
        if execute:
            if token is None:
                dry = sh(base, env=self.env)
                token = [w for w in dry.stdout.split() if len(w) == 8 and w.isalnum() and "TOKEN" in dry.stdout][0] if "TOKEN" in dry.stdout else "x"
                token = dry.stdout.split("TOKEN ")[1].split()[0] if "TOKEN " in dry.stdout else "x"
            base += ["--yes", "--token", token]
        return sh(base, env=self.env)

    def item_n(self, d, suffix):
        for n, it in enumerate(d["items"]):
            if it["path"] and it["path"].endswith(suffix):
                return n
        self.fail("no item for " + suffix)

    # ---- tests ----
    def test_safe_artifact_removed_and_source_kept(self):
        d = self.items()
        n = self.item_n(d, "safe_old/node_modules")
        r = self.apply(self.scan, d, [n], execute=True)
        self.assertIn("OK   removed", r.stdout, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.proj, "safe_old", "node_modules")))
        self.assertTrue(os.path.exists(os.path.join(self.proj, "safe_old", "src", "app.js")))

    def test_dry_run_changes_nothing(self):
        d = self.items()
        n = self.item_n(d, "dirty/node_modules")
        r = self.apply(self.scan, d, [n])
        self.assertIn("DRY", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.proj, "dirty", "node_modules")))

    def test_execute_needs_the_dry_run_token(self):
        d = self.items()
        n = self.item_n(d, "dirty/node_modules")
        r = self.apply(self.scan, d, [n], execute=True, token="deadbeef")
        self.assertIn("REFUSED", r.stdout + r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.proj, "dirty", "node_modules")))

    def test_tampered_paths_are_refused(self):
        for bad in (self.home, os.path.join(self.home, "Projects"), "/etc", "/", os.path.join(self.proj, "safe_old", "src"),
                    os.path.join(self.proj, "dirty", ".git"), os.path.join(self.proj, "dirty")):
            f, n, d = self.forge({"type": "rm", "path": bad})
            r = self.apply(f, d, [n], execute=True)
            self.assertIn("SKIP", r.stdout, "should refuse %s\n%s%s" % (bad, r.stdout, r.stderr))
            self.assertTrue(os.path.exists(bad) or bad == "/", bad)
        self.assertTrue(os.path.exists(os.path.join(self.proj, "dirty", "wip.txt")))

    def test_dotdot_and_symlink_escape_refused(self):
        os.symlink("/etc", os.path.join(self.proj, "dirty", "link"))
        for bad in (os.path.join(self.proj, "safe_old", "node_modules", "..", "src"), os.path.join(self.proj, "dirty", "link")):
            f, n, d = self.forge({"type": "rm", "path": bad})
            r = self.apply(f, d, [n], execute=True)
            self.assertIn("SKIP", r.stdout, bad)
        self.assertTrue(os.path.exists(os.path.join(self.proj, "safe_old", "src", "app.js")))

    def test_git_tracked_folder_is_never_removed(self):
        d = self.items()
        f, n, d = self.forge({"type": "rm", "path": os.path.join(self.proj, "tracked_nm", "node_modules")})
        r = self.apply(f, d, [n], execute=True)
        self.assertIn("SKIP", r.stdout)
        self.assertIn("tracks files", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.proj, "tracked_nm", "node_modules", "pkg", "big.bin")))

    def test_running_process_blocks_deletion(self):
        f, n, d = self.forge({"type": "rm", "path": os.path.join(self.proj, "live", "node_modules")})
        r = self.apply(f, d, [n], execute=True)
        self.assertIn("running process", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.proj, "live", "node_modules")))

    def test_keep_marker_blocks_everything(self):
        f, n, d = self.forge({"type": "rm", "path": os.path.join(self.proj, "keep", "node_modules")})
        r = self.apply(f, d, [n], execute=True)
        self.assertIn("protected by you", r.stdout)
        f, n, d = self.forge({"type": "trash_project", "path": os.path.join(self.proj, "keep")})
        r = self.apply(f, d, [n], execute=True)
        self.assertIn("SKIP", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.proj, "keep", "src", "app.js")))

    def test_whole_project_trash_only_when_clean_pushed_old(self):
        for name, word in (("dirty", "uncommitted"), ("unpushed", "remote"), ("noremote", "remote")):
            f, n, d = self.forge({"type": "trash_project", "path": os.path.join(self.proj, name)})
            r = self.apply(f, d, [n], execute=True)
            self.assertIn("SKIP", r.stdout, name + r.stdout)
            self.assertTrue(os.path.exists(os.path.join(self.proj, name, "src", "app.js")), name)
        f, n, d = self.forge({"type": "trash_project", "path": os.path.join(self.proj, "live")})
        self.assertIn("SKIP", self.apply(f, d, [n], execute=True).stdout)

    def test_zz_clean_old_pushed_project_can_be_trashed(self):
        d = self.items()
        n = self.item_n(d, "Projects/safe_old")
        self.assertEqual(d["items"][n]["action"]["type"], "trash_project")
        r = self.apply(self.scan, d, [n], execute=True)
        self.assertIn("moved to Trash", r.stdout, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.proj, "safe_old")))
        self.assertTrue(any(os.path.exists(os.path.join(self.home, ".Trash", x, "src", "app.js")) for x in os.listdir(os.path.join(self.home, ".Trash"))))

    def test_caps_and_stale_scan_and_bad_plan(self):
        d = self.items()
        n = self.item_n(d, "dirty/node_modules")
        r = self.apply(self.scan, d, [n], extra=["--max-gb", "0.0000001"])
        self.assertIn("REFUSED", r.stdout + r.stderr)
        r = self.apply(self.scan, d, [n], extra=["--max-items", "0"])
        self.assertIn("REFUSED", r.stdout + r.stderr)
        r = self.apply(self.scan, d, [9999])
        self.assertIn("REFUSED", r.stdout + r.stderr)
        r = self.apply(self.scan, d, [n, n])
        self.assertIn("REFUSED", r.stdout + r.stderr)
        d2 = dict(d, generated=d["generated"] - 10 * 86400)
        f = os.path.join(self.home, "old.json")
        json.dump(d2, open(f, "w"))
        r = self.apply(f, d2, [n])
        self.assertIn("REFUSED", r.stdout + r.stderr)
        plan_other = json.dumps({"v": 1, "scan": 123, "n": [n]})
        r = sh([sys.executable, "-I", os.path.join(SCRIPTS, "apply.py"), "--scan", self.scan, "--plan", plan_other], env=self.env)
        self.assertIn("different scan", r.stdout + r.stderr)

    def test_command_allowlist(self):
        f, n, d = self.forge({"type": "cmd", "argv": ["rm", "-rf", self.home]})
        r = self.apply(f, d, [n], execute=True)
        self.assertIn("SKIP", r.stdout)
        self.assertTrue(os.path.exists(self.proj))

    def test_change_after_scan_is_detected(self):
        d = self.items()
        n = self.item_n(d, "noremote/node_modules")
        time.sleep(1.1)
        open(os.path.join(self.proj, "noremote", "src", "app.js"), "a").write("// edited after scan")
        r = self.apply(self.scan, d, [n], execute=True)
        self.assertIn("changed after the scan", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.proj, "noremote", "node_modules")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
