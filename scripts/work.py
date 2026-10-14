#!/usr/bin/env python3
"""Start and finish a piece of work the way the standard counts it: one issue, its branch, its commits, its friction.

    python3 scripts/work.py start 140 [short-name]   a branch `140-short-name` from the default branch (WORK-8),
                                                     and a commit-msg hook that adds `Refs #140` (WORK-3)
    python3 scripts/work.py friction "ci-wait x3 ~20 min; none else"
                                                     one comment on the pinned `friction` issue (WORK-5); the issue,
                                                     its label and the pin are made the first time
    python3 scripts/work.py friction                 only say where the friction issue is

--repo OWNER/NAME works on another repository's issues (default: this one). It needs the gh CLI, signed in. The name
comes from the issue's title when none is given. The hook goes into this clone only (git's hooks folder is not
versioned); `start` puts it there, and leaves alone a commit-msg hook it did not write. A friction comment holds counts
and short descriptions only, never conversation text or anyone's content: it is refused above 600 characters.

Owned by the template: change it there, not in a project.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

LABEL = "friction"
MAX_FRICTION = 600
HOOK_MARK = "# written by scripts/work.py"
HOOK = f"""#!/bin/sh
{HOOK_MARK}: adds `Refs #N` on a branch named N-something (WORK-3)
exec python3 "$(git rev-parse --show-toplevel)/scripts/work.py" commit-msg "$1"
"""
TRAILER = re.compile(r"^[A-Za-z][A-Za-z0-9-]*: ")
FRICTION_BODY = (
    "The friction log (WORK-5). At the end of each piece of work (a pull request merged, an issue closed, a long "
    "session ending) add one comment: each friction item with a tag, a count and the rough cost in turns or minutes, "
    "or \"none\". Counts and short descriptions only, never conversation text or anyone's content. A tag seen in three "
    "pieces of work becomes a tool proposal issue (WORK-6).\n\n`python3 scripts/work.py friction \"TEXT\"` adds one."
)


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


def install_hook(say: Callable[[str], None] = print) -> bool:
    hooks = Path(sh(["git", "rev-parse", "--git-path", "hooks"]).strip())
    hook = hooks / "commit-msg"
    if hook.exists() and HOOK_MARK not in hook.read_text(encoding="utf-8", errors="replace"):
        say(f"left alone: {hook} is not this kit's; add `Refs #N` to each commit by hand (WORK-3)")
        return False
    hooks.mkdir(parents=True, exist_ok=True)
    hook.write_text(HOOK, encoding="utf-8")
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
    return 0


def friction_issue(repo: Optional[str], say: Callable[[str], None] = print) -> str:
    """The number of the open friction issue, made, labelled and pinned the first time."""
    found = json.loads(gh(["issue", "list", "--label", LABEL, "--state", "open", "--json", "number"], repo) or "[]")
    if found:
        return str(sorted(item["number"] for item in found)[0])
    sh(["gh", "label", "create", LABEL, "--force", "--color", "C5DEF5",
        "--description", "Friction log of agent work (WORK-5)", *(["--repo", repo] if repo else [])])
    url = gh(["issue", "create", "--title", "Friction log", "--label", LABEL, "--body", FRICTION_BODY], repo).strip()
    number = url.rstrip("/").rsplit("/", 1)[-1]
    gh(["issue", "pin", number], repo)
    say(f"made and pinned the friction issue #{number}")
    return number


def friction(text: Optional[str], repo: Optional[str], say: Callable[[str], None] = print) -> int:
    if text is not None:
        text = text.strip()
        if not text:
            say('say what repeated, or "none"')
            return 2
        if len(text) > MAX_FRICTION:
            say(f"{len(text)} characters: a friction comment is counts and short descriptions, at most {MAX_FRICTION}")
            return 2
    number = friction_issue(repo, say)
    if text is None:
        say(f"the friction issue is #{number}")
        return 0
    gh(["issue", "comment", number, "--body", text], repo)
    say(f"added to the friction issue #{number}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="step", required=True)
    starting = sub.add_parser("start", help="a branch named after the issue, and the Refs hook")
    starting.add_argument("number")
    starting.add_argument("name", nargs="?", help="a short name (default: from the issue's title)")
    adding = sub.add_parser("friction", help="add to the friction log (no text: only say where it is)")
    adding.add_argument("text", nargs="?")
    hook = sub.add_parser("commit-msg", help=argparse.SUPPRESS)
    hook.add_argument("path")
    for p in (starting, adding):
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
        return friction(args.text, args.repo)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
