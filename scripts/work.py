#!/usr/bin/env python3
"""Start a piece of work the way the standard counts it: one issue, its branch, its commits, its quick checks.

    python3 scripts/work.py start 140 [short-name]   a branch `140-short-name` from the default branch (WORK-8),
                                                     a commit-msg hook that adds `Refs #140` (WORK-3), and a pre-push
                                                     hook that runs the quick checks (`scripts/check.sh --quick`)
    python3 scripts/work.py read 13                  one issue as plain text: head, body and every comment, with
                                                     times in US Eastern

--repo OWNER/NAME works on another repository's issues (default: this one). It needs the gh CLI, signed in. The name
comes from the issue's title when none is given. The hooks go into this clone only (git's hooks folder is not
versioned); `start` puts them there, and leaves alone a hook it did not write. The pre-push hook refuses a push whose
quick checks fail (`git push --no-verify` still pushes; say so when you use it): a failure found here costs seconds,
one found in CI costs a round trip. A check command that does not know `--quick` runs everything.

Owned by the template: change it there, not in a project.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import datetime
from pathlib import Path
from typing import Callable, List, Optional

HOOK_MARK = "# written by scripts/work.py"
HOOK = f"""#!/bin/sh
{HOOK_MARK}: adds `Refs #N` on a branch named N-something (WORK-3)
exec python3 "$(git rev-parse --show-toplevel)/{{path}}" commit-msg "$1"
"""
PUSH_HOOK = f"""#!/bin/sh
{HOOK_MARK}: the quick checks before every push, so a failure costs seconds here and not a CI round trip
cd "$(git rev-parse --show-toplevel)" || exit 1
check=$(python3 -c 'import json; print(json.load(open("standard.json")).get("check_command") or "scripts/check.sh")' 2>/dev/null || echo scripts/check.sh)
if ! sh -c "$check --quick"; then
  echo "pre-push: the quick checks failed; nothing was pushed (git push --no-verify pushes anyway: say so)" >&2
  exit 1
fi
"""
TRAILER = re.compile(r"^[A-Za-z][A-Za-z0-9-]*: ")


def sh(args: List[str], check: bool = True) -> str:
    """Run a command and return what it printed. Replaced in the tests."""
    done = subprocess.run(args, capture_output=True, text=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


def gh(args: List[str], repo: Optional[str]) -> str:
    return sh(["gh", *args, *(["--repo", repo] if repo else [])])


def slug(text: str) -> str:
    """A short branch-safe name: lower case, words joined by '-', at most 40 characters."""
    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    name = ""
    for word in words:
        if len(name) + len(word) + 1 > 40:
            break
        name = f"{name}-{word}" if name else word
    return name or "work"


def with_refs(message: str, branch: str) -> str:
    """The commit message with `Refs #N` added for a branch named N-something, before any trailer lines at its end.

    Unchanged when the branch has no issue number or the message already names that issue."""
    match = re.match(r"(\d+)-", branch)
    if not match:
        return message
    number = match.group(1)
    text = re.split(r"(?m)^# -+ >8 -+$", message)[0]  # `git commit -v`: the diff below this line is not the message
    lines = [line for line in text.split("\n") if not line.startswith("#")]  # git's own comment lines
    if any(re.search(rf"#{number}\b", line) for line in lines):
        return message
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return message  # an empty message: git will abort the commit itself
    end = len(lines)
    while end > 1 and TRAILER.match(lines[end - 1]):
        end -= 1
    if end < len(lines) and lines[end - 1].strip():  # trailing lines that are not a block after a blank line
        end = len(lines)
    body, trailers = lines[:end], lines[end:]
    while body and not body[-1].strip():
        body.pop()
    out = body + ["", f"Refs #{number}"] + ([""] + trailers if trailers else [])
    return "\n".join(out) + "\n"


def hook_text() -> str:
    """The commit hook runs this very file: scripts/work.py in a project, template/scripts/work.py in the kit's own repository (#41)."""
    try:
        top = Path(sh(["git", "rev-parse", "--show-toplevel"]).strip()).resolve()
        path = Path(__file__).resolve().relative_to(top).as_posix()
    except (ValueError, OSError, subprocess.CalledProcessError):
        path = "scripts/work.py"
    return HOOK.format(path=path)


