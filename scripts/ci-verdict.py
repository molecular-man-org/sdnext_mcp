#!/usr/bin/env python3
"""Reuse a pull request's CI verdict on main when the merged code is identical.

A pull request's CI tests the result of merging it into main. When main has
not moved before the merge, the merge commit on main holds exactly that code,
and running CI again proves nothing new. So:

  record  On a pull request run, after every job: mark the pull request's
          head commit with a commit status (context `ci/tested-tree`) whose
          description names the tree the run tested, and whether it passed.
  check   On a push to main: when HEAD is a merge commit whose second parent
          carries a passing `ci/tested-tree` status for HEAD's own tree, write
          `reuse=true` to $GITHUB_OUTPUT. The other jobs are then skipped.

Neither command ever fails the run. Why: a broken record or check must cost
runner time, never a red check (see "What may block" in the standard). On
any error, `check` says `reuse=false` and everything runs.

Standard library only; Python 3.9.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

CONTEXT = "ci/tested-tree"
PREFIX = "tree "

Api = Callable[[str, str, Optional[Dict[str, Any]]], Any]


def github_api(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", "Bearer " + os.environ["GITHUB_TOKEN"])
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode() or "null")


def tree_of(api: Api, repo: str, sha: str) -> str:
    return str(api("GET", f"/repos/{repo}/git/commits/{sha}", None)["tree"]["sha"])


def record(api: Api, repo: str, tested: str, head: str, passed: bool, url: str) -> str:
    """Mark `head` with the tree of `tested` (the merge commit the run checked out)."""
    tree = tree_of(api, repo, tested)
    state = "success" if passed else "failure"
    api(
        "POST",
        f"/repos/{repo}/statuses/{head}",
        {"state": state, "context": CONTEXT, "description": PREFIX + tree, "target_url": url},
    )
    return f"{head}: {state} for tree {tree}"


def decide(api: Api, repo: str, sha: str) -> Tuple[bool, str]:
    """(reuse?, why) for the commit `sha` pushed to main."""
    commit = api("GET", f"/repos/{repo}/commits/{sha}", None)
    parents = commit.get("parents", [])
    if len(parents) != 2:
        return False, "not a merge commit"
    tree = commit["commit"]["tree"]["sha"]
    head = parents[1]["sha"]
    statuses = api("GET", f"/repos/{repo}/commits/{head}/statuses?per_page=100", None)
    # The list is newest first; the newest status for the context is the verdict.
    latest = next((s for s in statuses if s.get("context") == CONTEXT), None)
    if latest is None:
        return False, f"no {CONTEXT} status on {head}"
    if latest.get("state") != "success":
        return False, f"the pull request's last run on {head} did not pass"
    if latest.get("description") != PREFIX + tree:
        return False, "main moved before the merge: the merged code was never tested as is"
    return True, f"tree {tree} passed on {head} ({latest.get('target_url', '')})"


def write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def main(argv: Optional[List[str]] = None, api: Api = github_api) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="mark a pull request's head with the tree its run tested")
    rec.add_argument("--head", required=True, help="the pull request's head commit")
    rec.add_argument("--result", required=True, choices=["success", "failure"])
    sub.add_parser("check", help="on main: may this push reuse a pull request's verdict?")
    args = parser.parse_args(argv)

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = os.environ.get("GITHUB_SHA", "")
    try:
        if args.command == "record":
            url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
            print(record(api, repo, sha, args.head, args.result == "success", url))
            return 0
        reuse, why = decide(api, repo, sha)
    except (urllib.error.URLError, KeyError, IndexError, TypeError, ValueError, OSError) as error:
        if args.command == "record":
            print(f"::warning::could not record the verdict ({error}); main will run every job")
            return 0
        reuse, why = False, f"could not check ({error})"
    print(("Reusing the verdict: " if reuse else "Running every job: ") + why)
    write_output("reuse", "true" if reuse else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
