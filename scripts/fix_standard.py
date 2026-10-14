#!/usr/bin/env python3
"""Fix the findings of the standard that need no judgement, in one command and no agent time.

    python3 scripts/fix_standard.py                  show what it would change
    python3 scripts/fix_standard.py --write          change the files
    python3 scripts/fix_standard.py --repo PATH      another checkout

What it fixes, each only when the checker finds it missing, and always by adding, never by rewriting:
- AGENT-1: `CLAUDE.md` names the check command (a short "Checks" section at the end).
- REPO-2: `.gitignore` ignores the personal agent files (`.claude/settings.local.json`, `CLAUDE.local.md`).
- TMP-5: `.gitignore` ignores what the kit's checks write (`.tools/`, `__pycache__/`, and with `e2e/` the browser
  tests' output), so a run leaves the checkout clean.
- REPO-3: `.gitattributes` sets LF line endings (`* text=auto eol=lf`).
- The kit's own Python scripts are left out of the project's ruff and coverage settings in `pyproject.toml`, where
  those sections exist (the kit tests them; a project's gates otherwise fail on them).
- CI-3, CI-4, CI-5, CI-6, CI-7, CI-8: the workflows, through `fix_workflows.py`.

Then run the check command and read the diff. Owned by the template: change it there, not in a project.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fix_workflows  # noqa: E402
import standard_check as sc  # noqa: E402

Fix = Tuple[str, str, str]  # (file, its new text, what was done)


def claude_md(repo: sc.Repo, root: Path) -> Optional[Fix]:
    command = repo.check_command
    text = repo.read("CLAUDE.md") if repo.has("CLAUDE.md") else "# CLAUDE.md\n"
    if command in text:
        return None
    section = (f"\n## Checks\n\nRun `{command}` before committing. Why: CI runs the same command, so a change that "
               "passes it here passes there.\n")
    return "CLAUDE.md", text.rstrip("\n") + "\n" + section, f"AGENT-1: CLAUDE.md names `{command}`"


def gitignore(repo: sc.Repo, root: Path) -> Optional[Fix]:
    text = repo.read(".gitignore") if repo.has(".gitignore") else ""
    present = {line.strip().lstrip("/") for line in text.splitlines()}
    groups = [("Personal agent files, never shared (REPO-2)", "REPO-2", [".claude/settings.local.json", "CLAUDE.local.md"]),
              ("What the kit's checks write, so they leave the checkout clean (TMP-5)", "TMP-5",
               [".tools/", "__pycache__/"] + (["test-results/", "browser-report.json", "playwright-report/"]
                                              if any(name.startswith("e2e/") for name in repo.files) else []))]
    added, done = "", []
    for heading, rule, lines in groups:
        missing = [line for line in lines if line not in present and line.rstrip("/") not in present]
        if missing:
            added += f"\n# {heading}\n" + "\n".join(missing) + "\n"
            done.append(f"{rule}: " + ", ".join(missing))
    if not added:
        return None
    return ".gitignore", (text.rstrip("\n") + "\n" if text else "") + added, "; ".join(done)


def line_endings(repo: sc.Repo, root: Path) -> Optional[Fix]:
    if sc.repo_3(repo).status == "pass":
        return None
    text = repo.read(".gitattributes") if repo.has(".gitattributes") else ""
    return ".gitattributes", (text.rstrip("\n") + "\n" if text else "") + "* text=auto eol=lf\n", "REPO-3: LF endings"


def _add_to_list(text: str, section: str, key: str, items: List[str]) -> str:
    """Add items to `key = [...]` in a TOML section, or the key itself under the section header."""
    header = re.search(rf"(?m)^\[{re.escape(section)}\]\s*$", text)
    if not header:
        return text
    end = re.search(r"(?m)^\[", text[header.end():])
    body_end = header.end() + (end.start() if end else len(text) - header.end())
    body = text[header.end():body_end]
    existing = re.search(rf"(?ms)^{re.escape(key)}\s*=\s*\[(.*?)\]", body)
    missing = [item for item in items if f'"{item}"' not in (existing.group(1) if existing else "")]
    if not missing:
        return text
    quoted = ", ".join(f'"{item}"' for item in missing)
    if existing:
        inside = existing.group(1).rstrip()
        joined = f"{inside}, {quoted}" if inside.strip() else quoted
        body = body[:existing.start(1)] + joined + body[existing.end(1):]
    else:
        body = f"\n{key} = [{quoted}]  # the project kit's files, tested in the template" + body
    return text[:header.end()] + body + text[body_end:]


def kit_exclusions(repo: sc.Repo, root: Path) -> Optional[Fix]:
    if not repo.has("pyproject.toml"):
        return None
    scripts = [name for name in sc.TEMPLATE_OWNED if repo.has(name)]
    text = repo.read("pyproject.toml")
    new = _add_to_list(text, "tool.ruff", "extend-exclude", scripts)
    new = _add_to_list(new, "tool.coverage.run", "omit", scripts)
    if new == text:
        return None
    return "pyproject.toml", new, "kit scripts left out of the project's ruff and coverage settings"


FIXERS: List[Callable[[sc.Repo, Path], Optional[Fix]]] = [claude_md, gitignore, line_endings, kit_exclusions]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo", default=".", help="the checkout to fix (default: this one)")
    parser.add_argument("--write", action="store_true", help="change the files (default: only show)")
    parser.add_argument("--keep-runners", action="store_true", help="never change runs-on (a public repository)")
    args = parser.parse_args(argv)
    root = Path(args.repo).resolve()
    repo = sc.Repo(root)
    for fixer in FIXERS:
        fix = fixer(repo, root)
        if fix:
            name, text, note = fix
            print(f"{name}: {note}")
            if args.write:
                (root / name).write_text(text, encoding="utf-8")
    return fix_workflows.main(["--repo", str(root)] + (["--write"] if args.write else [])
                              + (["--keep-runners"] if args.keep_runners else []))


if __name__ == "__main__":
    sys.exit(main())
