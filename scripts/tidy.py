#!/usr/bin/env python3
"""Tidy this clone after agents and sessions: stale branches and worktrees, unpushed work, finished issues.

    python3 scripts/tidy.py              clean what is safe, report the rest
    python3 scripts/tidy.py --dry-run    say what it would do and do nothing
    python3 scripts/tidy.py --issues     also list open issues that a merged pull request mentions (needs `gh`)

Cleans, because nothing is lost by it:

- worktrees git lists as prunable (their folder is gone);
- a local branch whose remote branch is gone (GitHub deleted it after the merge) or that never had one, when every one
  of its commits is already in the default branch (by hash or by patch, so a squash merge counts), with its worktree
  if that worktree has no uncommitted changes.

Reports and never touches, because it is someone's work:

- a branch with commits that are not on the default branch and not on any remote (UNPUSHED: push it or decide);
- a worktree with uncommitted changes (DIRTY), a stash, a branch that is checked out;
- with `--issues`, an open issue named by a merged pull request (`Refs #N`, `For #N`, `Fixes #N`) that is still open:
  a finished issue left open, or one a pull request only half did. A person or a session reads it and closes it with
  a comment saying which pull request did it; this script never closes one.

The default branch and the builder's branch are never deleted. Exits 0 when everything is clean or was cleaned, 1 when
something is left that needs a decision, so a session ends its run with it. Standard library only; Python 3.9. Owned by
the template: change it there, not in a project.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Callable, Dict, List, Optional, Set

KEEP = {"main", "master", "lovable-sync"}
MENTION = re.compile(r"(?i)\b(?:refs?|for|fix(?:es|ed)?|close[sd]?|resolve[sd]?|issue)\s+#(\d+)")


def run(args: List[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def git_in(cwd: Optional[str]) -> Callable[..., str]:
    def git(*args: str) -> str:
        return run(["git", *args], cwd).stdout.strip()
    return git


def default_branch(git: Callable[..., str]) -> str:
    ref = git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    return ref.split("/", 1)[1] if ref.startswith("origin/") else "main"


def worktrees(git: Callable[..., str]) -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []
    for block in git("worktree", "list", "--porcelain").split("\n\n"):
        item: Dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            item[key] = value
        if item:
            found.append(item)
    return found


def is_merged(git: Callable[..., str], branch: str, base: str) -> bool:
    """Every commit of `branch` is in `base`, by hash or by patch (a squash merge leaves the same changes)."""
    return not any(line.startswith("+") for line in git("cherry", base, branch).splitlines())


def tidy(cwd: Optional[str] = None, dry_run: bool = False, say: Callable[[str], None] = print) -> int:
    """Returns how many things are left that need a decision."""
    git = git_in(cwd)
    run(["git", "fetch", "--prune", "--quiet", "origin"], cwd)
    default = default_branch(git)
    base = f"origin/{default}"
    left = 0

    prunable = [w for w in worktrees(git) if "prunable" in w]
    for item in prunable:
        say(f"{'would prune' if dry_run else 'pruned'} the worktree whose folder is gone: {item['worktree']}")
    if prunable and not dry_run:
        git("worktree", "prune")

    trees = worktrees(git)
    main_tree = trees[0]["worktree"] if trees else ""
    checked_out = {w["branch"].replace("refs/heads/", ""): w["worktree"] for w in trees if "branch" in w}

    gone: Set[str] = set()
    for line in git("for-each-ref", "--format=%(refname:short)\t%(upstream:track)\t%(upstream)", "refs/heads").splitlines():
        name, track, upstream = (line.split("\t") + ["", ""])[:3]
        if name in KEEP or name == default:
            continue
        if track == "[gone]" or not upstream:
            gone.add(name)

    for branch in sorted(gone):
        tree = checked_out.get(branch)
        if tree and tree == main_tree:
            say(f"LEFT {branch}: checked out in the main working tree; check out {default} first")
            left += 1
            continue
        if not is_merged(git, branch, base):
            ahead = git("rev-list", "--count", f"{base}..{branch}")
            remotes = git("branch", "-r", "--contains", branch)
            if not remotes:
                say(f"UNPUSHED {branch}: {ahead} commit(s) on no remote and not in {default}; push it or decide")
                left += 1
            continue  # it exists on a remote: someone else's open work
        if tree:
            if git("-C", tree, "status", "--porcelain"):
                say(f"DIRTY {tree}: uncommitted changes on {branch}; commit, push or discard them")
                left += 1
                continue
            say(f"{'would remove' if dry_run else 'removed'} the worktree {tree}")
            if not dry_run:
                git("worktree", "remove", tree)
        say(f"{'would delete' if dry_run else 'deleted'} the local branch {branch}: merged into {default}")
        if not dry_run:
            git("branch", "-D", branch)

    for item in trees[1:]:
        branch = item.get("branch", "").replace("refs/heads/", "")
        if branch in gone or "prunable" in item:
            continue
        if git("-C", item["worktree"], "status", "--porcelain"):
            say(f"DIRTY {item['worktree']}: uncommitted changes on {branch or 'a detached head'}")
            left += 1

    for stash in git("stash", "list").splitlines():
        say(f"STASH {stash}")
        left += 1

    return left


def mentioned_open_issues(cwd: Optional[str] = None) -> List[Dict[str, object]]:
    """Open issues that a merged pull request names: finished and left open, or half done."""
    prs = json.loads(run(["gh", "pr", "list", "--state", "merged", "--limit", "100", "--json", "number,title,body"], cwd).stdout or "[]")
    opened = {i["number"]: i for i in json.loads(run(["gh", "issue", "list", "--state", "open", "--limit", "200", "--json", "number,title"], cwd).stdout or "[]")}
    found: Dict[int, List[int]] = {}
    for pr in prs:
        for number in {int(n) for n in MENTION.findall(f"{pr['title']}\n{pr['body'] or ''}")}:
            if number in opened:
                found.setdefault(number, []).append(pr["number"])
    return [{"issue": n, "title": opened[n]["title"], "pulls": sorted(p)} for n, p in sorted(found.items())]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--issues", action="store_true")
    parser.add_argument("--clone", help="tidy that clone instead of the current directory")
    args = parser.parse_args()
    left = tidy(args.clone, args.dry_run)
    if args.issues:
        for item in mentioned_open_issues(args.clone):
            pulls = ", ".join(f"PR #{p}" for p in item["pulls"])
            print(f"ISSUE #{item['issue']} is open but {pulls} merged and names it: {item['title']}")
            left += 1
    print("clean" if not left else f"{left} thing(s) need a decision")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
