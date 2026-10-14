#!/usr/bin/env python3
"""Scan the project's lockfiles for known vulnerabilities, and turn each finding into an issue (UPD-2).

    python3 scripts/vuln-scan.py                  scan and print what it found (before a release, say)
    python3 scripts/vuln-scan.py --issues         also open an issue per finding and close the ones gone (weekly CI)
    python3 scripts/vuln-scan.py --report FILE    read osv-scanner's JSON output instead of scanning

The scan is osv-scanner (installed by `scripts/ci-tools.sh`) in offline mode: it downloads the public vulnerability
database and compares the lockfiles here, so the project's package list never leaves the machine.

One issue per vulnerability and package version, labelled `security`, with its due date from the standard's time
limits (UPD-3) by the record's `exposure`: public apps get the shorter limits. Only issues this script opened (they
carry a marker line) are ever changed by it: a finding no longer found is closed with a comment; a closed one found
again is reopened. Findings never fail the run: they become issues. A scan that cannot run fails it.

Standard library only; Python 3.9. Owned by the template: change it there, not in a project.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import standard_check as sc  # noqa: E402  # the record's exposure, read the way the checker reads it

LABEL = "security"
MARKER = "<!-- vuln-scan: {key} -->"
MAX_NEW = 25  # more than this in one run is a sign of a broken scan, not 25 real findings to file at once
# Days to fix, from UPD-3: (public app, home-network app). Low waits for the next release.
LIMITS = {"critical": (7, 30), "high": (14, 30), "medium": (30, 90)}

Api = Callable[[str, str, Optional[Dict[str, Any]]], Any]


def severity(score: str) -> str:
    """The class of a CVSS base score; no score counts as medium (a person reads it)."""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "medium"
    return "critical" if value >= 9 else "high" if value >= 7 else "medium" if value >= 4 else "low"


def fixed_in(vulns: List[Dict[str, Any]], ids: List[str], name: str, ecosystem: str) -> List[str]:
    found = []
    for vuln in vulns:
        if vuln.get("id") not in ids:
            continue
        for affected in vuln.get("affected", []):
            package = affected.get("package", {})
            if package.get("name") != name or package.get("ecosystem") != ecosystem:
                continue
            for span in affected.get("ranges", []):
                found += [e["fixed"] for e in span.get("events", []) if "fixed" in e and e["fixed"] not in found]
    return found


def findings(report: Dict[str, Any], root: Path) -> List[Dict[str, Any]]:
    """One finding per vulnerability group and package version, in a stable order."""
    out = []
    for result in report.get("results", []):
        path = result.get("source", {}).get("path", "")
        try:
            path = str(Path(path).resolve().relative_to(root.resolve()))
        except ValueError:
            pass
        for item in result.get("packages", []):
            package = item.get("package", {})
            for group in item.get("groups", []):
                ids = sorted(group.get("ids", []))
                if not ids:
                    continue
                name, version, ecosystem = package.get("name", "?"), package.get("version", "?"), package.get("ecosystem", "?")
                vulns = item.get("vulnerabilities", [])
                summary = next((v.get("summary") for v in vulns if v.get("id") in ids and v.get("summary")), "")
                out.append({
                    "key": f"{ecosystem}/{name}@{version}/{ids[0]}",
                    "id": ids[0], "aliases": sorted(set(group.get("aliases", [])) - {ids[0]}),
                    "package": name, "version": version, "ecosystem": ecosystem, "lockfile": path,
                    "severity": severity(group.get("max_severity", "")), "score": group.get("max_severity", ""),
                    "summary": summary, "fixed": fixed_in(vulns, ids, name, ecosystem),
                })
    return sorted({f["key"]: f for f in out}.values(), key=lambda f: f["key"])


def due(finding: Dict[str, Any], public: bool, today: datetime.date) -> str:
    if not finding["fixed"]:
        return "no fix exists yet: keep this open with what was done instead (UPD-3)"
    if finding["severity"] == "low":
        return "the next release (UPD-3)"
    days = LIMITS[finding["severity"]][0 if public else 1]
    return f"{(today + datetime.timedelta(days=days)).isoformat()} ({days} days from the scan that found it, UPD-3)"


def issue_title(finding: Dict[str, Any]) -> str:
    return f"Security: {finding['package']} {finding['version']} ({finding['id']}, {finding['severity']})"


def issue_body(finding: Dict[str, Any], public: bool, today: datetime.date) -> str:
    fixed = ", ".join(finding["fixed"]) or "none yet"
    also = f" (also {', '.join(finding['aliases'])})" if finding["aliases"] else ""
    return (f"{finding['summary'] or 'A known vulnerability.'}\n\n"
            f"| | |\n| --- | --- |\n"
            f"| Package | `{finding['package']}` {finding['version']} ({finding['ecosystem']}), in `{finding['lockfile']}` |\n"
            f"| Advisory | [{finding['id']}](https://osv.dev/vulnerability/{finding['id']}){also} |\n"
            f"| Severity | {finding['severity']} (CVSS {finding['score'] or 'not given'}) |\n"
            f"| Fixed in | {fixed} |\n"
            f"| Due | {due(finding, public, today)} |\n\n"
            "Update within the version range the manifest allows where that is enough; a security fix goes through "
            "the same checks as any change (UPD-5) and waits the usual 24 hours unless the owner says otherwise "
            "(UPD-4). Found by the weekly scan (`scripts/vuln-scan.py`); it closes this issue itself once the "
            "lockfiles no longer have it.\n\n" + MARKER.format(key=finding["key"]) + "\n")


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


def scanner_issues(api: Api, repo: str) -> Dict[str, Dict[str, Any]]:
    """This script's issues, open and closed, by finding key."""
    found: Dict[str, Dict[str, Any]] = {}
    page = 1
    while True:
        batch = api("GET", f"/repos/{repo}/issues?state=all&labels={LABEL}&per_page=100&page={page}", None) or []
        for issue in batch:
            body = issue.get("body") or ""
            start = body.find("<!-- vuln-scan: ")
            if start >= 0 and "pull_request" not in issue:
                found[body[start + 16:body.find(" -->", start)]] = issue
        if len(batch) < 100:
            return found
        page += 1


