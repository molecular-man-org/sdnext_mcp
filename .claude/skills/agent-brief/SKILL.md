---
name: agent-brief
description: Write the brief for a subagent that implements an issue in this repository. Use before every Agent call that will change code, so each brief carries the same rules (worktree, checks, commit lines, draft pull request, report) and only the job is written by hand.
---

# Agent brief

The agent knows only the brief. Copy the template, fill the `<...>` parts,
and delete what does not apply. Write the job by hand; keep the rest as it
is. From realmsculpt#481: seven hand-written briefs in one afternoon each
forgot a different rule.

```markdown
**Role.** You implement issue #<N> in <owner/repo> (`gh issue view <N>`).
Read `CLAUDE.md` (and `AGENTS.md` if there is one) first and follow them.
<The owner's word for this work, with the date, if it needed his word.>

## Setup

Work in a new worktree on a branch named after the issue:
`git fetch origin && git worktree add -b <N>-<short-name> <your scratch directory>/<N> origin/main`.
<Install step, for example `bun install --frozen-lockfile`.> Never edit the
main checkout; read other repositories only, at a named commit. Keep scratch
files in your scratch directory, never in the repository. No git hook runs
the checks for you: run `<the check command from standard.json>` before
every push.

## The job

<Numbered steps. What is decided already and must not be reopened. What to
stop and report on instead of deciding.>

## Boundaries

You may change: <paths>. Never: dependencies or lockfiles, `.claude/`,
`.github/` <unless listed>, secrets, repository settings, how the app
deploys, the version, the UI builder's files, live data. Never raise a count
in `standard-baseline.json` or a coverage floor to make a check pass: report
it instead.

## Delivery

- Small commits; push at least every 30 minutes.
- Subject 10 to 120 characters, ending `(Refs #<N>)`. Never a closing word
  (close, fix, resolve and their forms) next to an issue number.
- Every commit ends, after a blank line, with
  `Co-Authored-By: <your tool and model> <the vendor's noreply address>`.
- Open a draft pull request to `main` with the repository's template
  headings, title ending `(Refs #<N>)`. Do not mark it ready; do not merge.
- If `main` moves, merge it into the branch. Never rebase or force-push.
- Watch CI with `python3 scripts/pr_flow.py ci <PR>`; fix at most three
  rounds, then report.

## Report

The pull request link and head commit, files changed, the checks and CI
results with their times (US Eastern), what you stopped on or were unsure
of, and faults you saw but did not fix (file and line).
```
