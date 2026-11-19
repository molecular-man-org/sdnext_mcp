#!/usr/bin/env python3
"""Count the issues opened in a repository over the last days, by where they came from.

    python3 scripts/issue_count.py [--days 7] [--repo OWNER/NAME]

The rule "When to open an issue" (CLAUDE.md) only holds if it can be seen: this prints, for each origin, how many issues
were opened, and how many of all of them were closed within an hour of being opened (a session that files its own work
and does it at once; Issue #152 found 16 percent). The origin is read from the author, the labels and the "Found by /
Found while / Split from" lines of the body. It guesses; the numbers are for a trend, not for blame. Needs the `gh`
CLI. Standard library only; Python 3.9. Owned by the template: change it there, not in a project.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional

BOT_TITLE = re.compile(r"(?i)^(ci failed|nightly|vulnerab|security advisory|\[?vuln-scan)")
PARENT_LINE = re.compile(r"(?im)^\W*(found (?:by|while|in|during)|split from)\b")
ORIGINS = ("workflow", "found by a session", "owner decision or question", "bug", "feature or enhancement", "other")


def origin(issue: Dict[str, Any]) -> str:
    """One origin for one issue, the first that fits: a workflow, a session's own finding, an owner question, then labels."""
    author = (issue.get("author") or {}).get("login", "")
    labels = {str(label.get("name", "")).lower() for label in issue.get("labels") or []}
    body = issue.get("body") or ""
    if author.endswith("[bot]") or author.startswith("app/") or "<!-- vuln-scan" in body or BOT_TITLE.match(issue.get("title", "")):
        return "workflow"
    if PARENT_LINE.search(body):
        return "found by a session"
    if labels & {"owner-decision", "question"}:
        return "owner decision or question"
    if "bug" in labels:
        return "bug"
    if labels & {"enhancement", "feature"}:
        return "feature or enhancement"
    return "other"


def parse(stamp: Optional[str]) -> Optional[datetime.datetime]:
    return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None


def count(issues: List[Dict[str, Any]], now: datetime.datetime, days: int) -> Dict[str, Any]:
    """Origins of the issues opened in the last `days` days, and how many were closed within an hour."""
    since = now - datetime.timedelta(days=days)
    origins = {name: 0 for name in ORIGINS}
    opened = quick = 0
    for issue in issues:
        created = parse(issue.get("createdAt"))
        if created is None or created < since:
            continue
        opened += 1
        origins[origin(issue)] += 1
        closed = parse(issue.get("closedAt"))
        if closed is not None and closed - created <= datetime.timedelta(hours=1):
            quick += 1
    return {"days": days, "opened": opened, "origins": origins, "closed_within_an_hour": quick}


def render(result: Dict[str, Any]) -> str:
    lines = [f"Issues opened in the last {result['days']} days: {result['opened']}"]
    lines += [f"  {name:28} {number}" for name, number in result["origins"].items()]
    share = f" ({100 * result['closed_within_an_hour'] // result['opened']}%)" if result["opened"] else ""
    lines.append(f"Closed within an hour of being opened: {result['closed_within_an_hour']}{share}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--repo", help="OWNER/NAME (default: this repository)")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days takes a positive number")
    command = ["gh", "issue", "list", "--state", "all", "--limit", "1000", "--json", "number,title,body,labels,author,createdAt,closedAt"]
    if args.repo:
        command += ["-R", args.repo]
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode != 0:
        print(f"gh could not list the issues: {done.stderr.strip() or done.stdout.strip()}", file=sys.stderr)
        return 1
    print(render(count(json.loads(done.stdout or "[]"), datetime.datetime.now(datetime.timezone.utc), args.days)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