def sync_issues(api: Api, repo: str, items: List[Dict[str, Any]], public: bool, today: datetime.date) -> List[str]:
    """Open, reopen and close this script's issues to match the findings. Returns what it did."""
    done: List[str] = []
    try:
        api("POST", f"/repos/{repo}/labels", {"name": LABEL, "color": "b60205", "description": "A known vulnerability (UPD-2)"})
    except urllib.error.HTTPError as error:
        if error.code != 422:  # 422: the label is already there
            raise
    existing = scanner_issues(api, repo)
    current = {f["key"] for f in items}
    opened = 0
    for finding in items:
        issue = existing.get(finding["key"])
        if issue is None:
            if opened >= MAX_NEW:
                done.append(f"not filed (more than {MAX_NEW} new in one run): {finding['key']}")
                continue
            made = api("POST", f"/repos/{repo}/issues",
                       {"title": issue_title(finding), "body": issue_body(finding, public, today), "labels": [LABEL]})
            opened += 1
            done.append(f"opened #{made['number']}: {issue_title(finding)}")
        elif issue["state"] == "closed":
            api("PATCH", f"/repos/{repo}/issues/{issue['number']}", {"state": "open"})
            api("POST", f"/repos/{repo}/issues/{issue['number']}/comments",
                {"body": f"Found again by the scan of {today.isoformat()}."})
            done.append(f"reopened #{issue['number']}: {issue_title(finding)}")
    for key, issue in existing.items():
        if issue["state"] == "open" and key not in current:
            api("POST", f"/repos/{repo}/issues/{issue['number']}/comments",
                {"body": f"No longer found by the scan of {today.isoformat()}: the lockfiles no longer have it."})
            api("PATCH", f"/repos/{repo}/issues/{issue['number']}", {"state": "closed", "state_reason": "completed"})
            done.append(f"closed #{issue['number']}: {issue['title']}")
    return done


def scan(root: Path) -> Dict[str, Any]:
    """Run osv-scanner offline. Exit 0 is clean, 1 is findings, 128 is no lockfile; anything else is a failure."""
    output = Path(tempfile.mkdtemp(prefix="vuln-scan-")) / "report.json"
    try:
        done = subprocess.run(
            ["osv-scanner", "scan", "source", "--recursive", "--offline-vulnerabilities", "--download-offline-databases",
             "--data-source", "native", "--allow-no-lockfiles", "--format", "json", "--output-file", str(output), str(root)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if done.returncode not in (0, 1) or not output.is_file():
            sys.exit(f"vuln-scan: osv-scanner could not scan (exit {done.returncode}):\n{done.stdout[-2000:]}")
        return json.loads(output.read_text(encoding="utf-8") or "{}")
    finally:
        subprocess.run(["rm", "-rf", str(output.parent)])


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Scan the lockfiles for known vulnerabilities (UPD-2).")
    parser.add_argument("--repo", default=".", help="the checkout to scan")
    parser.add_argument("--report", help="read osv-scanner's JSON output from this file instead of scanning")
    parser.add_argument("--issues", action="store_true", help="open, reopen and close issues to match (needs GITHUB_TOKEN)")
    args = parser.parse_args(argv)
    root = Path(args.repo)
    report = json.loads(Path(args.report).read_text(encoding="utf-8")) if args.report else scan(root)
    items = findings(report, root)
    public = sc.Repo(root).exposure == "public"
    today = datetime.date.today()
    for finding in items:
        print(f"{finding['severity']:8} {finding['package']} {finding['version']} {finding['id']} "
              f"(fixed in {', '.join(finding['fixed']) or 'nothing yet'}) in {finding['lockfile']}")
    print(f"{len(items)} known vulnerabilities in the lockfiles.")
    if args.issues:
        repo = os.environ.get("GITHUB_REPOSITORY")
        if not repo or not os.environ.get("GITHUB_TOKEN"):
            sys.exit("vuln-scan: --issues needs GITHUB_REPOSITORY and GITHUB_TOKEN")
        for line in sync_issues(github_api, repo, items, public, today):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
