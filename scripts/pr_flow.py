#!/usr/bin/env python3
"""A pull request's way to `main`, in one command: wait for CI, merge, wait for the deploy, clean up.

    python3 scripts/pr_flow.py ci 140                   wait for every check on the pull request's head; print each job
    python3 scripts/pr_flow.py merge 140                mark it ready and merge with a merge commit pinned to its head
    python3 scripts/pr_flow.py deploy SHA --version-url URL    wait until the live app reports the commit
    python3 scripts/pr_flow.py cleanup BRANCH           remove its worktree, the local branch, and the remote one if left
    python3 scripts/pr_flow.py all 140 [--version-url URL]     all four in order; it stops at the first that fails

--repo OWNER/NAME works on another repository (default: this one). It needs the gh CLI, signed in. Merge commits
only (REPO-9): the run on `main` can then reuse the pull request's verdict. It never merges a head whose checks
failed, and never force-pushes or rewrites anything.

From realmsculpt#480: about 40 hand-written calls for these four steps in one afternoon. Owned by the template:
change it there, not in a project.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

POLL_SECONDS = 15
DONE = {"success", "skipped", "neutral"}


def sh(args: List[str], check: bool = True) -> str:
    """Run a command and return what it printed. Replaced in the tests."""
    done = subprocess.run(args, capture_output=True, text=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def gh_json(args: List[str], repo: Optional[str]) -> Any:
    """A gh call's JSON. A GitHub server error (HTTP 5xx) is retried a few times before it counts."""
    for attempt in range(4):
        try:
            return json.loads(sh(["gh", *args, *(["--repo", repo] if repo else [])]) or "null")
        except RuntimeError as error:
            if "HTTP 5" not in str(error) or attempt == 3:
                raise
            sleep(5 * (attempt + 1))
    return None


def pull_request(number: str, repo: Optional[str]) -> Dict[str, Any]:
    return dict(gh_json(["pr", "view", number, "--json", "headRefOid,headRefName,state,isDraft,mergeCommit"], repo))


def wait_for_ci(number: str, repo: Optional[str], timeout: float, say: Callable[[str], None] = print) -> bool:
    """Wait until every workflow run on the pull request's head has finished; True when all passed."""
    head = pull_request(number, repo)["headRefOid"]
    deadline = time.monotonic() + timeout
    runs: List[Dict[str, Any]] = []
    while True:
        runs = list(gh_json(["run", "list", "--commit", head, "--json", "databaseId,name,status,conclusion"], repo))
        if runs and all(run["status"] == "completed" for run in runs):
            break
        if time.monotonic() > deadline:
            say(f"CI on {head[:7]} has not finished within {timeout:.0f} seconds")
            return False
        sleep(POLL_SECONDS)
    passed = True
    for run in runs:
        say(f"{run['name']}: {run['conclusion']}")
        view = gh_json(["run", "view", str(run["databaseId"]), "--json", "jobs"], repo)
        for job in view.get("jobs", []):
            say(f"  {job.get('name')}: {job.get('conclusion')} {duration(job.get('startedAt'), job.get('completedAt'))}")
        passed = passed and run["conclusion"] in DONE
    say(f"CI on {head[:7]}: {'passed' if passed else 'FAILED'}")
    return passed