def install_hook(say: Callable[[str], None] = print, name: str = "commit-msg", content: Optional[str] = None) -> bool:
    """Write one of the kit's hooks into this clone; a hook another tool wrote is left alone."""
    if content is None:
        content = hook_text()
    hooks = Path(sh(["git", "rev-parse", "--git-path", "hooks"]).strip())
    hook = hooks / name
    if hook.exists() and HOOK_MARK not in hook.read_text(encoding="utf-8", errors="replace"):
        what = "add `Refs #N` to each commit by hand (WORK-3)" if name == "commit-msg" else "run scripts/check.sh --quick before each push yourself"
        say(f"left alone: {hook} is not this kit's; {what}")
        return False
    hooks.mkdir(parents=True, exist_ok=True)
    hook.write_text(content, encoding="utf-8")
    hook.chmod(0o755)
    return True


def start(number: str, name: Optional[str], repo: Optional[str], say: Callable[[str], None] = print) -> int:
    if not number.isdigit():
        say(f"not an issue number: {number}")
        return 2
    issue = json.loads(gh(["issue", "view", number, "--json", "title,state"], repo) or "{}")
    if issue.get("state") != "OPEN":
        say(f"issue #{number} is not open; work starts from an open issue (WORK-1)")
        return 1
    branch = f"{number}-{slug(name or issue.get('title', ''))}"
    default = sh(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], check=False).strip() or "origin/main"
    sh(["git", "fetch", "origin", "--quiet"])
    sh(["git", "switch", "-c", branch, default, "--no-track"])
    say(f"on {branch}, from {default}")
    if install_hook(say):
        say(f"each commit here will name #{number} (Refs #{number})")
    if install_hook(say, "pre-push", PUSH_HOOK):
        say("each push runs the quick checks first (scripts/check.sh --quick); a failure refuses the push")
    return 0


def eastern(stamp: str) -> str:
    """An ISO time from GitHub as `YYYY-MM-DD HH:MM AM/PM ET`; the owner's standing order is Eastern time."""
    from zoneinfo import ZoneInfo

    when = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
    return when.strftime("%Y-%m-%d %I:%M %p ") + ("EDT" if when.dst() else "EST")


def read_issue(number: str, repo: Optional[str], say: Callable[[str], None] = print) -> int:
    """One issue as plain text in one call: the head, the body and every comment."""
    out = gh(["issue", "view", number, "--json", "number,title,state,author,createdAt,labels,body,comments"], repo)
    issue = json.loads(out)
    author = (issue.get("author") or {}).get("login", "unknown")
    say(f"#{issue['number']} {issue['title']} [{issue['state']}] by {author}, {eastern(issue['createdAt'])}")
    say("labels: " + (", ".join(label["name"] for label in issue.get("labels", [])) or "none"))
    say("\n" + (issue.get("body") or "").rstrip())
    for comment in issue.get("comments", []):
        who = (comment.get("author") or {}).get("login", "unknown")
        say(f"\n--- {who}, {eastern(comment['createdAt'])}\n{(comment.get('body') or '').rstrip()}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="step", required=True)
    starting = sub.add_parser("start", help="a branch named after the issue, and the Refs hook")
    starting.add_argument("number")
    starting.add_argument("name", nargs="?", help="a short name (default: from the issue's title)")
    reading = sub.add_parser("read", help="one issue and its comments as plain text")
    reading.add_argument("number")
    hook = sub.add_parser("commit-msg", help=argparse.SUPPRESS)
    hook.add_argument("path")
    for p in (starting, reading):
        p.add_argument("--repo", help="OWNER/NAME (default: this repository)")
    args = parser.parse_args(argv)
    try:
        if args.step == "commit-msg":
            branch = sh(["git", "branch", "--show-current"], check=False).strip()
            path = Path(args.path)
            message = path.read_text(encoding="utf-8")
            changed = with_refs(message, branch)
            if changed != message:
                path.write_text(changed, encoding="utf-8")
            return 0
        if args.step == "start":
            return start(args.number, args.name, args.repo)
        if args.step == "read":
            return read_issue(args.number, args.repo)
        parser.error(f"unknown step {args.step}")
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
