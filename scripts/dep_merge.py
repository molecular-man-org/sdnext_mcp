#!/usr/bin/env python3
"""Merge Dependabot's pull requests by themselves, where the owner allowed it.

    python3 scripts/dep_merge.py            in CI, every few hours (.github/workflows/dependency-merge.yml)
    python3 scripts/dep_merge.py --dry-run  say what it would merge

It runs where the record allows it: `"dependency_automerge": {"approved_by": NAME, "date": DAY}` in standard.json,
the owner's standing go-ahead for dependency updates in this project, or by default in a repository whose `category`
is `tools` or `private-lan` (DEP-4 since standard 2.0, the owner's decision of 2026-10-29; `"dependency_automerge":
false` switches it off there). Then it merges each open pull request from Dependabot into the default branch when all
of these hold:

- it is at least 24 hours old, so the version it brings is too (DEP-3: nothing younger than a day is installed);
- every check on its head finished and passed, and there is at least one;
- GitHub says it can merge without a conflict;
- with `"majors": false` in that entry, it is not a major version: one whose title does not read "from A to B" with
  the same major number (for 0.x, the same minor) waits for a person, a grouped update included.

It merges with a merge commit (REPO-9), pinned to the head it checked. Anything else waits for the next run, and a
pull request whose checks failed stays open for a person. Standard library only; Python 3.9. Owned by the template:
change it there, not in a project.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

BOT = "dependabot[bot]"
MIN_AGE = datetime.timedelta(hours=24)
PASSED = {"success", "skipped", "neutral"}

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


DEFAULT_CATEGORIES = ("tools", "private-lan")  # DEP-4 (2.0): on by default here, unless the record says false


def allowed(record: Dict[str, Any]) -> Optional[str]:
    """Who allowed it and when, or None when neither the record nor the category's default allows it."""
    entry = record.get("dependency_automerge")
    if entry is False:
        return None
    if isinstance(entry, dict) and str(entry.get("approved_by", "")).strip() and str(entry.get("date", "")).strip():
        return f"{entry['approved_by']} on {entry['date']}"
    if entry is None and record.get("category") in DEFAULT_CATEGORIES:
        return f"the standard's default for the category {record['category']} (DEP-4, standard 2.0)"
    return None


def majors_allowed(record: Dict[str, Any]) -> bool:
    """False only when the record's entry says `"majors": false`: a major version then waits for a person."""
    entry = record.get("dependency_automerge")
    return not (isinstance(entry, dict) and entry.get("majors") is False)


def is_major(title: str) -> bool:
    """True unless the title shows a version change within one major (0.x: one minor). Unknown counts as major."""
    found = re.search(r"\bfrom v?(\d+)\.(\d+)\S* to v?(\d+)\.(\d+)", title)
    if not found:
        return True
    old_major, old_minor, new_major, new_minor = (int(part) for part in found.groups())
    return old_major != new_major or (old_major == 0 and old_minor != new_minor)


def checks_passed(api: Api, repo: str, sha: str) -> str:
    """'' when every check on `sha` finished and passed; otherwise why not."""
    runs = (api("GET", f"/repos/{repo}/commits/{sha}/check-runs?per_page=100", None) or {}).get("check_runs", [])
    status = api("GET", f"/repos/{repo}/commits/{sha}/status", None) or {}
    if not runs and not status.get("total_count"):
        return "no checks have run"
    waiting = [r["name"] for r in runs if r.get("status") != "completed"]
    if waiting:
        return "checks still running: " + ", ".join(waiting)
    failed = [r["name"] for r in runs if r.get("conclusion") not in PASSED]
    if failed:
        return "checks failed: " + ", ".join(failed)
    if status.get("total_count") and status.get("state") != "success":
        return f"commit statuses are {status.get('state')}"
    return ""


def run(api: Api, repo: str, now: datetime.datetime, dry_run: bool = False, majors: bool = True) -> List[str]:
    default = api("GET", f"/repos/{repo}", None)["default_branch"]
    said = []
    for pr in api("GET", f"/repos/{repo}/pulls?state=open&per_page=100", None) or []:
        if (pr.get("user") or {}).get("login") != BOT or pr["base"]["ref"] != default or pr.get("draft"):
            continue
        name = f"#{pr['number']} {pr['title']}"
        if not majors and is_major(pr["title"]):
            said.append(f"waits: {name} (a major version: for a person)")
            continue
        created = datetime.datetime.strptime(pr["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        if now - created < MIN_AGE:
            said.append(f"waits: {name} (younger than 24 hours)")
            continue
        sha = pr["head"]["sha"]
        why = checks_passed(api, repo, sha)
        if why:
            said.append(f"waits: {name} ({why})")
            continue
        if api("GET", f"/repos/{repo}/pulls/{pr['number']}", None).get("mergeable") is not True:
            said.append(f"waits: {name} (GitHub cannot merge it yet: a conflict, or not computed)")
            continue
        if dry_run:
            said.append(f"would merge: {name}")
            continue
        api("PUT", f"/repos/{repo}/pulls/{pr['number']}/merge", {"merge_method": "merge", "sha": sha})
        said.append(f"merged: {name}")
    return said


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Merge Dependabot's pull requests where the owner allowed it.")
    parser.add_argument("--repo", default=".", help="the checkout whose standard.json says whether it is allowed")
    parser.add_argument("--dry-run", action="store_true", help="say what it would merge, merge nothing")
    args = parser.parse_args(argv)
    record_path = Path(args.repo) / "standard.json"
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.is_file() else {}
    if "category" not in record:  # a tools repository keeps no record (standard 3.0): Copier's answers name its category
        answers = Path(args.repo) / ".copier-answers.yml"
        found = re.search(r"(?m)^category:\s*(\S+)\s*$", answers.read_text(encoding="utf-8")) if answers.is_file() else None
        if found:
            record["category"] = found.group(1)
    who = allowed(record)
    if not who:
        print("Dependency pull requests are not merged by themselves here: standard.json has no "
              "dependency_automerge with approved_by and date (the owner's go-ahead), and the category is not "
              "tools or private-lan, where the standard switches it on by default (DEP-4).")
        return 0
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo or not os.environ.get("GITHUB_TOKEN"):
        sys.exit("dep_merge: needs GITHUB_REPOSITORY and GITHUB_TOKEN")
    print(f"Dependency pull requests merge by themselves here (allowed by {who}).")
    majors = majors_allowed(record)
    now = datetime.datetime.now(datetime.timezone.utc)
    for line in run(github_api, repo, now, args.dry_run, majors) or ["none open"]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
