#!/usr/bin/env python3
"""List what is untrue in a repository's open issues, labels, pull requests and remote branches.

    python3 scripts/issue_hygiene.py                    this repository
    python3 scripts/issue_hygiene.py --repo OWNER/NAME  another one
    python3 scripts/issue_hygiene.py --json             the same findings for a skill or a script
    python3 scripts/issue_hygiene.py --labels           also create the kit's labels the repository lacks

The aim (WORK-10): every open issue is real, carries at least one label, its labels say how things stand now, no two
open issues are the same work, and nothing finished is left lying about. This lists what a machine can see:

- UNLABELLED          an open issue with no label;
- BLOCKER-GONE        an issue that says it is blocked by issues that are all closed, or by one that does not exist;
- BLOCKER-CIRCLE      open issues that block each other;
- DEFERRED-DUE        a `deferred` issue whose date ("Deferred until", "review on", "revisit on", "until") has passed, or that
                      names neither a date nor a condition ("Remove the label when ...", "leave this until ...");
- IN-PROGRESS-STALE   `in-progress` with no open pull request and nothing new for a day;
- FINISHED            an open issue a merged pull request closes by name (`Closes #N`, `Fixes #N`, `Resolves #N`);
- DUPLICATE           two open issues whose titles are nearly the same;
- PR-ISSUE-CLOSED     an open pull request whose issue is already closed;
- PR-STALE            an open pull request nothing has touched for --stale-days (14);
- PR-CONFLICT         an open pull request that no longer merges;
- BRANCH-NO-PR        a remote branch with no pull request, open or merged, and no commit for --stale-days;
- LABEL-TWIN          two label names that differ only in case or punctuation;
- LABEL-MISSING       a label the kit's queue uses that the repository does not have (`--labels` creates it).

It reads and reports. It never closes, relabels or deletes: whether an issue is still real, whether two are the same
work and whether a label is right are a reader's call (the `repo-cleanup` skill, or a person), and a mention by a merged
pull request alone was right for 2 issues in 71 (claude-repo-template issue 177). `--labels` only adds labels.

Exits 0 with nothing to report, 1 with findings, 2 when GitHub could not be read (it never says "clean" without having
looked). Needs the `gh` CLI. Standard library only; Python 3.9. Owned by the template: change it there, not in a project.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import json
import re
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# The labels the queue reads to skip an issue (.claude/queue.md, step 3), with what each says. A wrong one hides work.
KIT_LABELS: Dict[str, Tuple[str, str]] = {
    "in-progress": ("fbca04", "A session has claimed this and is working on it"),
    "parked": ("d93f0b", "Stopped: a person must decide or act before work goes on"),
    "owner-decision": ("b60205", "Waits for the owner's decision"),
    "question": ("d876e3", "Further information is requested"),
    "blocked-external": ("5319e7", "Waits on something outside this repository"),
    "needs-migration": ("c2e0c6", "Needs a database change that the owner applies"),
    "deferred": ("bfdadc", "Put off by the owner until the date in its last 'Deferred until' comment"),
    "queue": ("0e8a16", "A pull request opened by the issue queue"),
}
KEEP_BRANCHES = {"main", "master", "lovable-sync", "builder-cleanup/autofix"}
BLOCKED_BY = re.compile(r"(?i)\b(?:blocked (?:by|on)|depends on|waiting (?:on|for)|waits (?:on|for)|needs)\s+((?:#\d+(?:\s*(?:,|and|&)\s*)?)+)")
CLOSES = re.compile(r"(?i)\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#(\d+)")
# The owner's dated deferrals come worded several ways ("Deferred until 2026-12-01", "review on 2026-12-01", "revisit on",
# "until"): all name a date. A deferral that names a condition instead has no date to pass (Issue #201).
DEFERRED_UNTIL = re.compile(r"(?i)\b(?:deferred until|defer until|until|review(?:ed)? (?:on|by)|revisit(?:ed)? (?:on|by))\s+(\d{4}-\d{2}-\d{2})")
DEFERRED_CONDITION = re.compile(r"(?i)\b(?:remove the label when|leave (?:this|it) until|deferred until (?!\d{4}-\d{2}-\d{2}))")
DEFERRED_WORDING = "'Deferred until YYYY-MM-DD (owner, date): reason' or 'Remove the label when <the condition>'"
DUPLICATE_RATIO = 0.9

Finding = Dict[str, Any]


class GitHubError(Exception):
    """GitHub could not be read: no findings may be reported from a partial reading."""


def gh(args: List[str], repo: Optional[str]) -> str:
    try:
        done = subprocess.run(["gh", *args, *(["-R", repo] if repo and args[0] != "api" else [])], capture_output=True, text=True,
                              timeout=300)
    except subprocess.TimeoutExpired:
        raise GitHubError(f"gh {' '.join(args[:3])}: no answer in 300 seconds") from None
    if done.returncode != 0:
        raise GitHubError(f"gh {' '.join(args[:3])}: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


def parse(stamp: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def names(item: Dict[str, Any]) -> Set[str]:
    return {str(label.get("name", "")) for label in item.get("labels") or []}


def words(item: Dict[str, Any]) -> str:
    """The body of an issue or pull request with its comments, oldest first."""
    return "\n".join([item.get("body") or ""] + [c.get("body") or "" for c in item.get("comments") or []])


def blockers(issue: Dict[str, Any]) -> Set[int]:
    """The issue numbers this one says it waits for ("Blocked by #4 and #5", "Depends on #7")."""
    found: Set[int] = set()
    for match in BLOCKED_BY.finditer(words(issue)):
        found.update(int(n) for n in re.findall(r"#(\d+)", match.group(1)))
    found.discard(int(issue["number"]))
    return found


def finding(kind: str, item: Dict[str, Any], detail: str, **more: Any) -> Finding:
    return {"kind": kind, "number": item.get("number"), "title": item.get("title", ""), "detail": detail, **more}


def check_issues(issues: List[Dict[str, Any]], prs: List[Dict[str, Any]], state_of: Callable[[int], str],
                 now: datetime.datetime) -> List[Finding]:
    """Findings about open issues. `state_of(N)` is "open", "closed" or "missing" for any issue or pull request."""
    found: List[Finding] = []
    opened = {int(i["number"]) for i in issues}
    waits: Dict[int, Set[int]] = {}
    for issue in issues:
        labels = names(issue)
        if not labels:
            found.append(finding("UNLABELLED", issue, "no label"))
        named = blockers(issue)
        states = {n: ("open" if n in opened else state_of(n)) for n in sorted(named)}
        waits[int(issue["number"])] = {n for n, state in states.items() if state == "open"}
        missing = [n for n, state in states.items() if state == "missing"]
        if named and not waits[int(issue["number"])]:
            gone = ", ".join(f"#{n} ({states[n]})" for n in sorted(named))
            carried = sorted(labels & (set(KIT_LABELS) - {"queue"}))
            found.append(finding("BLOCKER-GONE", issue, f"says it waits for {gone}"
                                 + (f"; still labelled {', '.join(carried)}" if carried else ""), blockers=sorted(named)))
        elif missing:
            found.append(finding("BLOCKER-GONE", issue, "says it waits for " + ", ".join(f"#{n}" for n in missing)
                                 + ", which does not exist", blockers=missing))
        if "deferred" in labels:
            dates = DEFERRED_UNTIL.findall(words(issue))
            if not dates and DEFERRED_CONDITION.search(words(issue)):
                pass  # deferred until something happens, not until a day: nothing passes
            elif not dates:
                found.append(finding("DEFERRED-DUE", issue, "labelled deferred but no comment names a date or a condition; if "
                                     f"the owner's decision is already in the thread, add one comment: {DEFERRED_WORDING}"))
            elif dates[-1] <= now.date().isoformat():
                found.append(finding("DEFERRED-DUE", issue, f"deferred until {dates[-1]}, which has passed", until=dates[-1]))
        if "in-progress" in labels and now - parse(issue["updatedAt"]) > datetime.timedelta(days=1):
            number = int(issue["number"])
            at_work = [p for p in prs if str(p.get("headRefName", "")).startswith(f"{number}-")
                       or re.search(rf"#{number}\b", f"{p.get('title', '')}\n{p.get('body') or ''}")]
            if not at_work:
                found.append(finding("IN-PROGRESS-STALE", issue, "labelled in-progress, no open pull request, nothing new for a day"))
    found += circles(issues, waits)
    found += duplicates(issues)
    return found


def circles(issues: List[Dict[str, Any]], waits: Dict[int, Set[int]]) -> List[Finding]:
    """Open issues that wait for each other, each circle once."""
    by_number = {int(i["number"]): i for i in issues}
    seen: Set[Tuple[int, ...]] = set()
    found: List[Finding] = []

    def walk(start: int, at: int, path: List[int]) -> None:
        for nxt in sorted(waits.get(at, ())):
            if nxt == start:
                key = tuple(sorted(path))
                if key not in seen:
                    seen.add(key)
                    found.append(finding("BLOCKER-CIRCLE", by_number[min(path)], "wait for each other: "
                                         + " -> ".join(f"#{n}" for n in path + [start]), circle=path))
            elif nxt not in path and len(path) < 20:
                walk(start, nxt, path + [nxt])

    for number in sorted(waits):
        walk(number, number, [number])
    return found


def plain(title: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", title.lower()).split())


def duplicates(issues: List[Dict[str, Any]]) -> List[Finding]:
    """Pairs of open issues whose titles are nearly the same: candidates for a reader, not a verdict."""
    found: List[Finding] = []
    titles = [(issue, plain(issue.get("title", ""))) for issue in sorted(issues, key=lambda i: int(i["number"]))]
    for index, (first, a) in enumerate(titles):
        for second, b in titles[index + 1:]:
            if not a or not b:
                continue
            matcher = difflib.SequenceMatcher(None, a, b)
            if a == b or (matcher.quick_ratio() >= DUPLICATE_RATIO and matcher.ratio() >= DUPLICATE_RATIO):
                found.append(finding("DUPLICATE", second, f"title nearly the same as #{first['number']}: {first.get('title', '')}",
                                     other=first["number"]))
    return found


def finished(issues: List[Dict[str, Any]], merged: List[Dict[str, Any]]) -> List[Finding]:
    """Open issues that a merged pull request closes by name: GitHub closes them only from the default branch's own
    pull request body, so one named in a commit, or reopened, stays open."""
    by_number = {int(i["number"]): i for i in issues}
    closers: Dict[int, List[int]] = {}
    for pr in merged:
        for number in {int(n) for n in CLOSES.findall(f"{pr.get('title', '')}\n{pr.get('body') or ''}")}:
            if number in by_number:
                closers.setdefault(number, []).append(int(pr["number"]))
    return [finding("FINISHED", by_number[n], "still open, but merged " + ", ".join(f"PR #{p}" for p in sorted(pulls))
                    + " says it closes it", pulls=sorted(pulls)) for n, pulls in sorted(closers.items())]


def check_pulls(prs: List[Dict[str, Any]], opened: Set[int], state_of: Callable[[int], str], now: datetime.datetime,
                stale_days: int) -> List[Finding]:
    found: List[Finding] = []
    for pr in prs:
        closed = [n for n in sorted({int(n) for n in CLOSES.findall(f"{pr.get('title', '')}\n{pr.get('body') or ''}")})
                  if n not in opened and state_of(n) == "closed"]
        if closed:
            found.append(finding("PR-ISSUE-CLOSED", pr, "open, but " + ", ".join(f"#{n}" for n in closed) + " is already closed",
                                 issues=closed))
        idle = (now - parse(pr["updatedAt"])).days
        if idle >= stale_days:
            found.append(finding("PR-STALE", pr, f"{'a draft, ' if pr.get('isDraft') else ''}nothing new for {idle} days", days=idle))
        if pr.get("mergeable") == "CONFLICTING":
            found.append(finding("PR-CONFLICT", pr, f"conflicts with the default branch ({pr.get('headRefName', '')})"))
    return found


def check_branches(branches: List[Dict[str, Any]], default: str, now: datetime.datetime, stale_days: int) -> List[Finding]:
    """Remote branches no pull request ever came from and nobody has pushed to for `stale_days`. A branch whose pull
    request merged is branch_cleanup.py's to delete; one with an open pull request is that pull request's."""
    found: List[Finding] = []
    for branch in branches:
        name = str(branch.get("name", ""))
        if name == default or name in KEEP_BRANCHES or branch.get("pulls"):
            continue
        idle = (now - parse(branch["committed"])).days
        if idle >= stale_days:
            found.append({"kind": "BRANCH-NO-PR", "number": None, "title": name, "detail": f"no pull request, last commit {idle} days ago", "days": idle})
    return found


def check_labels(labels: List[str]) -> List[Finding]:
    found: List[Finding] = []
    groups: Dict[str, List[str]] = {}
    for name in labels:
        groups.setdefault(re.sub(r"[^a-z0-9]+", "", name.lower()), []).append(name)
    for twins in sorted(groups.values()):
        if len(twins) > 1:
            found.append({"kind": "LABEL-TWIN", "number": None, "title": twins[0], "detail": "the same label under two names: " + ", ".join(sorted(twins)), "labels": sorted(twins)})
    for name in KIT_LABELS:
        if name not in labels:
            found.append({"kind": "LABEL-MISSING", "number": None, "title": name, "detail": "the queue uses this label and the repository does not have it (--labels creates it)"})
    return found


def render(found: List[Finding]) -> str:
    lines = []
    for item in found:
        subject = f"#{item['number']} {item['title']}" if item.get("number") is not None else item["title"]
        lines.append(f"{item['kind']} {subject}: {item['detail']}")
    lines.append("clean" if not found else f"{len(found)} finding(s) for a reader to settle")
    return "\n".join(lines)


def read_branches(repo: str) -> Tuple[str, List[Dict[str, Any]]]:
    owner, _, name = repo.partition("/")
    query = ("query($o:String!,$n:String!){repository(owner:$o,name:$n){defaultBranchRef{name} "
             "refs(refPrefix:\"refs/heads/\",first:100){nodes{name target{... on Commit{committedDate}} "
             "associatedPullRequests(first:1){totalCount}}}}}")
    data = json.loads(gh(["api", "graphql", "-f", f"query={query}", "-f", f"o={owner}", "-f", f"n={name}"], None))["data"]["repository"]
    branches = [{"name": node["name"], "committed": node["target"]["committedDate"], "pulls": node["associatedPullRequests"]["totalCount"]}
                for node in data["refs"]["nodes"] if node.get("target", {}).get("committedDate")]
    return (data.get("defaultBranchRef") or {}).get("name", "main"), branches


def survey(repo: Optional[str], now: datetime.datetime, stale_days: int, create_labels: bool,
           say: Callable[[str], None] = print) -> Tuple[str, List[Finding]]:
    """Read the repository from GitHub and return its name and every finding."""
    repo = repo or json.loads(gh(["repo", "view", "--json", "nameWithOwner"], None))["nameWithOwner"]
    issues = json.loads(gh(["issue", "list", "--state", "open", "--limit", "1000", "--json", "number,title,body,labels,updatedAt,comments"], repo))
    prs = json.loads(gh(["pr", "list", "--state", "open", "--limit", "300", "--json", "number,title,body,isDraft,updatedAt,mergeable,headRefName"], repo))
    merged = json.loads(gh(["pr", "list", "--state", "merged", "--limit", "200", "--json", "number,title,body"], repo))
    labels = [str(label["name"]) for label in json.loads(gh(["label", "list", "--limit", "500", "--json", "name"], repo))]
    default, branches = read_branches(repo)
    states: Dict[int, str] = {}

    def state_of(number: int) -> str:
        if number not in states:
            try:
                done = subprocess.run(["gh", "api", f"repos/{repo}/issues/{number}", "-q", ".state"], capture_output=True, text=True,
                                      timeout=120)
            except subprocess.TimeoutExpired:
                raise GitHubError(f"gh api issues/{number}: no answer in 120 seconds") from None
            if done.returncode == 0:
                states[number] = done.stdout.strip() or "missing"
            elif "404" in done.stderr or "Not Found" in done.stderr:
                states[number] = "missing"
            else:
                raise GitHubError(f"gh api issues/{number}: {done.stderr.strip()}")
        return states[number]

    if create_labels:
        for name, (color, description) in KIT_LABELS.items():
            if name not in labels:
                gh(["label", "create", name, "--color", color, "--description", description], repo)
                labels.append(name)
                say(f"created the label {name}")
    found = check_issues(issues, prs, state_of, now)
    found += finished(issues, merged)
    found += check_pulls(prs, {int(i["number"]) for i in issues}, state_of, now, stale_days)
    found += check_branches(branches, default, now, stale_days)
    found += check_labels(labels)
    return repo, found


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--repo", help="OWNER/NAME (default: this repository)")
    parser.add_argument("--json", action="store_true", help="print the findings as JSON")
    parser.add_argument("--labels", action="store_true", help="create the kit's labels the repository lacks")
    parser.add_argument("--stale-days", type=int, default=14, help="how long a pull request or branch may sit untouched (14)")
    args = parser.parse_args(argv)
    if args.stale_days < 1:
        parser.error("--stale-days takes a positive number")
    if args.repo and not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
        parser.error("--repo takes OWNER/NAME")
    try:
        repo, found = survey(args.repo, datetime.datetime.now(datetime.timezone.utc), args.stale_days, args.labels,
                             say=(lambda line: print(line, file=sys.stderr)) if args.json else print)
    except (GitHubError, OSError, ValueError, KeyError) as error:
        print(f"GitHub could not be read, so nothing is reported: {error}", file=sys.stderr)
        return 2
    if args.json:
        counts: Dict[str, int] = {}
        for item in found:
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        print(json.dumps({"repo": repo, "counts": counts, "findings": found}, indent=2))
    else:
        print(render(found))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
