---
name: disk-cleanup
description: Free disk space on a Mac with insights, not just a file list. Finds idle projects, rebuildable node_modules and build folders, Xcode simulator runtimes, caches and big downloads, and separates "safe to remove" from "your only copy". Shows a chart dashboard in the chat and an optional visual HTML report. Deletes only after a dry run and a token, with hard safety checks. Use when the user says "free up space", "disk cleanup", "my Mac is full", "storage almost full", "what can I delete", or "/disk-cleanup". macOS only.
---

# disk-cleanup

Insights first, deletion last. Scan, show a dashboard, let the user pick, dry run, delete.

Also finds what plain cleaners miss: leaked temp folders (a test tool that never cleans up), apps not opened for a year, and a map of what to delete, what to move to an external drive, and what to keep.

Files (in this skill folder, `$SKILL`): `scripts/scan.py` (read only), `scripts/summary.py` (chat dashboard and plan maker), `scripts/build_report.py` (HTML), `scripts/apply.py` (the only script that deletes), `scripts/common.py` (all safety checks), `tests/test_guards.py`.

## Workflow

1. **Scan** (read only, 15 to 60 s). Use a work folder such as the scratchpad as `$WORK`:
   `python3 -I $SKILL/scripts/scan.py --out $WORK/scan.json`
   Options: `--roots <dirs>` where projects live, `--stale-days 180`, `--old-days 365`.
2. **Show the dashboard in the chat.** Run `python3 -I $SKILL/scripts/summary.py $WORK/scan.json`. Bash output is not reliably shown to the user, so **paste the full output into your reply inside a code block.** Then add 3 or 4 plain lines on what matters most (the biggest safe win, which old projects are on GitHub, which work exists only on this Mac).
   Old-project detail: `summary.py $WORK/scan.json --old`.
   **Map view** (what to delete, move to a drive, back up, keep): `summary.py $WORK/scan.json --map`. Paste it in a code block too.
   Insight-only lines (leaked temp folders, unused apps) have no item number. Show them, and give the user the command; do not delete them.
3. **Ask what to do.** Offer: "all safe", item numbers (`do 0, 3, 7`), or the visual report.
   Visual report (optional): `python3 -I $SKILL/scripts/build_report.py --scan $WORK/scan.json --out $WORK/report.html --open`. The user ticks items and presses "Copy plan". They paste a `DISK-CLEANUP-PLAN {...}` line.
4. **Make the plan** when the user gives numbers or "all safe":
   `summary.py $WORK/scan.json --plan safe` or `--plan 0,3,7`. Never add items the user did not choose.
5. **Dry run.** `python3 -I $SKILL/scripts/apply.py --scan $WORK/scan.json --plan '<plan line>'`
   Paste the result in a code block. Say what will go and what was skipped, and why. The dry run prints a `TOKEN`.
6. **Wait for a clear yes.** Then run the same command with `--yes --token <TOKEN>`. `apply.py` refuses to run without the token from the dry run of exactly this plan.
   **Move to a pendrive or hard disk** (personal files only): add `--to /Volumes/NAME` to both the dry run and the execute run. Each item is copied, checked by size and SHA-256, and only then is the original moved to the Trash. Items with symlinks are refused (exFAT cannot keep them). Check the drive format first with `diskutil info /Volumes/NAME`: FAT32 cannot hold files over 4 GB, and Photos libraries need APFS. Offer to reformat only if the drive is empty, and let the user run it.
7. **Report** free space before and after, what was skipped, and what is still pending (simulator runtimes delete in the background; Trash items free space after the Trash is emptied). The audit log path is in the output.

## Item tags

- **SAFE**: a tool rebuilds it (caches, DerivedData, `node_modules` or venv in an idle project with a lockfile).
- **CHECK**: probably fine, depends on current work (build folders, active projects, a runtime with a booted simulator, Trash).
- **SAFE items still cost something**: say what returns (re-download, slower first build). Never call a cache "free".
- **ASK**: the user's call (big downloads, simulator runtimes and devices, whole old projects, Xcode Archives, device backups).
- **KEEP**: the only copy of the user's work. Never offered for deletion. Shown so the user can back it up.