def duration(start: Optional[str], end: Optional[str]) -> str:
    """'(3m 05s)' from two GitHub timestamps, or '' when either is missing."""
    import datetime

    if not start or not end:
        return ""
    try:
        seconds = int((datetime.datetime.fromisoformat(end.replace("Z", "+00:00"))
                       - datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds())
    except ValueError:
        return ""
    return f"({seconds // 60}m {seconds % 60:02d}s)"


def merge(number: str, repo: Optional[str], say: Callable[[str], None] = print) -> Optional[str]:
    """Mark ready and merge with a merge commit pinned to the head; the merge commit's id, or None."""
    pr = pull_request(number, repo)
    if pr["state"] == "MERGED":
        say(f"#{number} is merged already")
        return str((pr.get("mergeCommit") or {}).get("oid") or "") or None
    runs = list(gh_json(["run", "list", "--commit", pr["headRefOid"], "--json", "status,conclusion,name"], repo))
    unfinished = [run["name"] for run in runs if run["status"] != "completed" or run["conclusion"] not in DONE]
    if not runs or unfinished:
        say(f"#{number} not merged: CI on {pr['headRefOid'][:7]} has not passed "
            f"({', '.join(unfinished) or 'no run yet'}). Run the ci step first.")
        return None
    if pr.get("isDraft"):
        sh(["gh", "pr", "ready", number, *(["--repo", repo] if repo else [])])
    sh(["gh", "pr", "merge", number, "--merge", "--match-head-commit", pr["headRefOid"],
        *(["--repo", repo] if repo else [])])
    for _ in range(10):
        pr = pull_request(number, repo)
        oid = (pr.get("mergeCommit") or {}).get("oid")
        if pr["state"] == "MERGED" and oid:
            say(f"#{number} merged as {oid[:7]}")
            return str(oid)
        sleep(3)
    say(f"#{number} did not show as merged")
    return None


def wait_for_deploy(sha: str, url: str, timeout: float, say: Callable[[str], None] = print) -> bool:
    """Wait until the page at `url` names the commit (its first 7 characters are enough)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 -- the project's own address
                body = response.read().decode("utf-8", "replace")
            if sha[:7] in body:
                say(f"live: {sha[:7]} at {url}")
                return True
        except OSError:
            pass  # the app restarting during a deploy answers nothing for a moment
        if time.monotonic() > deadline:
            say(f"{url} did not report {sha[:7]} within {timeout:.0f} seconds")
            return False
        sleep(POLL_SECONDS)


def cleanup(branch: str, say: Callable[[str], None] = print) -> None:
    """Remove the branch's worktree and local branch, and the remote branch only if GitHub has not removed it."""
    worktree = None
    current = None
    for line in sh(["git", "worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            current = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}":
            worktree = current
    if worktree:
        sh(["git", "worktree", "remove", "--force", worktree])
        say(f"removed the worktree {worktree}")
    if sh(["git", "branch", "--list", branch]).strip():
        sh(["git", "branch", "-D", branch])
        say(f"deleted the local branch {branch}")
    if sh(["git", "ls-remote", "--heads", "origin", branch], check=False).strip():
        sh(["git", "push", "origin", "--delete", branch])
        say(f"deleted the remote branch {branch}")
    sh(["git", "worktree", "prune"])
    sh(["git", "fetch", "origin", "--prune", "--quiet"], check=False)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("step", choices=("ci", "merge", "deploy", "cleanup", "all"))
    parser.add_argument("target", help="the pull request number; for deploy the commit; for cleanup the branch")
    parser.add_argument("--repo", help="OWNER/NAME (default: this repository)")
    parser.add_argument("--version-url", help="an address of the live app that names the deployed commit")
    parser.add_argument("--timeout", type=float, default=1800, help="seconds to wait for CI or a deploy")
    args = parser.parse_args(argv)
    try:
        if args.step == "ci":
            return 0 if wait_for_ci(args.target, args.repo, args.timeout) else 1
        if args.step == "merge":
            return 0 if merge(args.target, args.repo) else 1
        if args.step == "deploy":
            if not args.version_url:
                parser.error("deploy needs --version-url")
            return 0 if wait_for_deploy(args.target, args.version_url, args.timeout) else 1
        if args.step == "cleanup":
            cleanup(args.target)
            return 0
        branch = pull_request(args.target, args.repo)["headRefName"]
        if not wait_for_ci(args.target, args.repo, args.timeout):
            return 1
        sha = merge(args.target, args.repo)
        if not sha:
            return 1
        if args.version_url and not wait_for_deploy(sha, args.version_url, args.timeout):
            return 1
        if not args.repo:
            cleanup(branch)
        else:  # another repository: no local checkout to tidy, but the merged branch goes (GitHub may have done it)
            sh(["gh", "api", "-X", "DELETE", f"repos/{args.repo}/git/refs/heads/{branch}"], check=False)
        return 0
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
