#!/usr/bin/env python3
"""Fix the common workflow findings in place, line by line, so a file keeps its layout and comments.

    python3 scripts/fix_workflows.py                 show what it would change in .github/workflows/
    python3 scripts/fix_workflows.py --write         change the files
    python3 scripts/fix_workflows.py --repo PATH     another checkout

It fixes six rules of the standard and nothing else:
- CI-5: every action pinned to a full commit SHA, with the tag it came from in a comment (`@<sha> # v4`).
  The SHA is read with `git ls-remote` from github.com; an action that cannot be resolved is left as it is.
- CI-6: a job that names a runner and sets no `timeout-minutes` gets `timeout-minutes: 30` under `runs-on`.
- CI-4: a workflow without `permissions` gets `permissions: contents: read` before `jobs:`.
- CI-3: a workflow that runs on every push gets `branches: [main]` under `push` (other commits are covered by
  `pull_request`).
- CI-7: a job on one of GitHub's own machines (`ubuntu-latest`, `macos-14`, ...) runs on the first runner the
  record's `ci_runners` names instead. Only a plain label is changed; a matrix or an expression is left alone.
  `--keep-runners` leaves runners alone: a PUBLIC repository must never run on self-hosted runners, because anyone's
  pull request would run code on them (and GitHub's machines cost nothing there).
- CI-8: a workflow that runs on pull requests and has no `concurrency` gets one that cancels a pull request's
  older run (never a run on main).

Read the change before committing it: a workflow that writes (releases, comments, pushes) needs more than
`contents: read`, and its CI says so on the first run. Owned by the template: change it there, not in a project.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import standard_check as sc  # noqa: E402

USES = re.compile(r"""^(?P<head>\s*-?\s*uses\s*:\s*)(?P<q>['"]?)(?P<action>[\w.-]+/[\w.-]+(?:/[^@\s'"]*)?)@(?P<ref>[^\s'"#]+)(?P=q)(?P<tail>.*)$""")
SHA = re.compile(r"\A[0-9a-f]{40}\Z")
TIMEOUT = 30
CONCURRENCY = (
    "concurrency:\n"
    "  group: ${{ github.workflow }}-${{ github.ref }}\n"
    "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n"
)
_resolved: Dict[Tuple[str, str], Optional[str]] = {}


def resolve(action: str, ref: str) -> Optional[str]:
    """The commit a tag or branch of an action points to, or None. Replaced in the tests."""
    repo = "/".join(action.split("/")[:2])
    key = (repo, ref)
    if key not in _resolved:
        try:
            out = subprocess.run(
                ["git", "ls-remote", f"https://github.com/{repo}.git", ref, f"{ref}^{{}}"],
                capture_output=True, text=True, timeout=30,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        refs = dict((name, sha) for sha, name in (line.split("\t", 1) for line in out.splitlines() if "\t" in line))
        _resolved[key] = (refs.get(f"refs/tags/{ref}^{{}}") or refs.get(f"refs/tags/{ref}")
                          or refs.get(f"refs/heads/{ref}"))
    return _resolved[key]


def pin(lines: List[str], notes: List[str]) -> None:
    for index, line in enumerate(lines):
        match = USES.match(line)
        if not match or SHA.match(match.group("ref")) or line.lstrip().startswith("#"):
            continue
        action, ref = match.group("action"), match.group("ref")
        sha = resolve(action, ref)
        if not sha:
            notes.append(f"CI-5: could not resolve {action}@{ref}; left as it is")
            continue
        tail = match.group("tail")
        # Two spaces before a comment: yamllint's default asks for it, and one space fails a project that uses it.
        comment = f"  {tail.strip()}" if tail.strip().startswith("#") else f"  # {ref}"
        lines[index] = f"{match.group('head')}{action}@{sha}{comment}"
        notes.append(f"CI-5: {action}@{ref} -> {sha[:12]}")


def add_timeouts(lines: List[str], notes: List[str]) -> None:
    index = 0
    while index < len(lines):
        line = sc.without_comment(lines[index])
        if not re.match(r"^\s*runs-on\s*:", line):
            index += 1
            continue
        indent = len(line) - len(line.lstrip())
        start = index
        while start > 0 and (not lines[start - 1].strip() or len(lines[start - 1]) - len(lines[start - 1].lstrip()) >= indent):
            start -= 1
        end = index + 1
        while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) >= indent):
            end += 1
        job = lines[start:end]
        if any(re.match(r"^\s{%d}timeout-minutes\s*:" % indent, sc.without_comment(other)) for other in job):
            index = end
            continue
        after = index + 1  # past a runs-on value written as a block below it
        while after < len(lines) and lines[after].strip() and len(lines[after]) - len(lines[after].lstrip()) > indent:
            after += 1
        lines.insert(after, " " * indent + f"timeout-minutes: {TIMEOUT}")
        notes.append(f"CI-6: timeout-minutes: {TIMEOUT} added at line {after + 1}")
        index = end + 1


def before_jobs(lines: List[str], block: str) -> bool:
    for index, line in enumerate(lines):
        if re.match(r"^jobs\s*:", line):
            lines[index:index] = block.rstrip("\n").split("\n") + [""]
            return True
    return False


def push_to_main(lines: List[str], notes: List[str]) -> None:
    """CI-3: limit a `push` trigger that has no branches or tags to main."""
    for index, line in enumerate(lines):
        found = re.match(r"""^(?:on|"on"|'on')\s*:\s*(.*?)\s*$""", sc.without_comment(line))
        if not found:
            continue
        inline = found.group(1)
        if inline:  # `on: push` or `on: [push, pull_request]`: written out as a block
            events = [e.strip().strip("'\"") for e in inline.strip("[]").split(",") if e.strip()]
            block = ["on:"]
            for event in events:
                block += ["  push:", "    branches: [main]"] if event == "push" else [f"  {event}:"]
            lines[index:index + 1] = block
            notes.append("CI-3: push runs for main only")
            return
        cursor = index + 1
        while cursor < len(lines) and (not lines[cursor].strip() or lines[cursor].startswith((" ", "\t"))):
            push = re.match(r"^(\s+)push\s*:\s*(\S*)", sc.without_comment(lines[cursor]))
            if push:
                indent = push.group(1)
                if push.group(2) not in ("", "{}", "null", "~"):
                    return
                lines[cursor] = f"{indent}push:"
                lines.insert(cursor + 1, f"{indent}  branches: [main]")
                notes.append("CI-3: push runs for main only")
                return
            cursor += 1
        return


HOSTED = re.compile(r"""^(?P<head>\s*runs-on\s*:\s*)(?P<q>['"]?)(?:ubuntu|macos|windows)-[\w.-]+(?P=q)\s*(?P<tail>#.*)?$""")


def own_runners(lines: List[str], runner: str, notes: List[str]) -> None:
    """CI-7: move jobs from GitHub's machines to the project's own runner."""
    for index, line in enumerate(lines):
        match = HOSTED.match(line)
        if match:
            lines[index] = f"{match.group('head')}{runner}" + (f"  {match.group('tail')}" if match.group("tail") else "")
            notes.append(f"CI-7: runs-on {line.split(':', 1)[1].strip()} -> {runner}")


def fix_text(text: str, runner: Optional[str] = None) -> Tuple[str, List[str]]:
    notes: List[str] = []
    lines = text.split("\n")
    pin(lines, notes)
    if runner:
        own_runners(lines, runner, notes)
    add_timeouts(lines, notes)
    if sc.workflow_triggers(text).get("push") is False:
        push_to_main(lines, notes)
    if not re.search(r"(?m)^\s*permissions\s*:", text) and before_jobs(lines, "permissions:\n  contents: read\n"):
        notes.append("CI-4: permissions: contents: read")
    if "pull_request" in sc.workflow_triggers(text) and not re.search(r"(?m)^\s*concurrency\s*:", text):
        if before_jobs(lines, CONCURRENCY):
            notes.append("CI-8: concurrency that cancels a pull request's older run")
    return "\n".join(lines), notes


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo", default=".", help="the checkout to fix (default: this one)")
    parser.add_argument("--write", action="store_true", help="change the files (default: only show)")
    parser.add_argument("--keep-runners", action="store_true", help="never change runs-on (a public repository)")
    args = parser.parse_args(argv)
    runners = sc.Repo(Path(args.repo)).record.get("ci_runners")
    runner = runners[0] if isinstance(runners, list) and runners and isinstance(runners[0], str) else None
    if args.keep_runners:
        runner = None
    folder = Path(args.repo) / ".github" / "workflows"
    files = sorted(list(folder.glob("*.yml")) + list(folder.glob("*.yaml"))) if folder.is_dir() else []
    changed = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        fixed, notes = fix_text(text, runner)
        if not notes:
            continue
        print(f"{path.relative_to(args.repo)}:")
        for note in notes:
            print(f"  {note}")
        if fixed != text:
            changed += 1
            if args.write:
                path.write_text(fixed, encoding="utf-8")
    verb = "changed" if args.write else "would change"
    print(f"{verb} {changed} of {len(files)} workflow files" + ("" if args.write or not changed else "; --write to apply"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