## Safety design (why this will not go rogue)

1. **Scan and summary never delete.** Only `apply.py --yes` deletes.
2. **The plan holds item numbers, not paths.** The scan file is not trusted: `apply.py` re-derives from the disk that each target is an allowed kind of thing. Allowed: exact known cache folders, direct children of `~/Library/Caches`, artifact folders (`node_modules`, `Pods`, `.venv`, `.next`, `target`, `build`) with a matching manifest beside them, direct children of `~/Downloads`, whole projects that pass the strict test below, three fixed commands, and simulator IDs. Everything else is refused.
3. **Source code is never an artifact.** If git tracks any file inside a folder, it is refused.
4. **Whole old projects** are moved to the Trash only if: it is its own git repo, no uncommitted or untracked files, no stash, every local commit is on a remote (as of the last fetch), a remote exists, idle for the old-days limit, not named in the shell config (PATH or aliases, so SDKs like Flutter stay), and no process runs in it. Facts are re-checked at delete time.
5. **Live work is protected.** A project with a running process (dev server, agent session, terminal) in it is skipped. A project changed after the scan is skipped.
6. **User controls.** Put an empty file named `.disk-cleanup-keep` in any folder, or list paths in `~/.disk-cleanup/protect.txt`, and the skill never touches them.
7. **Hard caps per run:** 60 items, 50 GB, 10 whole projects (raise with `--max-items`, `--max-gb`, `--max-projects`). A scan older than 24 hours is refused.
8. **Dry run plus token.** Execute needs the token printed by the dry run of the same plan.
9. **Recoverable first.** Personal files and whole projects go to the Trash, not `rm`. Every run writes an audit log to `~/.disk-cleanup/logs/`.
10. **Protected paths** (`~`, `~/Library`, `~/Documents`, `~/.ssh`, and more), symlinks, and anything outside the home folder are refused.
11. Booted simulators are never touched. Xcode DerivedData is skipped while Xcode runs. The Chrome cache is skipped while Chrome runs.
12. The token covers the exact paths and commands, so editing `scan.json` after the dry run voids it.
13. Items that hold user state are CHECK, never SAFE: JetBrains caches (local history), `Library/Logs`, iOS DeviceSupport (a slow resync if the user tests on a cable-connected phone). Chrome logins live in `Application Support`, not in the cache folder; never offer `Application Support` as a whole.
14. Copy to a drive is verified by SHA-256 before the original moves. A failed copy keeps the original and removes the partial copy.
15. macOS protects `~/Library/Containers` and the Trash from scripts. If a move is refused, tell the user how to do it in Finder (Cmd+Shift+G). Do not work around it.

If your permission system blocks a script that deletes, run the explicit commands from the dry run, one path per command. Do not weaken a guard to get past a refusal. Tell the user why it was refused.

## Testing

`python3 tests/test_guards.py` runs 13 attack tests (the drive copy is tested separately on throwaway files) in a throwaway HOME (forged paths, tracked source, dirty and unpushed repos, running processes, stale scans, symlinks, caps, wrong token). Run it after any change to `common.py` or `apply.py`.

## Hard-won lessons

- Use real disk use (`du -sk`), not `find -size` or `ls -l`. Sparse files look huge and are not.
- A disk that fills by GBs per day is usually a leak: check `/private/var/folders/*/*/T` for hundreds of same-named folders, then Xcode build output, then simulator data.
- Verify every "safe" claim on disk before the user deletes: look inside the folder for logins, tokens, history.
- If the user says a folder is important or recent, put a `.disk-cleanup-keep` file in it and rescan.
- Ask a second reviewer (another model) to check a delete plan if the user is nervous. It found two items this skill had marked SAFE wrongly.

## Do not

- Do not upload the report, `scan.json` or the dashboard anywhere. They list the user's file names.
- Do not delete `~/.claude`, `~/.ssh`, keychains, or any `.git` folder.
- Do not empty the Trash for the user.
