---
name: preflight
description: Run this repository's checks and review the pending diff before committing or pushing. Use when the user asks "is this ready to push", "run the checks", or before opening a pull request.
---

# Preflight

1. Run `scripts/check.sh`. It runs the same checks as CI. If a tool is
   missing locally the script says so and skips it; report skipped checks
   rather than treating them as passed.
2. Read `git diff` (and `git diff --staged`) and look for:
   - secrets, tokens, or personal paths;
   - debugging leftovers and commented-out code;
   - changes unrelated to the task.
3. Fix what you find, then re-run the checks.
4. Report: which checks passed, which were skipped, and anything you could not
   fix. Do not commit or push unless the user asked for it.
