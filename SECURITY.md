# Security and safety

This tool can delete files. Treat every report seriously.

**Report a bug that deletes or moves the wrong thing:** open a GitHub issue titled "SAFETY: ..." and give the dry-run output. Remove your file names first if they are private.

**Report a way to bypass a guard** (forged plan, path tricks, symlinks): use GitHub's private "Report a vulnerability" button on the Security tab, not a public issue.

Design rules: scan and summary never delete; the plan holds item numbers, not paths; `apply.py` re-checks every target on disk; a dry run prints a token that covers the exact paths; deletes go to the Trash when the item is personal. See SKILL.md, "Safety design".

Status: beta. Tested on one Mac (macOS 26). Always read the dry run. No warranty.
