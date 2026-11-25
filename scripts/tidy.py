#!/usr/bin/env python3
"""Tidy this clone after agents and sessions: stale branches and worktrees, unpushed work, finished issues.

    python3 scripts/tidy.py              clean what is safe, report the rest
    python3 scripts/tidy.py --dry-run    say what it would do and do nothing
    python3 scripts/tidy.py --issues     also list open issues that a merged pull request mentions (needs `gh`)
    python3 scripts/tidy.py --keep-orphans   leave processes in deleted scratch folders alone (they are stopped by default)
    python3 scripts/tidy.py --sessions 24    also remove the locked worktrees of Claude remote sessions idle 24 hours or more

Cleans, because nothing is lost by it:

- processes of this user whose working directory was a scratch folder under the temporary directory that is now gone
  (a server a run left behind): SIGTERM, then SIGKILL after a few seconds (Issue #214);
- worktrees git lists as prunable (their folder is gone);
- a local branch whose remote branch is gone (GitHub deleted it after the merge) or that never had one, when every one
  of its commits is already in the default branch (by hash or by patch) or its tip is the head of a merged pull request
  (so a squash merge of several commits counts), with its worktree
  if that worktree has no uncommitted changes.

Reports and never touches, because it is someone's work:

- a branch with commits that are not on the default branch and not on any remote (UNPUSHED: push it or decide);
- a worktree with uncommitted changes (DIRTY), a stash, a branch that is checked out;
- with `--issues`, an open issue named by a merged pull request (`Refs #N`, `For #N`, `Fixes #N`) that is still open:
  a finished issue left open, or one a pull request only half did. A person or a session reads it and closes it with
  a comment saying which pull request did it; this script never closes one. An issue whose latest comment says it stays
  open ("Left open", "Not closing", "stays open") or that carries a queue skip label (parked, owner-decision, ...) is not
  listed: a line from this script means "probably finished".

With `--sessions HOURS` it also removes the worktrees a Claude remote session leaves under `.claude/worktrees/bridge-*`
(git locks each with "claude agent ...", so `git worktree prune` and the rules above skip them; Issue #171), but only a
worktree that is not the one it runs in, has no uncommitted changes, whose commits are all in the default branch or on
a remote, and that nothing has touched for HOURS hours: a session still at work, or one with work only here, is kept.

The default branch and the builder's branch are never deleted. Exits 0 when everything is clean or was cleaned, 1 when
something is left that needs a decision, so a session ends its run with it. Standard library only; Python 3.9. Owned by
the template: change it there, not in a project.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Callable, Dict, List, Optional, Set

KEEP = {"main", "master", "lovable-sync"}
# What the issue queue reads to skip an issue: such an issue is parked on purpose, not finished and forgotten.
SKIP_LABELS = {"in-progress", "parked", "owner-decision", "question", "blocked-external", "needs-migration", "deferred"}
# A comment that says the issue stays open after the pull request that names it (Issue #200).
KEEP_OPEN = re.compile(r"(?i)\b(left open|not closing|stays? open|left on this issue|still open|leave (?:it|this) open|not finished)\b")
MENTION = re.compile(r"(?i)\b(?:refs?|for|fix(?:es|ed)?|close[sd]?|resolve[sd]?|issue)\s+#(\d+)")


def run(args: List[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:  # a hung git or gh call must not stall the run (Issue #206): it failed
        return subprocess.CompletedProcess(args, 124, "", f"{' '.join(args[:3])} did not finish in 300 seconds")


def git_in(cwd: Optional[str]) -> Callable[..., str]:
    def git(*args: str) -> str:
        return run(["git", *args], cwd).stdout.strip()
    return git


def default_branch(git: Callable[..., str]) -> str:
    ref = git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    return ref.split("/", 1)[1] if ref.startswith("origin/") else "main"


def end_processes(pids: List[int], where: str, say: Callable[[str], None] = print, grace: float = 3.0) -> int:
    """SIGTERM each process, wait up to `grace` seconds, then SIGKILL the ones still there: a server that was left
    running ignored SIGTERM (Issue #214). Returns how many were signalled."""
    import signal
    import time

    sent = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            sent.append(pid)
        except OSError:
            pass
    deadline = time.monotonic() + grace
    alive = list(sent)
    while alive and time.monotonic() < deadline:
        time.sleep(0.1)
        alive = [pid for pid in alive if _alive(pid)]
    for pid in sent:
        if pid in alive:
            try:
                os.kill(pid, signal.SIGKILL)
                say(f"process {pid} ignored SIGTERM and was killed, {where}")
                continue
            except OSError:
                pass
        say(f"stopped process {pid}, {where}")
    return len(sent)


def _alive(pid: int) -> bool:
    """Still running, and not just waiting for its parent to collect it."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def stop_orphans(scratch: Optional[str] = None, say: Callable[[str], None] = print, dry_run: bool = False,
                 grace: float = 3.0) -> int:
    """Stop this user's processes whose working directory was deleted under the temporary directory: what a session left
    running in a scratch folder it removed (eight `node` servers ran for a day at 90% CPU, Issue #214). Linux only."""
    import tempfile

    if not os.path.isdir("/proc"):
        return 0
    root = os.path.realpath(scratch or tempfile.gettempdir())
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            cwd = os.readlink(f"/proc/{entry}/cwd")
            if os.stat(f"/proc/{entry}").st_uid != os.getuid():
                continue
        except OSError:
            continue
        if cwd.endswith(" (deleted)") and cwd.startswith(root + os.sep):
            pids.append(int(entry))
    if dry_run:
        for pid in pids:
            say(f"would stop process {pid}, whose scratch folder under {root} is gone")
        return len(pids)
    return end_processes(pids, f"its scratch folder under {root} is gone", say, grace)


def stop_processes_in(folder: str, say: Callable[[str], None] = print, grace: float = 3.0) -> int:
    """Stop the processes whose working directory is inside `folder` (a dev server a browser test started there keeps
    running after the folder is removed: Issue #140). Linux reads /proc; elsewhere lsof. Returns how many were stopped."""
    root = os.path.realpath(folder)
    pids: List[int] = []
    if os.path.isdir("/proc"):
        for entry in os.listdir("/proc"):
            if entry.isdigit() and int(entry) != os.getpid():
                try:
                    cwd = os.readlink(f"/proc/{entry}/cwd")
                except OSError:
                    continue
                if cwd == root or cwd.startswith(root + os.sep):
                    pids.append(int(entry))
    else:
        try:
            out = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fp", "+D", root], capture_output=True, text=True, check=False,
                                 timeout=60)
        except subprocess.TimeoutExpired:
            out = subprocess.CompletedProcess([], 124, "", "")
        pids = [int(line[1:]) for line in out.stdout.splitlines() if line.startswith("p") and line[1:].isdigit()
                and int(line[1:]) != os.getpid()]
    return end_processes(pids, f"which was running in {folder}", say, grace)


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


def merged_pull_heads(branch: str, cwd: Optional[str] = None) -> List[str]:
    """The head commits of the merged pull requests that came from a branch of this name; none without `gh`. A squash
    merge of several commits leaves a patch `git cherry` cannot match to any one of them (Issue #202)."""
    done = run(["gh", "pr", "list", "--state", "merged", "--head", branch, "--limit", "20", "--json", "headRefOid"], cwd)
    try:
        return [str(item["headRefOid"]) for item in json.loads(done.stdout or "[]")]
    except (ValueError, KeyError, TypeError):
        return []


def is_done(git: Callable[..., str], branch: str, base: str, cwd: Optional[str] = None) -> bool:
    """Merged by hash or patch, or its tip is the head of a pull request that merged (a squash merge, whatever the
    number of commits). A branch with commits after that merged head is not done."""
    return is_merged(git, branch, base) or git("rev-parse", branch) in merged_pull_heads(branch, cwd)


SESSION_TREE = re.compile(r"/\.claude/worktrees/bridge-[^/]+$")


def last_touched(git_dir: str) -> float:
    """The newest change time among the files git rewrites when someone commits or switches branch in a worktree."""
    times = [0.0]
    for name in ("HEAD", "logs/HEAD"):  # not the index: `git status` rewrites it
        try:
            times.append(os.path.getmtime(os.path.join(git_dir, name)))
        except OSError:
            pass
    return max(times)


def stale_sessions(git: Callable[..., str], trees: List[Dict[str, str]], base: str, here: str, hours: float,
                   dry_run: bool, say: Callable[[str], None], now: Optional[float] = None) -> None:
    """Remove the locked worktrees of remote sessions that nobody has touched for `hours` and that hold no work that is
    only there. Never the worktree this runs in (Issue #171)."""
    import time

    now = time.time() if now is None else now
    for item in trees[1:]:
        path = item["worktree"]
        if not SESSION_TREE.search(path) or not item.get("locked", "").startswith("claude agent"):
            continue
        if os.path.realpath(path) == os.path.realpath(here):
            continue
        if git("-C", path, "status", "--porcelain"):
            say(f"KEPT {path}: uncommitted changes")
            continue
        head = git("-C", path, "rev-parse", "HEAD")
        branch = item.get("branch", "").replace("refs/heads/", "")
        on_remote = bool(git("branch", "-r", "--contains", head))
        in_base = not git("rev-list", "--max-count=1", f"{base}..{head}")
        if not (on_remote or in_base):
            say(f"KEPT {path}: commits that are on no remote and not in {base}")
            continue
        idle = (now - last_touched(os.path.join(git("-C", path, "rev-parse", "--absolute-git-dir")))) / 3600
        if idle < hours:
            say(f"KEPT {path}: touched {idle:.1f} hours ago, less than {hours:g}")
            continue
        say(f"{'would remove' if dry_run else 'removed'} the session worktree {path} (idle {idle:.0f} hours)")
        if dry_run:
            continue
        git("worktree", "unlock", path)
        stop_processes_in(path, say)
        git("worktree", "remove", "--force", path)
        if branch and branch not in KEEP:
            git("branch", "-D", branch)


def tidy(cwd: Optional[str] = None, dry_run: bool = False, say: Callable[[str], None] = print,
         session_hours: Optional[float] = None, orphans: bool = False) -> int:
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
        if tree and "locked" in next((w for w in trees if w["worktree"] == tree), {}):
            continue  # git locks a session's worktree: a session may be working in it right now (Issue #171)
        if not is_done(git, branch, base, cwd):
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
                stop_processes_in(tree, say)
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

    if session_hours is not None:
        stale_sessions(git, worktrees(git), base, git("rev-parse", "--show-toplevel"), session_hours, dry_run, say)
        run(["git", "worktree", "prune"], cwd)

    if orphans:  # the command line asks for it; a test of tidy must not signal the machine's real processes
        stop_orphans(say=say, dry_run=dry_run)

    for stash in git("stash", "list").splitlines():
        say(f"STASH {stash}")
        left += 1

    return left


def stays_open(issue: Dict[str, object]) -> bool:
    """The issue carries a queue skip label, or its latest comment says it stays open: the merged pull request that names
    it did a part, and the listing it would make is a false lead."""
    labels = {str(label.get("name", "")) for label in issue.get("labels", []) or []}  # type: ignore[union-attr]
    if labels & SKIP_LABELS:
        return True
    comments = issue.get("comments") or []
    return bool(comments) and bool(KEEP_OPEN.search(str(comments[-1].get("body", ""))))  # type: ignore[index,union-attr]


def mentioned_open_issues(cwd: Optional[str] = None) -> List[Dict[str, object]]:
    """Open issues that a merged pull request names: finished and left open, or half done. Not the ones that say they
    stay open or are labelled to be skipped."""
    prs = json.loads(run(["gh", "pr", "list", "--state", "merged", "--limit", "100", "--json", "number,title,body"], cwd).stdout or "[]")
    listing = run(["gh", "issue", "list", "--state", "open", "--limit", "200", "--json", "number,title,labels,comments"], cwd).stdout
    opened = {i["number"]: i for i in json.loads(listing or "[]")}
    found: Dict[int, List[int]] = {}
    for pr in prs:
        for number in {int(n) for n in MENTION.findall(f"{pr['title']}\n{pr['body'] or ''}")}:
            if number in opened and not stays_open(opened[number]):
                found.setdefault(number, []).append(pr["number"])
    return [{"issue": n, "title": opened[n]["title"], "pulls": sorted(p)} for n, p in sorted(found.items())]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--issues", action="store_true")
    parser.add_argument("--sessions", type=float, metavar="HOURS",
                        help="also remove locked Claude remote-session worktrees idle this many hours (Issue #171)")
    parser.add_argument("--keep-orphans", action="store_true",
                        help="do not stop processes left running in a scratch folder that is gone (Issue #214)")
    parser.add_argument("--clone", help="tidy that clone instead of the current directory")
    args = parser.parse_args()
    if args.sessions is not None and args.sessions <= 0:
        parser.error("--sessions takes a positive number of hours")
    left = tidy(args.clone, args.dry_run, session_hours=args.sessions, orphans=not args.keep_orphans)
    if args.issues:
        for item in mentioned_open_issues(args.clone):
            pulls = ", ".join(f"PR #{p}" for p in item["pulls"])
            print(f"ISSUE #{item['issue']} is open but {pulls} merged and names it: {item['title']}")
            left += 1
    print("clean" if not left else f"{left} thing(s) need a decision")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
