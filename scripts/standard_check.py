#!/usr/bin/env python3
"""Score a repository against the project standard.

The rules are written down in standard/STANDARD.md of the template repository
(claude-repo-template). This file is the machine's reading of them: one entry
per rule in RULES, with the same ID, scope, level and kind of check.

Usage:
    standard_check.py [--repo PATH] [--format text|json|markdown]
                      [--baseline FILE] [--write-baseline] [--report-only]
    standard_check.py [--repo PATH] --lane-guard RANGE

Exit status: 0 when no "must" rule failed, 1 when one did (0 with
--report-only), 2 for a usage error.

Standard library only, Python 3.9 or newer, so it runs on any runner and can
be copied into a project unchanged.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

STANDARD_VERSION = "1.0"
RECORD_NAME = "standard.json"
BASELINE_NAME = "standard-baseline.json"
MAX_READ_BYTES = 400_000
HISTORY_DEPTH = 200
TYPED_LANGUAGE_FILES = 5  # fewer files than this in a language do not call for a type checker

DEFAULT_LANES: Dict[str, List[str]] = {
    "builder": [
        "src/routes/**",
        "src/components/**",
        "src/styles.css",
        "src/index.css",
        "public/**",
    ],
    "builder_tests": ["e2e/**", "tests/e2e/**"],
    "server": ["src/routes/api/**", "src/routes/**/*.ts"],
    "generated": [
        "src/routeTree.gen.ts",
        ".lovable/**",
        "bun.lock",
        "src/integrations/supabase/types.ts",
    ],
    "shared": ["e2e/kit/**"],
    # Claude's, but the owner may tell the UI builder to write here by typing
    # an override into the builder's own prompt. The guard reports a commit
    # here and does not fail on it: it cannot see what the owner typed.
    "override": ["src/lib/**", "src/hooks/**", "src/api/**", "package.json"],
}
DEFAULT_BUILDER_AUTHORS = ["gpt-engineer-app[bot]", "lovable-dev[bot]", "Lovable"]

SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs")
# Files the template puts into a project. They are not the project's own code.
TEMPLATE_OWNED = ("scripts/standard_check.py", "scripts/ci-verdict.py", "scripts/pr_flow.py", "scripts/fix_workflows.py", "scripts/fix_standard.py",
                  "scripts/vuln-scan.py")
# The owner's categories (2026-10-13). One per app; it sets the defaults for `exposure` and `accounts`, and the
# last two are out of scope for this standard.
CATEGORIES: Dict[str, Dict[str, object]] = {
    "public-business": {"exposure": "public", "accounts": True, "in_scope": True},
    "public-brochure": {"exposure": "public", "accounts": False, "in_scope": True},
    "private-lan": {"exposure": "lan", "accounts": True, "in_scope": True},
    "tools": {"exposure": "none", "accounts": False, "in_scope": True},
    "dataiku": {"exposure": "none", "accounts": False, "in_scope": False},
    "legacy": {"exposure": "none", "accounts": False, "in_scope": False},
}
CHECKER_NAME = "standard_check.py"  # never followed: its own patterns would satisfy every search
SCRIPT_SUFFIXES = (".sh", ".py", ".mjs", ".js", ".ts")
JS_LOCKFILES = {
    "bun.lock": "bun",
    "bun.lockb": "bun",
    "package-lock.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
}
PY_LOCKFILES = ("uv.lock", "poetry.lock", "pdm.lock")
FROZEN_INSTALL = re.compile(
    r"--frozen-lockfile|\bnpm ci\b|\bbun ci\b|--locked\b|--frozen\b|--immutable\b|--require-hashes\b"
)
LINT_TOOLS = re.compile(r"\b(eslint|ruff|prettier|biome|shellcheck|flake8|pylint)\b|\blint\b")
SECRET_SCANNERS = re.compile(r"gitleaks|trufflehog|detect-secrets|secretlint", re.I)
REMOTE_ASSET = re.compile(
    r"fonts\.googleapis\.com|fonts\.gstatic\.com|cdn\.jsdelivr\.net|unpkg\.com"
    r"|cdnjs\.cloudflare\.com|cdn\.tailwindcss\.com|cdn\.gpteng\.co"
    r"|@import\s+(?:url\(\s*)?['\"]?https?:"
)
NETWORK_CALL = re.compile(
    r"\bfetch\s*\(|,\s*fetch\s*\)|\bXMLHttpRequest\b|\baxios\b"
    r"|\bnew\s+WebSocket\b|\bnew\s+EventSource\b|\bsendBeacon\b"
)
RAW_CONTROL = re.compile(r"<(?:motion\.)?(button|input|select|textarea)\b")
# A colour, not an issue number: it follows a quote, a bracket, a comma or a colon.
HEX_COLOUR = re.compile(
    r"(?:[\"'`\[,]|:\s*)#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![0-9a-zA-Z_-])"
)
# A picture with no `loading` attribute: it loads at once, wherever it is on the page (MOBILE-10). `=>` inside an
# attribute (an arrow function) does not end the tag.
EAGER_IMAGE = re.compile(r"<img\b(?!(?:=>|[^>])*\bloading=)(?:=>|[^>])*>")
VIEWPORT_HEIGHT = re.compile(r"(?<![a-zA-Z0-9.])\d+vh\b|(?<![a-zA-Z0-9-])(?:min-|max-)?h-screen\b")
TEST_NAME = re.compile(r"\.(test|spec)\.[a-z]+\Z|(^|/)test_[^/]+\.py\Z|_test\.(py|go)\Z")
TEST_FOLDER = re.compile(r"(^|/)(tests?|e2e|__tests__)/")
TEST_SUPPORT = re.compile(r"(^|/)(setup|conftest|__init__|fixtures?|helpers?)\.[a-z]+\Z")


# --------------------------------------------------------------------------
# Small helpers


def glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """Translate a lane pattern: `**` crosses folders, `*` and `?` do not.

    A pattern with no wildcard names a file or a folder: `src/lib`, `src/lib/`
    and `/src/lib` all match the folder's whole content.
    """
    pattern = pattern.strip()
    if pattern.startswith("./"):
        pattern = pattern[2:]
    pattern = pattern.lstrip("/")
    if pattern.endswith("/"):
        pattern += "**"
    if not any(mark in pattern for mark in "*?"):
        return re.compile(re.escape(pattern) + r"(?:/.*)?\Z")
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(glob_to_regex(p).match(path) for p in patterns)


def strip_comments(text: str) -> str:
    """Drop whole-line `#` comments so a word in a comment proves nothing."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def is_test_file(name: str) -> bool:
    if TEST_NAME.search(name):
        return True
    return bool(TEST_FOLDER.search(name)) and name.endswith(SOURCE_SUFFIXES) and not TEST_SUPPORT.search(name)


def git(root: Path, *args: str) -> Optional[str]:
    """Run git in `root`; None when git is missing or the command fails."""
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "-c", "core.quotepath=off", *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    if done.returncode != 0:
        return None
    return done.stdout


# --------------------------------------------------------------------------
# A small reader for the parts of a workflow file the rules look at. The
# standard library has no YAML parser, so this reads only what is needed:
# block and flow mappings, block and flow lists, plain and quoted scalars.


def without_comment(line: str) -> str:
    return re.sub(r"(^|\s)#.*$", "", line).rstrip()


class _Flow:
    """Reads one value written on a single line: `[a, b]`, `{a: [b]}` or a scalar."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.at = 0

    def _skip_spaces(self) -> None:
        while self.at < len(self.text) and self.text[self.at] in " \t":
            self.at += 1

    def _peek(self) -> str:
        self._skip_spaces()
        return self.text[self.at] if self.at < len(self.text) else ""

    def value(self, top: bool = False) -> Any:
        first = self._peek()
        if first == "[":
            self.at += 1
            items = []
            while self._peek() not in ("", "]"):
                items.append(self.value())
                if self._peek() == ",":
                    self.at += 1
            self.at += 1
            return items
        if first == "{":
            self.at += 1
            mapping: Dict[str, Any] = {}
            while self._peek() not in ("", "}"):
                key = self._scalar(":,}")
                if self._peek() == ":":
                    self.at += 1
                    mapping[key] = self.value()
                else:
                    mapping[key] = None
                if self._peek() == ",":
                    self.at += 1
            self.at += 1
            return mapping
        return self._scalar("" if top else ",]}")

    def _scalar(self, stop: str) -> str:
        first = self._peek()
        if first and first in "'\"":
            close = self.text.find(first, self.at + 1)
            close = len(self.text) if close == -1 else close
            word = self.text[self.at + 1 : close]
            self.at = close + 1
            return word
        begin = self.at
        while self.at < len(self.text) and self.text[self.at] not in stop:
            self.at += 1
        return self.text[begin : self.at].strip()


def parse_inline(text: str) -> Any:
    return _Flow(text).value(top=True) if text.strip() else None


def parse_block(lines: List[Tuple[int, str]]) -> Any:
    """Read indented lines, given as (indent, text) with comments and blanks removed."""
    if not lines:
        return None
    base = lines[0][0]
    if lines[0][1].startswith("-"):
        return [parse_inline(text[1:]) for indent, text in lines if indent == base and text.startswith("-")]
    mapping: Dict[str, Any] = {}
    index = 0
    while index < len(lines):
        indent, text = lines[index]
        index += 1
        if indent != base:
            continue
        key, _, rest = text.partition(":")
        children = []
        while index < len(lines) and (
            lines[index][0] > base or (not rest.strip() and lines[index][0] == base and lines[index][1].startswith("-"))
        ):
            children.append(lines[index])
            index += 1
        mapping[key.strip(" '\"")] = parse_inline(rest) if rest.strip() else parse_block(children)
    return mapping


def value_at(lines: List[str], index: int, inline: str) -> Any:
    """The value of the key on line `index`: what follows the colon, or the block beneath it."""
    inline = without_comment(inline).strip()
    if inline:
        return parse_inline(inline)
    key_indent = len(lines[index]) - len(lines[index].lstrip())
    block: List[Tuple[int, str]] = []
    for following in lines[index + 1 :]:
        text = without_comment(following)
        if not text.strip():
            continue
        indent = len(text) - len(text.lstrip())
        if indent < key_indent or (indent == key_indent and not text.lstrip().startswith("-")):
            break
        block.append((indent, text.strip()))
    return parse_block(block)


def _names(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return []


def workflow_triggers(text: str) -> Dict[str, bool]:
    """Return {event: restricted} for a workflow file.

    `restricted` matters for `push`: True when it runs only for named
    branches (no wildcard) or only for tags.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        found = re.match(r"""^(?:on|"on"|'on'|true)\s*:(.*)$""", line)
        if not found:
            continue
        value = value_at(lines, index, found.group(1))
        if isinstance(value, dict):
            return {str(name): _restricted(settings) for name, settings in value.items()}
        return {name: False for name in _names(value)}
    return {}


def _restricted(settings: Any) -> bool:
    if not isinstance(settings, dict):
        return False
    if "branches" in settings:
        branches = _names(settings["branches"])
        return bool(branches) and not any("*" in branch for branch in branches)
    if "branches-ignore" in settings:
        return False
    return "tags" in settings or "tags-ignore" in settings


def runner_label_sets(text: str) -> List[Optional[frozenset[str]]]:
    """Label set of every `runs-on:` in a workflow.

    None stands for a runner the reader cannot work out: an expression other
    than a plain `matrix` value, or a runner group with no labels.
    """
    found: List[Optional[frozenset[str]]] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        hit = re.match(r"^\s*runs-on\s*:(.*)$", line)
        if hit:
            found.extend(_runner_labels(value_at(lines, index, hit.group(1)), lines))
    return found


def _runner_labels(value: Any, lines: List[str]) -> List[Optional[frozenset[str]]]:
    if isinstance(value, dict):
        labels = _names(value.get("labels"))
        return [frozenset(labels)] if labels else [None]
    if isinstance(value, list):
        names = _names(value)
        if not any("${{" in name for name in names):
            return [frozenset(names)]
        # A list with matrix values in it, such as [self-hosted, "${{ matrix.arch }}"]:
        # one label set for every value the matrix gives.
        sets: List[Optional[frozenset[str]]] = [frozenset(name for name in names if "${{" not in name)]
        for name in names:
            if "${{" not in name:
                continue
            choices = _matrix_choices(name, lines)
            if choices is None or any(not isinstance(choice, str) for choice in choices):
                return [None]
            sets = [None if labels is None else labels | {choice} for labels in sets for choice in choices]
        return sets
    if not isinstance(value, str) or not value:
        return [None]
    if "${{" not in value:
        return [frozenset([value])]
    choices = _matrix_choices(value, lines)
    if not choices:
        return [None]
    found: List[Optional[frozenset[str]]] = []
    for choice in choices:
        names = _names(choice)
        found.append(frozenset(names) if names and not any("${{" in name for name in names) else None)
    return found


def _matrix_choices(value: str, lines: List[str]) -> Optional[List[Any]]:
    """Every value a `${{ matrix.NAME }}` expression can take, or None when it is some other expression."""
    matrix = re.match(r"\$\{\{\s*matrix\.([\w-]+)\s*\}\}\Z", value)
    if not matrix:
        return None
    found: List[Any] = []
    for index, line in enumerate(lines):
        hit = re.match(r"^\s*(?:-\s*)?" + re.escape(matrix.group(1)) + r"\s*:(.*)$", line)
        if hit:
            choices = value_at(lines, index, hit.group(1))
            found.extend(choices if isinstance(choices, list) else [choices])
    return found or None


def jobs_without_timeout(text: str) -> int:
    """Count jobs that name a runner and set no `timeout-minutes` of their own."""
    lines = [without_comment(line) for line in text.splitlines()]
    missing = 0
    for index, line in enumerate(lines):
        if not re.match(r"^\s*runs-on\s*:", line):
            continue
        indent = len(line) - len(line.lstrip())
        job: List[str] = []
        for step in (-1, 1):
            cursor = index + step
            while 0 <= cursor < len(lines):
                other = lines[cursor]
                if other.strip():
                    if len(other) - len(other.lstrip()) < indent:
                        break
                    job.append(other)
                cursor += step
        if not any(re.match(r"^\s{%d}timeout-minutes\s*:" % indent, other) for other in job):
            missing += 1
    return missing


# --------------------------------------------------------------------------
# What the checker knows about one repository


class Repo:
    def __init__(self, root: Path, record_file: Optional[Path] = None) -> None:
        self.root = root
        self.files = self._list_files()
        self._file_set = set(self.files)
        self.record: Dict[str, Any] = {}
        # record_error is about the repository's own standard.json, even when
        # the facts are read from a record kept somewhere else.
        self.record_error: Optional[str] = None
        self._load_record()
        if record_file is not None:
            loaded = json.loads(record_file.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("the record is not a JSON object")
            self.record = loaded
        ignored = self.record.get("ignore")
        if isinstance(ignored, list) and ignored:
            # Files the project ships or vendors and does not itself run (a template's files, fixtures).
            patterns = [str(pattern) for pattern in ignored]
            self.files = [name for name in self.files if not matches_any(name, patterns)]
            self._file_set = set(self.files)
        self._text_cache: Dict[str, str] = {}
        self._check_text: Optional[str] = None

    def _list_files(self) -> List[str]:
        listed = git(self.root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        if listed is not None:
            return sorted(name for name in listed.split("\0") if name)
        skip = {".git", "node_modules", ".venv", "__pycache__"}
        names = []
        for path in self.root.rglob("*"):
            if path.is_file() and not skip.intersection(path.relative_to(self.root).parts):
                names.append(path.relative_to(self.root).as_posix())
        return sorted(names)

    def _load_record(self) -> None:
        if RECORD_NAME not in self._file_set and not (self.root / RECORD_NAME).is_file():
            self.record_error = f"{RECORD_NAME} is missing"
            return
        try:
            loaded = json.loads((self.root / RECORD_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            self.record_error = f"{RECORD_NAME} cannot be read: {error}"
            return
        if not isinstance(loaded, dict):
            self.record_error = f"{RECORD_NAME} is not an object"
            return
        self.record = loaded

    # files ---------------------------------------------------------------

    def has(self, path: str) -> bool:
        return path in self._file_set

    def first(self, *paths: str) -> Optional[str]:
        lowered = {name.lower(): name for name in self.files}
        for path in paths:
            if path.lower() in lowered:
                return lowered[path.lower()]
        return None

    def under(self, prefix: str) -> List[str]:
        return [name for name in self.files if name.startswith(prefix)]

    def read(self, path: str) -> str:
        if path not in self._text_cache:
            try:
                with open(self.root / path, "rb") as handle:
                    data = handle.read(MAX_READ_BYTES)
                self._text_cache[path] = data.decode("utf-8", errors="replace")
            except OSError:
                self._text_cache[path] = ""
        return self._text_cache[path]

    def workflows(self) -> Dict[str, str]:
        return {
            name: self.read(name)
            for name in self.files
            if re.match(r"\.github/workflows/[^/]+\.ya?ml\Z", name)
        }

    # the record, with defaults ---------------------------------------------

    @property
    def layers(self) -> List[str]:
        layers = self.record.get("layers")
        if isinstance(layers, list) and layers:
            return [str(layer) for layer in layers]
        return ["base", "web"] if self.looks_like_web() else ["base"]

    @property
    def is_web(self) -> bool:
        return "web" in self.layers

    def looks_like_web(self) -> bool:
        """True when the files say a front end exists, whatever the record says."""
        answered_yes = re.search(r"(?m)^has_front_end:\s*true\s*$", self.read(".copier-answers.yml"))
        return self.has(".lovable/project.json") or self.has("components.json") or bool(answered_yes)

    @property
    def back_end(self) -> str:
        value = self.record.get("back_end")
        if value in ("none", "service", "lovable-cloud"):
            return str(value)
        if not self.is_web:
            return "none"
        return "lovable-cloud" if self.under("supabase/") else "service"

    @property
    def deployed(self) -> bool:
        return self.record.get("deployed") is True

    def flag(self, key: str, default: bool = False) -> bool:
        """A true-or-false fact from the record, else the category's default, else `default`."""
        value = self.record.get(key)
        if isinstance(value, bool):
            return value
        implied = self.category_defaults.get(key)
        return implied if isinstance(implied, bool) else default

    @property
    def category_defaults(self) -> Dict[str, object]:
        return CATEGORIES.get(str(self.record.get("category")), {})

    @property
    def exposure(self) -> Optional[str]:
        """`exposure` from the record, else from the category."""
        value = self.record.get("exposure", self.category_defaults.get("exposure"))
        return value if value in ("public", "lan", "none") else None

    @property
    def in_scope(self) -> bool:
        return self.category_defaults.get("in_scope", True) is not False

    @property
    def check_command(self) -> str:
        value = self.record.get("check_command")
        if isinstance(value, str) and value.strip():
            return value
        if not self.has("scripts/check.sh"):
            manager = "bun" if self.has("bun.lock") or self.has("bun.lockb") else "npm"
            for name in ("ci", "check"):
                if name in self.package_scripts():
                    return f"{manager} run {name}"
        return "scripts/check.sh"

    @property
    def ci_runners(self) -> List[frozenset[str]]:
        value = self.record.get("ci_runners")
        if not (isinstance(value, list) and value):
            value = ["self-hosted"]
        return [
            frozenset(part.strip(" '\"") for part in str(entry).strip("[]").split(",") if part.strip())
            for entry in value
        ]

    @property
    def lanes(self) -> Dict[str, List[str]]:
        lanes = {name: list(paths) for name, paths in DEFAULT_LANES.items()}
        recorded = self.record.get("lanes")
        if isinstance(recorded, dict):
            for name, paths in recorded.items():
                if isinstance(paths, list):
                    lanes[name] = [str(path) for path in paths]
        return lanes

    @property
    def builder_authors(self) -> List[str]:
        value = self.record.get("builder_authors")
        if isinstance(value, list) and value:
            return [str(name) for name in value]
        return DEFAULT_BUILDER_AUTHORS

    def path_setting(self, key: str) -> Optional[str]:
        paths = self.record.get("paths")
        if isinstance(paths, dict) and isinstance(paths.get(key), str) and paths[key]:
            return str(paths[key])
        return None

    # derived ---------------------------------------------------------------

    def builder_lane(self, path: str) -> str:
        """`own`: the UI builder's. `override`: only on the owner's word. `closed`: never."""
        lanes = self.lanes
        if matches_any(path, lanes.get("shared", []) + lanes.get("server", [])):
            return "closed"
        if matches_any(path, lanes.get("builder", []) + lanes.get("builder_tests", []) + lanes.get("generated", [])):
            return "own"
        return "override" if matches_any(path, lanes.get("override", [])) else "closed"

    def builder_may_write(self, path: str) -> bool:
        return self.builder_lane(path) == "own"

    def screen_files(self, suffixes: Tuple[str, ...]) -> List[str]:
        """The UI builder's own source files: its lane, minus the library and tests."""
        lanes = self.lanes
        return [
            name
            for name in self.files
            if name.endswith(suffixes)
            and matches_any(name, lanes.get("builder", []))
            and not matches_any(name, lanes.get("shared", []) + lanes.get("server", []))
            and not name.startswith("src/components/ui/")
            and not name.startswith("public/")
            and not re.search(r"\.(test|spec)\.", name)
        ]

    def source_files(self) -> List[str]:
        """The project's own code: not its tests, and not what the template put there."""
        return [
            name
            for name in self.files
            if name.endswith(SOURCE_SUFFIXES)
            and name not in TEMPLATE_OWNED
            and not TEST_FOLDER.search(name)
            and not TEST_NAME.search(name)
            and not name.startswith(".")
            and "/." not in name
            and "node_modules/" not in name
        ]

    def check_text(self) -> str:
        """What CI runs: the non-comment text of every workflow and of the check
        command, following the scripts and package.json scripts they call."""
        if self._check_text is not None:
            return self._check_text
        scripts = self.package_scripts()
        seen = set()
        queue = [strip_comments(text) for text in self.workflows().values()]
        entry = self.check_command_file()
        if entry:
            seen.add(entry)
            queue.append(strip_comments(self.read(entry)))
        named = self.check_command_package_script()
        if named:
            queue.append(named)
        collected: List[str] = []
        for _ in range(4):  # workflow -> script -> script -> script is deep enough
            following: List[str] = []
            for text in queue:
                collected.append(text)
                for token in re.findall(r"[\w./-]+", text):
                    path = token[2:] if token.startswith("./") else token
                    if path.rpartition("/")[2] == CHECKER_NAME:
                        continue
                    if path.endswith(SCRIPT_SUFFIXES) and path in self._file_set and path not in seen:
                        seen.add(path)
                        following.append(strip_comments(self.read(path)))
                for name in re.findall(r"\b(?:bun|npm|pnpm|yarn)\s+(?:run(?:-script)?\s+)?([\w:.-]+)", text):
                    if name in scripts and f"script:{name}" not in seen:
                        seen.add(f"script:{name}")
                        following.append(scripts[name])
            queue = following
            if not queue:
                break
        self._check_text = "\n".join(collected)
        return self._check_text

    def check_command_file(self) -> Optional[str]:
        for token in self.check_command.split():
            path = token.strip("'\"")
            path = path[2:] if path.startswith("./") else path
            if self.has(path):
                return path
        return None

    def check_command_package_script(self) -> Optional[str]:
        tokens = self.check_command.split()
        if len(tokens) >= 3 and tokens[0] in ("bun", "npm", "pnpm", "yarn") and tokens[1] == "run":
            return self.package_scripts().get(tokens[2])
        return None

    def package_scripts(self) -> Dict[str, str]:
        if not self.has("package.json"):
            return {}
        try:
            package = json.loads(self.read("package.json"))
        except ValueError:
            return {}
        scripts = package.get("scripts") if isinstance(package, dict) else None
        return {str(k): str(v) for k, v in scripts.items()} if isinstance(scripts, dict) else {}


# --------------------------------------------------------------------------
# Results and rules


@dataclass
class Result:
    status: str  # pass, fail, na, manual, excepted
    detail: str = ""
    count: Optional[int] = None


def ok(detail: str = "") -> Result:
    return Result("pass", detail)


def bad(detail: str) -> Result:
    return Result("fail", detail)


def na(detail: str) -> Result:
    return Result("na", detail)


def counted(count: int, detail: str = "") -> Result:
    return Result("count", detail, count)


@dataclass(frozen=True)
class Rule:
    id: str
    applies: str  # see applies_to(): all, web, service, ... or several joined by +
    level: str  # must, should
    kind: str  # auto, ratchet, manual
    title: str
    check: Optional[Callable[[Repo], Result]] = None
    older: Optional[str] = None  # the level for a repository that predates the standard, when it differs
    blocks: bool = True  # False: a failure is counted and reported, and never fails the run ("What may block")


def applies_to(rule: Rule, repo: Repo) -> bool:
    """`applies` is one condition or several joined by `+`, all of which must hold."""
    conditions: Dict[str, Callable[[], bool]] = {
        "all": lambda: True,
        "web": lambda: repo.is_web,
        "service": lambda: repo.is_web and repo.back_end == "service",
        "cloud": lambda: repo.is_web and repo.back_end == "lovable-cloud",
        "deployed": lambda: repo.deployed,
        "no-web": lambda: not repo.is_web,
        "app": lambda: repo.is_web or repo.deployed,
        "split": lambda: bool(repo.record.get("app_repositories")),
        "public": lambda: repo.exposure == "public",
        "lan": lambda: repo.exposure == "lan",
        "accounts": lambda: repo.flag("accounts"),
        # Unstated, an app with accounts is taken to have passwords and to hold data.
        "passwords": lambda: repo.flag("passwords", repo.flag("accounts")),
        "data": lambda: repo.flag("stores_data", repo.flag("accounts")),
        "phone": lambda: repo.record.get("phone_apps") in ("planned", "shipping"),
        # Home apps: on the home network, or running nowhere (the owner's "home apps first", 2026-10-13).
        "home": lambda: repo.exposure in ("lan", "none"),
        "notifications": lambda: repo.flag("notifications"),
        "licensing": lambda: repo.flag("licensing"),
        "previews": lambda: repo.flag("previews"),
        # Help is owed by a public app and by a home app with more than one role.
        "help": lambda: repo.exposure == "public"
        or (isinstance(repo.record.get("roles"), dict) and len(repo.record["roles"]) > 1),
    }
    return all(conditions[part]() for part in rule.applies.split("+"))


# checks: repository ---------------------------------------------------------


def repo_1(repo: Repo) -> Result:
    readme = repo.first("README.md")
    if not readme:
        return bad("no README.md")
    if len(repo.read(readme).strip()) < 200:
        return bad("README.md is under 200 characters")
    return ok()


def repo_2(repo: Repo) -> Result:
    if not repo.has(".gitignore"):
        return bad("no .gitignore")
    if "settings.local.json" not in repo.read(".gitignore"):
        return bad(".gitignore does not ignore .claude/settings.local.json")
    return ok()


def repo_3(repo: Repo) -> Result:
    if re.search(r"end_of_line\s*=\s*lf", repo.read(".editorconfig")) and repo.has(".editorconfig"):
        return ok(".editorconfig")
    if repo.has(".gitattributes") and "eol=lf" in repo.read(".gitattributes"):
        return ok(".gitattributes")
    return bad("neither .editorconfig nor .gitattributes sets LF line endings")


def repo_4(repo: Repo) -> Result:
    owners = repo.first(".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
    if not owners:
        return bad("no CODEOWNERS file")
    if not re.search(r"(?m)^\*\s+@\S+", repo.read(owners)):
        return bad(f"{owners} has no `* @owner` line")
    return ok(owners)


def repo_5(repo: Repo) -> Result:
    template = repo.first(
        ".github/pull_request_template.md",
        "pull_request_template.md",
        "docs/pull_request_template.md",
    )
    return ok(template or "") if template else bad("no pull request template")


def repo_8(repo: Repo) -> Result:
    found = repo.first("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")
    return ok(found or "") if found else bad("no LICENSE file")


# checks: agent instructions -------------------------------------------------


def agent_1(repo: Repo) -> Result:
    if not repo.has("CLAUDE.md"):
        return bad("no CLAUDE.md")
    command = repo.check_command
    if command not in repo.read("CLAUDE.md"):
        return bad(f"CLAUDE.md does not name the check command `{command}`")
    return ok()


def agent_2(repo: Repo) -> Result:
    path = ".claude/settings.json"
    if not repo.has(path):
        return bad(f"no {path}")
    try:
        settings = json.loads(repo.read(path))
    except ValueError as error:
        return bad(f"{path} is not valid JSON: {error}")
    permissions = settings.get("permissions") if isinstance(settings, dict) else None
    deny = permissions.get("deny") if isinstance(permissions, dict) else None
    if not any(".env" in str(entry) for entry in (deny if isinstance(deny, list) else [])):
        return bad(f"{path} does not deny reading .env files")
    return ok()


# checks: CI -----------------------------------------------------------------


def ci_1(repo: Repo) -> Result:
    command = repo.check_command
    tokens = command.split()
    if len(tokens) >= 3 and tokens[0] in ("bun", "npm", "pnpm", "yarn") and tokens[1] == "run":
        if tokens[2] in repo.package_scripts():
            return ok(f"package.json script `{tokens[2]}`")
        return bad(f"package.json has no script `{tokens[2]}`")
    if tokens and tokens[0] == "make":
        return ok("Makefile") if repo.first("Makefile") else bad("no Makefile")
    script = repo.check_command_file()
    return ok(script) if script else bad(f"`{command}` names no file in the repository")


def ci_2(repo: Repo) -> Result:
    workflows = repo.workflows()
    if not workflows:
        return bad("no workflow in .github/workflows")
    command = repo.check_command
    for name, text in workflows.items():
        if command in strip_comments(text):
            return ok(name)
    return bad(f"no workflow runs `{command}`")


def ci_3(repo: Repo) -> Result:
    workflows = repo.workflows()
    if not workflows:
        return bad("no workflow in .github/workflows")
    loose = [name for name, text in workflows.items() if workflow_triggers(text).get("push") is False]
    if loose:
        return bad("runs on every push: " + ", ".join(sorted(loose)))
    return ok()


def ci_4(repo: Repo) -> Result:
    workflows = repo.workflows()
    if not workflows:
        return bad("no workflow in .github/workflows")
    missing = [name for name, text in workflows.items() if not re.search(r"(?m)^\s*permissions\s*:", text)]
    return bad("no `permissions`: " + ", ".join(sorted(missing))) if missing else ok()


def ci_5(repo: Repo) -> Result:
    unpinned = []
    for name, text in repo.workflows().items():
        for ref in re.findall(r"(?m)^\s*-?\s*uses\s*:\s*([^\s#]+)", strip_comments(text)):
            ref = ref.strip("'\"")
            if ref.startswith(("./", "docker://")):
                continue
            if not re.search(r"@[0-9a-f]{40}\Z", ref):
                unpinned.append(f"{name}: {ref}")
    return counted(len(unpinned), "; ".join(unpinned[:5]))


def ci_6(repo: Repo) -> Result:
    missing = 0
    where = []
    for name, text in repo.workflows().items():
        lacking = jobs_without_timeout(text)
        if lacking:
            missing += lacking
            where.append(name)
    return counted(missing, ", ".join(sorted(where)[:5]))


def ci_7(repo: Repo) -> Result:
    workflows = repo.workflows()
    if not workflows:
        return bad("no workflow in .github/workflows")
    allowed = repo.ci_runners
    wrong = []
    for name, text in workflows.items():
        for labels in runner_label_sets(text):
            if labels is None:
                wrong.append(f"{name}: a runner that cannot be read (write its labels out)")
            elif not any(entry <= labels for entry in allowed):
                wrong.append(f"{name}: {', '.join(sorted(labels))}")
    return bad("runner not in ci_runners: " + "; ".join(wrong[:5])) if wrong else ok()


# Ways a job stops a process it did not start, or tests against a server it did not start. On a host that runs
# several jobs at once, each of these takes down or borrows another job's server.
SHARED_HOST_HAZARDS = re.compile(
    r"\blsof\s+-t|\bfuser\s+-k|\bkill-port\b|\bpkill\b|\bkillall\b|reuseExistingServer\s*:\s*true\b"
)
CI_CODE = re.compile(r"(\A|/)(scripts|e2e|tests|bin)/|(\A|/)playwright[^/]*\.config\.[a-z]+\Z|\A\.github/workflows/")


def ci_10(repo: Repo) -> Result:
    hits: Dict[str, int] = {}
    for name in repo.files:
        if not CI_CODE.search(name) or name in TEMPLATE_OWNED or name.endswith(".md"):
            continue
        for line in repo.read(name).splitlines():
            if line.lstrip().startswith(("#", "//", "*")):
                continue
            if SHARED_HOST_HAZARDS.search(line):
                hits[name] = hits.get(name, 0) + 1
    top = sorted(hits.items(), key=lambda item: (-item[1], item[0]))[:3]
    return counted(sum(hits.values()), ", ".join(f"{name} {count}" for name, count in top))


def ci_8(repo: Repo) -> Result:
    missing = [
        name
        for name, text in repo.workflows().items()
        if "pull_request" in workflow_triggers(text) and not re.search(r"(?m)^\s*concurrency\s*:", text)
    ]
    return bad("no `concurrency`: " + ", ".join(sorted(missing))) if missing else ok()


# checks: dependencies -------------------------------------------------------


def _manifest_dirs(repo: Repo) -> Dict[str, str]:
    """{folder: 'js' | 'py'} for every folder that declares dependencies."""
    found: Dict[str, str] = {}
    for name in repo.files:
        if "node_modules/" in name:
            continue
        folder, _, base = name.rpartition("/")
        if base == "package.json":
            found[folder] = "js"
        elif base == "pyproject.toml" and re.search(r"(?m)^\s*dependencies\s*=", repo.read(name)):
            found.setdefault(folder, "py")
    return found


def dep_1(repo: Repo) -> Result:
    manifests = _manifest_dirs(repo)
    if not manifests:
        return na("no dependency manifest")
    problems = []
    for folder, kind in sorted(manifests.items()):
        prefix = f"{folder}/" if folder else ""
        if kind == "js":
            managers = {JS_LOCKFILES[lock] for lock in JS_LOCKFILES if repo.has(prefix + lock)}
            if not managers:
                problems.append(f"{prefix}package.json has no lockfile")
            elif len(managers) > 1:
                problems.append(f"{prefix or './'} has lockfiles of {', '.join(sorted(managers))}")
        else:
            locks = [lock for lock in PY_LOCKFILES if repo.has(prefix + lock)]
            pinned = [n for n in repo.files if re.match(re.escape(prefix) + r"requirements[^/]*\.txt\Z", n)]
            if not locks and not pinned:
                problems.append(f"{prefix}pyproject.toml has no lockfile")
    return bad("; ".join(problems)) if problems else ok()


def dep_2(repo: Repo) -> Result:
    if not _manifest_dirs(repo):
        return na("no dependency manifest")
    if FROZEN_INSTALL.search(repo.check_text()):
        return ok()
    return bad("CI does not install from the lockfile with a frozen or locked flag")


def dep_3(repo: Repo) -> Result:
    if not any(JS_LOCKFILES.get(name.rpartition("/")[2]) == "bun" for name in repo.files):
        return na("not a bun project")
    found = re.search(r"(?m)^\s*minimumReleaseAge\s*=\s*(\d+)", repo.read("bunfig.toml"))
    if not found:
        return bad("bunfig.toml sets no minimumReleaseAge")
    if int(found.group(1)) < 86400:
        return bad(f"minimumReleaseAge is {found.group(1)}, under 86400")
    return ok()


def dep_5(repo: Repo) -> Result:
    config = repo.first(".github/dependabot.yml", ".github/dependabot.yaml")
    if not config:
        return bad("no .github/dependabot.yml")
    text = repo.read(config)
    listed = set(re.findall(r"package-ecosystem\s*:\s*[\"']?([a-z-]+)", text))
    wanted = []
    if repo.workflows():
        wanted.append(("github-actions", {"github-actions"}))
    kinds = set(_manifest_dirs(repo).values())
    if "js" in kinds:
        wanted.append(("npm or bun", {"npm", "bun"}))
    if "py" in kinds:
        wanted.append(("pip or uv", {"pip", "uv"}))
    missing = [label for label, names in wanted if not names & listed]
    return bad("dependabot does not cover: " + ", ".join(missing)) if missing else ok()


# checks: tests --------------------------------------------------------------

def test_1(repo: Repo) -> Result:
    if not repo.source_files():
        return na("no source files")
    tests = [name for name in repo.files if is_test_file(name)]
    return ok(f"{len(tests)} test files") if tests else bad("no test files")


def test_2(repo: Repo) -> Result:
    if not repo.source_files():
        return na("no source files")
    for name in repo.files:
        base = name.rpartition("/")[2]
        if re.search(r"coverage[-_]?(floor|baseline|ratchet)", base, re.I):
            return ok(name)
        if re.match(r"(vitest|vite|jest)\.config\.", base) and re.search(
            r"thresholds|coverageThreshold", repo.read(name)
        ):
            return ok(name)
        if re.match(r"\.(c8rc|nycrc)", base):
            return ok(name)
        if base in ("pyproject.toml", ".coveragerc", "setup.cfg") and "fail_under" in repo.read(name):
            return ok(name)
    if re.search(r"--(cov-)?fail-under", repo.check_text()):
        return ok("fail-under flag in the checks")
    return bad("no coverage floor is configured")


def test_3(repo: Repo) -> Result:
    sources = repo.source_files()
    has_ts = sum(name.endswith((".ts", ".tsx")) for name in sources) >= TYPED_LANGUAGE_FILES
    has_py = sum(name.endswith(".py") for name in sources) >= TYPED_LANGUAGE_FILES
    if not has_ts and not has_py:
        return na("too little TypeScript or Python to judge")
    problems = []
    if has_ts:
        configs = [n for n in repo.files if re.match(r"(.*/)?tsconfig[^/]*\.json\Z", n) and "node_modules/" not in n]
        if not any(re.search(r'"strict"\s*:\s*true', repo.read(n)) for n in configs):
            problems.append("no tsconfig sets \"strict\": true")
    if has_py:
        typed = (
            any("[tool.mypy]" in repo.read(n) or "[tool.pyright]" in repo.read(n) for n in repo.files if n.endswith("pyproject.toml"))
            or repo.first("mypy.ini", "pyrightconfig.json") is not None
        )
        if not typed:
            problems.append("no mypy or pyright configuration")
    return bad("; ".join(problems)) if problems else ok()


def test_4(repo: Repo) -> Result:
    if not repo.workflows():
        return bad("no workflow in .github/workflows")
    return ok() if LINT_TOOLS.search(repo.check_text()) else bad("CI runs no lint or format check")


def sec_1(repo: Repo) -> Result:
    if not repo.workflows():
        return bad("no workflow in .github/workflows")
    return ok() if SECRET_SCANNERS.search(repo.check_text()) else bad("CI runs no secret scan")


# checks: work tracking ------------------------------------------------------


def work_2(repo: Repo) -> Result:
    log = git(repo.root, "log", "--no-merges", "-n", "100", "--format=%an%x09%s")
    if log is None:
        return na("no git history")
    odd = []
    for line in log.splitlines():
        author, _, subject = line.partition("\t")
        if author.endswith("[bot]") or author in repo.builder_authors:
            continue
        if not 10 <= len(subject) <= 120:
            odd.append(subject[:40].strip())
    return counted(len(odd), "; ".join(odd[:3]))


PROGRESS_FILE = re.compile(r"(?i)(\A|/)(roadmap|todo|to-do|backlog)(\.[a-z]+)?\Z")
CHECKLIST_ITEM = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s*\[[ xX]\]")
POINTER_LINES = 12  # a pointer to the issues is a few lines; more than this is a list kept in a file


def work_4(repo: Repo) -> Result:
    kept = []
    for name in repo.files:
        if not PROGRESS_FILE.search(name):
            continue
        text = repo.read(name)
        lines = sum(1 for line in text.splitlines() if line.strip())
        if CHECKLIST_ITEM.search(text):
            kept.append(f"{name} (has checklist items)")
        elif lines > POINTER_LINES:
            kept.append(f"{name} ({lines} lines; a pointer is at most {POINTER_LINES})")
    return bad("progress kept in a file: " + "; ".join(sorted(kept)[:5])) if kept else ok()


# checks: logging -------------------------------------------------------------

# Outside services an app can send logs, errors or usage to, and how each shows up among the dependencies.
LOG_SERVICE_PACKAGES = {
    "sentry": r"@sentry/|sentry[-_]sdk",
    "posthog": r"posthog",
    "datadog": r"dd-trace|ddtrace|datadog",
    "logrocket": r"logrocket",
    "bugsnag": r"bugsnag",
    "rollbar": r"rollbar",
    "new relic": r"newrelic",
    "honeycomb": r"honeycombio|@honeycombio/",
    "mixpanel": r"mixpanel",
    "amplitude": r"@amplitude/|amplitude-analytics",
    "segment": r"@segment/|analytics-node|segment-analytics",
    "logtail": r"logtail|@logtail/",
}
# The same, for a service that is reached from the code with no package of its own.
LOG_SERVICE_CODE = {
    "sentry": r"\bsentry_sdk\b|from\s+[\"']@sentry/",
    "lovable": r"__lovableEvents|__lovableReportRuntimeError",
}
# Approved in general by the owner, and still needing his permission in each project that uses them.
PROVISIONAL_LOG_SERVICES = ("sentry",)


def _dependency_names(repo: Repo) -> List[str]:
    names: List[str] = []
    for name in repo.files:
        base = name.rpartition("/")[2]
        if "node_modules/" in name:
            continue
        if base == "package.json":
            try:
                data = json.loads(repo.read(name))
            except ValueError:
                continue
            if isinstance(data, dict):
                for table in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
                    if isinstance(data.get(table), dict):
                        names.extend(str(key) for key in data[table])
        elif base == "pyproject.toml" or re.match(r"requirements[^/]*\.txt\Z", base):
            names.extend(re.findall(r"(?m)^\s*[\"']?([A-Za-z0-9][A-Za-z0-9._-]*)", repo.read(name)))
    return names


def log_services_in_use(repo: Repo) -> List[str]:
    """The outside logging, error and usage services the project's dependencies or code reach."""
    found = set()
    names = "\n".join(_dependency_names(repo)).lower()
    for service, pattern in LOG_SERVICE_PACKAGES.items():
        if re.search(pattern, names):
            found.add(service)
    for name in repo.source_files():
        if name.endswith(CHECKER_NAME):
            continue  # the checker names these services in order to look for them
        text = repo.read(name)
        for service, pattern in LOG_SERVICE_CODE.items():
            if service not in found and re.search(pattern, text):
                found.add(service)
    return sorted(found)


def approved_log_services(repo: Repo) -> List[str]:
    """Services the record names with who approved them and when. An entry without both does not count."""
    entries = repo.record.get("log_services", [])
    approved = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and all(isinstance(entry.get(key), str) and entry[key].strip() for key in ("name", "approved_by", "date")):
            approved.append(entry["name"].strip().lower())
    return approved


def log_3(repo: Repo) -> Result:
    approved = approved_log_services(repo)
    unnamed = [service for service in log_services_in_use(repo) if service not in approved]
    notes = []
    for service in unnamed:
        if service in PROVISIONAL_LOG_SERVICES:
            notes.append(f"{service} (approved in general; still needs the owner's permission for this project)")
        else:
            notes.append(service)
    return counted(len(unnamed), "not in log_services: " + ", ".join(notes) if notes else "")


# checks: outbound traffic and temporary files ------------------------------

# Packages that reach an outside service while the app runs, beyond the logging services LOG-3 counts.
OUTBOUND_PACKAGES = {
    "segment": r"(?m)^(@segment/|analytics-node$|analytics-python$)",
    "mixpanel": r"(?m)^mixpanel",
    "amplitude": r"(?m)^(@amplitude/|amplitude)",
    "google-analytics": r"(?m)^(react-ga|react-ga4|@next/third-parties|vue-gtag|ga-4-react)",
    "datadog": r"(?m)^(@datadog/|dd-trace$|ddtrace$|datadog$)",
    "new-relic": r"(?m)^(newrelic$|@newrelic/)",
    "logrocket": r"(?m)^logrocket",
    "hotjar": r"(?m)^(@hotjar/|react-hotjar)",
    "bugsnag": r"(?m)^(@bugsnag/|bugsnag)",
    "rollbar": r"(?m)^rollbar",
    "openai": r"(?m)^(openai$|@openai/)",
    "anthropic": r"(?m)^(anthropic$|@anthropic-ai/sdk$)",
    "hugging-face-hub": r"(?m)^(huggingface[-_]hub$|@huggingface/)",
    "gradio": r"(?m)^gradio$",
    "streamlit": r"(?m)^streamlit$",
}
# An address in a string in the code. A comment's link is not a call, so commented lines are skipped.
URL_IN_STRING = re.compile(r"[\"'`](?:https?|wss?)://([A-Za-z0-9.-]+)")
COMMENT_LINE = re.compile(r"^\s*(#|//|\*|/\*|<!--)")
NOT_OUTBOUND = re.compile(
    r"(^|\.)(localhost|example\.(com|org|net)|[a-z0-9-]+\.(test|invalid|example|local|internal)|int\.butters\.me)$"
    r"|^127\.|^10\.|^192\.168\.|^172\.(1[6-9]|2\d|3[01])\.|^0\.0\.0\.0$"
    r"|^(www\.w3\.org|json\.schemastore\.org)$"  # XML and JSON namespaces, never fetched by the app
)


def approved_outbound(repo: Repo) -> Tuple[List[str], List[str]]:
    """(hosts, package names) the record approves under `outbound`: host, purpose, approved_by and date."""
    entries = repo.record.get("outbound", [])
    hosts: List[str] = []
    packages: List[str] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(key), str) and entry[key].strip() for key in ("purpose", "approved_by", "date")
        ):
            continue
        if isinstance(entry.get("host"), str):
            hosts.append(entry["host"].strip().lower())
        if isinstance(entry.get("package"), str):
            packages.append(entry["package"].strip().lower())
    return hosts, packages + approved_log_services(repo)


def outbound_hosts_in_code(repo: Repo) -> Dict[str, List[str]]:
    """{host: [files]} for addresses outside the home network written in strings in the app's code."""
    found: Dict[str, List[str]] = {}
    for name in repo.source_files():
        if name.endswith(CHECKER_NAME) or name.startswith((".github/", "docs/")) or name.endswith(TEMPLATE_OWNED):
            continue
        for line in repo.read(name).splitlines():
            if COMMENT_LINE.match(line):
                continue
            for host in URL_IN_STRING.findall(line):
                host = host.lower().rstrip(".")
                if "." not in host:
                    continue  # a placeholder in an example (`http://name:port`), never an internet address
                if not NOT_OUTBOUND.search(host) and name not in found.setdefault(host, []):
                    found[host].append(name)
    return found


def net_4(repo: Repo) -> Result:
    hosts, packages = approved_outbound(repo)
    names = "\n".join(_dependency_names(repo)).lower()
    unlisted = [
        f"package {service}" for service, pattern in OUTBOUND_PACKAGES.items()
        if re.search(pattern, names) and service not in packages
    ]
    for host, files in sorted(outbound_hosts_in_code(repo).items()):
        if host not in hosts and not any(host.endswith("." + h) for h in hosts):
            unlisted.append(f"`{host}` ({files[0]})")
    return counted(len(unlisted), "not in outbound: " + ", ".join(unlisted) if unlisted else "")


FIXED_TMP = re.compile(r"[\"'`](/tmp/[A-Za-z0-9_.-]+)")


def tmp_2(repo: Repo) -> Result:
    found: Dict[str, int] = {}
    for name in repo.files:
        if (
            not name.endswith(SCRIPT_SUFFIXES + (".tsx", ".jsx", ".yml", ".yaml"))
            or name.endswith(CHECKER_NAME)
            or "node_modules/" in name
        ):
            continue
        for line in repo.read(name).splitlines():
            if not COMMENT_LINE.match(line) and FIXED_TMP.search(line):
                found[name] = found.get(name, 0) + 1
    where = ", ".join(f"{n} {c}" for n, c in sorted(found.items())[:5])
    return counted(sum(found.values()), where)


def tmp_5(repo: Repo) -> Result:
    places = [repo.check_command_file() or "", *repo.workflows()]
    clean = r"git status --porcelain|git diff --exit-code"
    for name in places:
        if name and re.search(clean, repo.read(name)):
            return ok(name)
    if re.search(clean, repo.check_command_package_script() or ""):
        return ok("the package script")
    return bad("neither the check command nor CI checks that the checks left the checkout clean")


# checks: layout and record --------------------------------------------------


def layout_1(repo: Repo) -> Result:
    if repo.record_error:
        return bad(repo.record_error)
    record = repo.record
    problems = []
    if not isinstance(record.get("standard_version"), str):
        problems.append("standard_version")
    layers = record.get("layers")
    if not (isinstance(layers, list) and "base" in layers and all(layer in ("base", "web") for layer in layers)):
        problems.append("layers")
    elif "web" not in layers and repo.looks_like_web():
        # Copier never rewrites standard.json, so a front end added later
        # has to be recorded by hand. Without this the web rules would
        # silently not apply.
        problems.append("layers (the repository has a front end, the record says it has none)")
    if not (isinstance(record.get("check_command"), str) and record["check_command"].strip()):
        problems.append("check_command")
    runners = record.get("ci_runners")
    if not (isinstance(runners, list) and runners and all(isinstance(r, str) for r in runners)):
        problems.append("ci_runners")
    return bad("missing or wrong in standard.json: " + ", ".join(problems)) if problems else ok()


NEVER_EXCEPTED = ("LAYOUT-1", "LAYOUT-2")  # the record and its exceptions cannot waive themselves


def read_exceptions(repo: Repo) -> Tuple[Dict[str, str], List[str]]:
    """({rule: reason} for complete entries, [what is wrong with the others])."""
    exceptions = repo.record.get("exceptions", [])
    if not isinstance(exceptions, list):
        return {}, ["exceptions is not a list"]
    known = {rule.id for rule in RULES}
    valid: Dict[str, str] = {}
    problems: List[str] = []
    for index, entry in enumerate(exceptions):
        rule = entry.get("rule") if isinstance(entry, dict) else None
        if not isinstance(rule, str) or rule not in known:
            problems.append(f"entry {index + 1}: unknown rule")
            continue
        wrong = [key for key in ("reason", "approved_by") if not (isinstance(entry.get(key), str) and entry[key].strip())]
        if not re.match(r"\d{4}-\d{2}-\d{2}\Z", str(entry.get("date", ""))):
            wrong.append("date")
        if rule in NEVER_EXCEPTED:
            problems.append(f"{rule}: cannot be excepted")
        elif wrong:
            problems.extend(f"{rule}: no {key}" for key in wrong)
        else:
            valid[rule] = entry["reason"].strip()
    return valid, problems


def layout_2(repo: Repo) -> Result:
    valid, problems = read_exceptions(repo)
    return bad("; ".join(problems)) if problems else ok(f"{len(valid)} recorded")


RESERVED_ROOT = ("package.json", "index.html", "tsconfig.json")


def layout_3(repo: Repo) -> Result:
    if repo.record.get("predates_standard") is True:
        return ok("not asked of a repository that predates the standard")
    taken = [name for name in RESERVED_ROOT if repo.has(name)]
    taken += [name for name in repo.files if re.match(r"vite\.config\.[a-z]+\Z", name)]
    taken += [folder for folder in ("src/", "public/") if repo.under(folder)]
    return bad("the root holds: " + ", ".join(sorted(taken))) if taken else ok()


def layout_4(repo: Repo) -> Result:
    if not repo.has(".copier-answers.yml"):
        return bad("no .copier-answers.yml")
    if not re.search(r"(?m)^_commit\s*:", repo.read(".copier-answers.yml")):
        return bad(".copier-answers.yml records no template version")
    return ok()


# checks: deployed projects --------------------------------------------------


def ops_1(repo: Repo) -> Result:
    endpoint = repo.path_setting("version_endpoint")
    if not endpoint:
        return bad("paths.version_endpoint is not set")
    needle = endpoint.strip("/")
    in_names = needle.replace(".", "[.]")
    for name in repo.source_files():
        if needle in name or in_names in name or endpoint in repo.read(name):
            return ok(name)
    return bad(f"nothing in the source mentions {endpoint}")


BUILDER_FILE_LIMIT = 10 * 1024 * 1024  # the UI builder's repository rejects a larger file, and has removed one


def lane_11(repo: Repo) -> Result:
    large = []
    for name in repo.files:
        try:
            size = (repo.root / name).stat().st_size
        except OSError:
            continue
        if size > BUILDER_FILE_LIMIT:
            large.append(f"{name} ({size // (1024 * 1024)} MB)")
    return bad("over 10 MB: " + ", ".join(sorted(large)[:5])) if large else ok()


def ops_6(repo: Repo) -> Result:
    if repo.record.get("back_end_in_repo") is True:
        return ok("not asked: the back end is in this repository, so the two halves are one commit")
    if not repo.has("api-requires.json"):
        return bad("no api-requires.json")
    try:
        value = json.loads(repo.read("api-requires.json")).get("min_api_revision")
    except (ValueError, AttributeError):
        return bad("api-requires.json is not a JSON object")
    if not isinstance(value, int) or isinstance(value, bool):
        return bad("min_api_revision is not a whole number")
    return ok(f"min_api_revision {value}")


# checks: lanes --------------------------------------------------------------


def lane_1(repo: Repo) -> Result:
    if not repo.has("AGENTS.md"):
        return bad("no AGENTS.md")
    if not re.search(r"(?im)^#{1,6}\s.*\b(lanes?|who owns|ownership)\b", repo.read("AGENTS.md")):
        return bad("AGENTS.md has no section on lanes (a heading with \"Lanes\" or \"Who owns what\")")
    return ok()


def lane_2(repo: Repo) -> Result:
    if not repo.has("AGENTS.md"):
        return bad("no AGENTS.md")
    size = len(repo.read("AGENTS.md"))
    return ok(f"{size} characters") if size < 5000 else bad(f"{size} characters")


def lane_3(repo: Repo) -> Result:
    if repo.record_error:
        return bad(repo.record_error)
    problems = []
    lanes = repo.record.get("lanes")
    if not (isinstance(lanes, dict) and isinstance(lanes.get("builder"), list) and lanes["builder"]):
        problems.append("lanes.builder")
    if repo.record.get("back_end") not in ("service", "lovable-cloud"):
        problems.append("back_end")
    return bad("missing or wrong in standard.json: " + ", ".join(problems)) if problems else ok()


def builder_commits(repo: Repo, revision_args: List[str]) -> Optional[List[Tuple[str, List[str]]]]:
    """[(short sha, files)] for commits by the UI builder, newest first.

    --cc lists what a merge commit itself changed; --no-renames lists both
    ends of a move, so moving a file out of a protected folder is seen.
    """
    log = git(
        repo.root, "log", "--cc", "--no-renames", "--relative", "--name-only", "--format=%x00%h%x09%an", *revision_args
    )
    if log is None:
        return None
    authors = [name.strip().lower() for name in repo.builder_authors]
    commits = []
    for chunk in log.split("\0")[1:]:
        header, _, body = chunk.partition("\n")
        sha, _, author = header.partition("\t")
        if author.strip().lower() in authors:
            commits.append((sha, [line for line in body.splitlines() if line.strip()]))
    return commits


def out_of_lane(repo: Repo, commits: List[Tuple[str, List[str]]], lane: str = "closed") -> List[Tuple[str, str]]:
    """The UI builder's changes to paths that are closed to it, or to paths it may write only under an override."""
    return [(sha, path) for sha, files in commits for path in files if repo.builder_lane(path) == lane]


def lane_4(repo: Repo) -> Result:
    commits = builder_commits(repo, ["-n", str(HISTORY_DEPTH)])
    if commits is None:
        return na("no git history")
    strays = out_of_lane(repo, commits)
    areas: Dict[str, int] = {}
    for _, path in strays:
        area = "/".join(path.split("/")[:2]) if "/" in path else path
        areas[area] = areas.get(area, 0) + 1
    top = sorted(areas.items(), key=lambda item: (-item[1], item[0]))[:5]
    detail = f"{len(commits)} builder commits in the last {HISTORY_DEPTH} commits, merges included"
    if top:
        detail += "; outside its lanes: " + ", ".join(f"{area} {count}" for area, count in top)
    overridden = len(out_of_lane(repo, commits, "override"))
    if overridden:
        detail += f"; under the owner's override or by a slip, not counted: {overridden}"
    return counted(len(strays), detail)


def lane_5(repo: Repo) -> Result:
    if "--lane-guard" in repo.check_text():
        return ok()
    return bad("neither a workflow nor the check command runs --lane-guard")


# checks: data layer and interface -------------------------------------------


def _count_in(repo: Repo, files: List[str], pattern: "re.Pattern[str]", by_line: bool = False) -> Result:
    total = 0
    worst: List[Tuple[int, str]] = []
    for name in files:
        text = repo.read(name)
        hits = sum(1 for line in text.splitlines() if pattern.search(line)) if by_line else len(pattern.findall(text))
        if hits:
            total += hits
            worst.append((hits, name))
    worst.sort(key=lambda item: (-item[0], item[1]))
    return counted(total, ", ".join(f"{name} {hits}" for hits, name in worst[:3]))


def data_1(repo: Repo) -> Result:
    return _count_in(repo, repo.screen_files((".ts", ".tsx", ".js", ".jsx")), NETWORK_CALL)


def data_6(repo: Repo) -> Result:
    files = repo.screen_files((".ts", ".tsx", ".js", ".jsx", ".css"))
    files += [name for name in ("index.html", "src/styles.css", "src/index.css") if repo.has(name) and name not in files]
    return _count_in(repo, files, REMOTE_ASSET, by_line=True)


def data_7(repo: Repo) -> Result:
    found = repo.under("supabase/")
    return bad(f"supabase/ holds {len(found)} files") if found else ok()


def ui_1(repo: Repo) -> Result:
    return ok() if repo.has("components.json") else bad("no components.json")


def ui_2(repo: Repo) -> Result:
    return _count_in(repo, repo.screen_files((".tsx", ".jsx")), RAW_CONTROL)


def ui_3(repo: Repo) -> Result:
    return _count_in(repo, repo.screen_files((".tsx", ".jsx")), HEX_COLOUR)


def mobile_1(repo: Repo) -> Result:
    configs = [n for n in repo.files if re.search(r"(^|/)playwright[^/]*\.config\.[a-z]+\Z", n)]
    if not configs:
        return bad("no Playwright configuration")
    if not any(re.search(r"webkit|iPhone|Safari", repo.read(n)) for n in configs):
        return bad("no Playwright project uses WebKit")
    if not re.search(r"playwright", repo.check_text(), re.I):
        return bad("CI does not run Playwright")
    return ok(configs[0])


def mobile_3(repo: Repo) -> Result:
    return _count_in(repo, repo.screen_files((".tsx", ".jsx", ".css")), VIEWPORT_HEIGHT)


def mobile_10(repo: Repo) -> Result:
    return _count_in(repo, repo.screen_files((".tsx", ".jsx")), EAGER_IMAGE)


def a11y_2(repo: Repo) -> Result:
    baseline = repo.path_setting("a11y_baseline")
    if baseline and not repo.has(baseline):
        return bad(f"{baseline} does not exist")
    if not baseline:
        guesses = [n for n in repo.files if re.search(r"a11y-baseline[^/]*\.json\Z", n)]
        if not guesses:
            return bad("no accessibility baseline file")
        baseline = guesses[0]
    packages = [n for n in repo.files if n.rpartition("/")[2] == "package.json" and "node_modules/" not in n]
    if not any("axe-core" in repo.read(n) for n in packages):
        return bad("axe-core is in no package.json")
    return ok(baseline)


def a11y_3(repo: Repo) -> Result:
    registry = repo.path_setting("control_registry")
    if registry:
        return ok(registry) if repo.has(registry) else bad(f"{registry} does not exist")
    if repo.has("e2e/registry.ts"):
        return ok("e2e/registry.ts (not yet named in standard.json)")
    return bad("paths.control_registry is not set")


def i18n_1(repo: Repo) -> Result:
    candidates = [n for n in repo.files if n.endswith((".html", ".tsx", ".jsx")) and "node_modules/" not in n]
    for name in candidates:
        text = repo.read(name)
        if "<html" in text:
            if re.search(r"<html[^>]*\blang=", text):
                return ok(name)
            return bad(f"{name}: <html> has no lang")
    return bad("no <html> element found")


# checks: added in version 0.2 -------------------------------------------------

VERSION_FORMAT = re.compile(r"\A20\d\d\.(?:[1-9]|1[0-2])\.(?:0|[1-9]\d*)\Z")
RECORD_FACTS = {"exposure": ("public", "lan", "none"), "phone_apps": ("none", "planned", "shipping")}


def layout_5(repo: Repo) -> Result:
    record = repo.record
    problems = []
    if repo.exposure is None:
        problems.append("exposure (or a category)")
    if not isinstance(record.get("accounts", repo.category_defaults.get("accounts")), bool):
        problems.append("accounts (or a category)")
    if repo.is_web and record.get("phone_apps") not in RECORD_FACTS["phone_apps"]:
        problems.append("phone_apps")
    return bad("missing or wrong in standard.json: " + ", ".join(problems)) if problems else ok()


def ver_1(repo: Repo) -> Result:
    if repo.has("package.json"):
        try:
            version = json.loads(repo.read("package.json")).get("version")
        except (ValueError, AttributeError):
            return bad("package.json is not valid JSON")
        source = "package.json"
    elif repo.has("pyproject.toml"):
        found = re.search(r'(?m)^version\s*=\s*"([^"]*)"', repo.read("pyproject.toml"))
        version, source = (found.group(1) if found else None), "pyproject.toml"
    else:
        return bad("no package.json or pyproject.toml holds the version")
    if not isinstance(version, str):
        return bad(f"{source} has no version")
    if not VERSION_FORMAT.match(version):
        return bad(f"{source}: {version} is not YYYY.M.N")
    return ok(f"{source}: {version}")


def pub_1(repo: Repo) -> Result:
    if not repo.has("public/robots.txt"):
        return bad("no public/robots.txt")
    if repo.record.get("exposure") == "lan":
        lines = [line.split("#")[0].strip().lower() for line in repo.read("public/robots.txt").splitlines()]
        blocks_all = "user-agent: *" in lines and "disallow: /" in lines
        if not blocks_all:
            return bad("a home app's robots.txt must say User-agent: * and Disallow: /")
    return ok()


def mobile_5(repo: Repo) -> Result:
    pages = [n for n in repo.files if n.endswith((".html", ".tsx", ".jsx")) and "node_modules/" not in n]
    found = next((n for n in pages if "viewport-fit=cover" in repo.read(n).replace(" ", "")), None)
    return ok(found) if found else bad("no viewport meta with viewport-fit=cover")


def ui_file_has(repo: Repo, name: str, needle: str, what: str) -> Result:
    path = f"src/components/ui/{name}"
    if not repo.has(path):
        return bad(f"no {path}")
    return ok(path) if needle in repo.read(path) else bad(f"{path} has no {what}")


def mobile_6(repo: Repo) -> Result:
    return ui_file_has(repo, "input.tsx", "text-base", "text-base (16 px) for phones")


def mobile_7(repo: Repo) -> Result:
    return ui_file_has(repo, "dialog.tsx", "max-h-", "height limit (max-h-)")


def layout_6(repo: Repo) -> Result:
    category = repo.record.get("category")
    if category is None:
        return na("no category recorded")
    if category not in CATEGORIES:
        return bad(f"unknown category {category!r}; one of: " + ", ".join(CATEGORIES))
    exposure, accounts = repo.record.get("exposure"), repo.record.get("accounts")
    problems = []
    if category.startswith("public-") and exposure not in (None, "public", "none"):
        problems.append(f"a {category} app is public, the record says exposure {exposure}")
    if category == "private-lan" and exposure not in (None, "lan", "none"):
        problems.append(f"a private-lan app is on the home network, the record says exposure {exposure}")
    if category == "tools" and exposure == "public":
        problems.append("a tool is not public; a public one has become a web app")
    if category == "public-brochure" and accounts is True:
        problems.append("a brochure app has no sign-in; one with accounts is public-business")
    if category == "public-business" and accounts is False:
        problems.append("a public-business app has accounts; one without is public-brochure")
    if category == "tools" and repo.is_web:
        problems.append("a tool has no front end; one with a front end is a web app")
    return bad("; ".join(problems)) if problems else ok(category)


PHONE_DECISIONS = "docs/phone-app-decisions.md"


def app_1(repo: Repo) -> Result:
    if not repo.has(PHONE_DECISIONS):
        return bad(f"no {PHONE_DECISIONS}")
    open_items = len(re.findall(r"\bTBD\b", repo.read(PHONE_DECISIONS)))
    return bad(f"{open_items} questions still TBD in {PHONE_DECISIONS}") if open_items else ok()


RULES: List[Rule] = [
    Rule("REPO-1", "all", "must", "auto", "README says what the project is", repo_1),
    Rule("REPO-2", "all", "should", "auto", ".gitignore covers personal agent files", repo_2),
    Rule("REPO-3", "all", "should", "auto", "LF line endings", repo_3),
    Rule("REPO-4", "all", "should", "auto", "CODEOWNERS names an owner", repo_4),
    Rule("REPO-5", "all", "should", "auto", "Pull request template", repo_5),
    Rule("REPO-6", "all", "must", "manual", "Code and documentation only, no user content"),
    Rule("REPO-7", "all", "must", "manual", "Pushed history on main is never rewritten"),
    Rule("REPO-8", "all", "must", "auto", "LICENSE file", repo_8, older="should"),
    Rule("AGENT-1", "all", "must", "auto", "CLAUDE.md names the check command", agent_1),
    Rule("AGENT-2", "all", "must", "auto", ".claude/settings.json denies reading secrets", agent_2),
    Rule("AGENT-3", "all", "should", "manual", "Every agent rule carries its reason"),
    Rule("AGENT-4", "all", "must", "manual", "Spending and live data need the owner's go-ahead"),
    Rule("AGENT-5", "all", "must", "manual", "Other repositories are read-only"),
    Rule("CI-1", "all", "must", "auto", "One command runs every check", ci_1),
    Rule("CI-2", "all", "must", "auto", "CI runs that command", ci_2),
    Rule("CI-3", "all", "must", "auto", "Push runs are for main only", ci_3),
    Rule("CI-4", "all", "must", "auto", "Every workflow declares permissions", ci_4),
    Rule("CI-5", "all", "must", "ratchet", "Actions pinned to a commit SHA", ci_5, older="should"),
    Rule("CI-6", "all", "should", "ratchet", "Every job sets a timeout", ci_6),
    Rule("CI-7", "all", "must", "auto", "Jobs name a runner from ci_runners", ci_7),
    Rule("CI-8", "all", "should", "auto", "Newer commit cancels the older run", ci_8),
    Rule("CI-9", "all", "must", "manual", "No test deleted or skipped to get green"),
    Rule("CI-10", "all", "must", "ratchet", "No job stops or borrows a process it did not start", ci_10),
    Rule("DEP-1", "all", "must", "auto", "One package manager, lockfile committed", dep_1),
    Rule("DEP-2", "all", "must", "auto", "CI installs from the lockfile", dep_2),
    Rule("DEP-3", "all", "must", "auto", "Packages under 24 hours old are not installed", dep_3),
    Rule("DEP-4", "all", "must", "manual", "No dependency change unasked"),
    Rule("DEP-5", "all", "must", "auto", "Automated dependency updates", dep_5, older="should"),
    Rule("DEP-6", "all", "should", "manual", "Downloaded tools are pinned and verified"),
    Rule("TEST-1", "all", "must", "auto", "Tests exist", test_1),
    Rule("TEST-2", "all", "must", "auto", "A coverage floor is configured", test_2),
    Rule("TEST-3", "all", "must", "auto", "Static types are strict", test_3, older="should"),
    Rule("TEST-4", "all", "must", "auto", "Lint and format checks run in CI", test_4, older="should"),
    Rule("TEST-5", "all", "should", "manual", "A bug fix ships with a failing-first test"),
    Rule("TEST-6", "all", "must", "manual", "Tests never reach a real service or real data"),
    Rule("SEC-1", "all", "must", "auto", "Secret scan in CI", sec_1),
    Rule("SEC-2", "all", "must", "manual", "No secret in the repository"),
    Rule("SEC-3", "all", "should", "manual", "Allowlist entries carry a reason"),
    Rule("SEC-4", "all", "should", "manual", "Destructive workflows need a typed confirmation"),
    Rule("SEC-5", "all", "must", "manual", "User content never goes to a translation service"),
    Rule("WORK-1", "all", "must", "manual", "Open work is a GitHub issue"),
    Rule("WORK-2", "all", "should", "ratchet", "Commit subjects: 10 to 120 characters", work_2),
    Rule("WORK-3", "all", "should", "manual", "A commit names its issue"),
    Rule("WORK-4", "all", "must", "auto", "No roadmap, to-do or backlog kept in a file", work_4),
    Rule("LOG-1", "all", "must", "manual", "No personal data or user content in logs or error reports"),
    Rule("LOG-2", "all", "must", "manual", "No secrets, tokens or session ids in logs"),
    Rule("LOG-3", "all", "must", "ratchet", "Outside logging services are named and approved per project", log_3),
    Rule("LOG-4", "all", "should", "manual", "An error is logged once, with a code and a request id"),
    Rule("LOG-5", "all", "should", "manual", "Logs are kept for a stated time, then deleted"),
    Rule("LAYOUT-1", "all", "must", "auto", "standard.json exists and is valid", layout_1),
    Rule("LAYOUT-2", "all", "must", "auto", "Exceptions are recorded in full", layout_2),
    Rule("LAYOUT-3", "no-web", "must", "auto", "Root left free for a future front end (new projects)", layout_3),
    Rule("LAYOUT-4", "all", "should", "auto", "Template version recorded", layout_4),
    Rule("OPS-1", "deployed", "must", "auto", "An address reports the running commit", ops_1),
    Rule("OPS-2", "deployed", "must", "manual", "No session, no access"),
    Rule("OPS-3", "deployed", "must", "manual", "A failed deploy check rolls back"),
    Rule("OPS-4", "deployed", "must", "manual", "Deploy waits for a person when it should"),
    Rule("OPS-5", "deployed", "must", "manual", "Contract changes are additive"),
    Rule("OPS-6", "service", "must", "auto", "api-requires.json states the needed revision", ops_6),
    Rule("LANE-1", "web", "must", "auto", "AGENTS.md has a section on lanes", lane_1),
    Rule("LANE-2", "web", "should", "auto", "AGENTS.md under 5,000 characters", lane_2),
    Rule("LANE-3", "web", "must", "auto", "Lanes and back-end shape are declared", lane_3),
    Rule("LANE-4", "web", "should", "ratchet", "The UI builder stays in its lanes", lane_4),
    Rule("LANE-5", "web", "must", "auto", "CI runs the lane guard", lane_5),
    Rule("LANE-6", "web", "must", "manual", "Tests travel with screens"),
    Rule("LANE-7", "web", "must", "manual", "The builder's branch is never rewritten or deleted"),
    Rule("LANE-8", "web", "must", "manual", "One issue per request, data first"),
    Rule("LANE-9", "web", "must", "manual", "The builder does only that issue"),
    Rule("LANE-10", "web", "must", "manual", "Own branch and guarded merge where the editor is used daily or main deploys itself"),
    Rule("LANE-11", "web", "must", "auto", "No file over 10 MB", lane_11),
    Rule("DATA-1", "web", "must", "ratchet", "No network call in the UI builder's files", data_1),
    Rule("DATA-2", "service", "must", "manual", "The app runs with no back end, on sample data"),
    Rule("DATA-3", "service", "must", "manual", "Sample data is checked against the contract"),
    Rule("DATA-4", "web", "must", "manual", "A 401 goes to sign-in, in one place"),
    Rule("DATA-5", "web", "must", "manual", "Errors are fixed sentences"),
    Rule("DATA-6", "web", "must", "ratchet", "No fonts, scripts or styles from another origin", data_6),
    Rule("DATA-7", "service", "must", "auto", "Lovable Cloud is off", data_7),
    Rule("CLOUD-1", "cloud", "must", "manual", "No schema or live-data change without an override"),
    Rule("CLOUD-2", "cloud", "must", "manual", "Migrations apply from an empty database in CI"),
    Rule("CLOUD-3", "cloud", "must", "manual", "Every new access policy ships with a test"),
    Rule("UI-1", "web", "must", "auto", "One component library (shadcn/ui)", ui_1),
    Rule("UI-2", "web", "must", "ratchet", "No raw controls outside the library", ui_2),
    Rule("UI-3", "web", "should", "ratchet", "No hex colours in screens", ui_3),
    Rule("UI-4", "web", "must", "manual", "No names or content in an address"),
    Rule("MOBILE-1", "web", "must", "auto", "Phone-size browser tests on Chromium and WebKit in CI", mobile_1),
    Rule("MOBILE-2", "web", "must", "manual", "Nothing scrolls sideways at 360 px"),
    Rule("MOBILE-3", "web", "must", "ratchet", "Heights use dvh, never vh or h-screen", mobile_3),
    Rule("MOBILE-4", "web", "should", "manual", "Phone Back closes the top dialog"),
    Rule("A11Y-1", "web", "must", "manual", "Target WCAG 2.2 AA, stated honestly"),
    Rule("A11Y-2", "web", "must", "auto", "Accessibility is measured against a baseline", a11y_2),
    Rule("A11Y-3", "web", "must", "auto", "A control list names the test for every control", a11y_3),
    Rule("A11Y-4", "web", "should", "manual", "Landmarks, heading, title, skip link, reduced motion"),
    Rule("A11Y-5", "web", "should", "manual", "Contrast from tokens meets 4.5 and 3"),
    Rule("A11Y-6", "web", "must", "manual", "No screenshots of real content; fixture data only"),
    Rule("A11Y-7", "web", "must", "manual", "Text for users reads at grade 11 or lower"),
    Rule("I18N-1", "web", "must", "auto", "The page declares its language", i18n_1),
    Rule("I18N-2", "web", "must", "manual", "Text is ready to translate", older="should"),
    Rule("VER-1", "app", "must", "auto", "The version is YYYY.M.N, in one file", ver_1, older="should"),
    Rule("VER-2", "app", "must", "manual", "A release is cut when there is something to tell"),
    Rule("VER-3", "app", "must", "manual", "Every build carries its full id"),
    Rule("VER-4", "app", "should", "manual", "CI tags each release"),
    Rule("VER-5", "split", "should", "manual", "Each repository of a split app has its own version"),
    Rule("UPD-2", "all", "must", "manual", "A weekly vulnerability scan; findings become issues"),
    Rule("UPD-3", "all", "must", "manual", "Security fixes within the time limits"),
    Rule("UPD-4", "all", "must", "manual", "Skipping the 24-hour wait needs the owner's word"),
    Rule("UPD-5", "deployed", "must", "manual", "Security fixes go through the same checks"),
    Rule("UPD-6", "all", "must", "manual", "A switched-off scan keeps a date to look again"),
    Rule("UPD-8", "public", "must", "manual", "SECURITY.md says how to report a problem"),
    Rule("BKP-1", "data", "must", "manual", "Everything that cannot be made again is listed with its backup"),
    Rule("BKP-2", "data", "must", "manual", "Backed up every day, files included"),
    Rule("BKP-3", "data", "must", "manual", "Backups are kept for a stated time"),
    Rule("BKP-4", "data", "must", "manual", "A copy outside the reach of one failure"),
    Rule("BKP-5", "data", "must", "manual", "Restores are rehearsed"),
    Rule("BKP-6", "data", "must", "manual", "A missed backup is noticed within a day"),
    Rule("BKP-7", "data", "must", "manual", "Backups are kept as carefully as the original"),
    Rule("BKP-8", "data", "must", "manual", "A checked backup before a migration that changes data"),
    Rule("BKP-9", "public+data", "must", "manual", "Deleted data leaves the backups when they expire"),
    Rule("BKP-10", "all", "must", "manual", "Git is not a backup for content"),
    Rule("AUTH-1", "accounts", "must", "manual", "Every address needs a sign-in unless listed as public"),
    Rule("AUTH-2", "accounts", "must", "manual", "Sign-in fails closed"),
    Rule("AUTH-3", "accounts", "must", "manual", "One sign-in module"),
    Rule("AUTH-4", "passwords", "must", "manual", "Passwords and tokens stored as slow hashes"),
    Rule("AUTH-5", "passwords", "must", "manual", "Failed sign-ins are slowed, then locked"),
    Rule("AUTH-6", "accounts", "must", "manual", "Sessions end on time and on a password change"),
    Rule("AUTH-7", "accounts", "must", "manual", "No open sign-up unless recorded"),
    Rule("AUTH-8", "public", "must", "manual", "HTTPS only, secure cookies"),
    Rule("AUTH-9", "public+accounts", "must", "manual", "Administrators use a second factor or a passkey"),
    Rule("AUTH-10", "accounts", "should", "manual", "People can end their sessions; tokens expire"),
    Rule("ROLE-1", "accounts", "must", "manual", "Refused unless allowed, from one permission table"),
    Rule("ROLE-2", "accounts", "must", "manual", "Checked on the server, never by hiding"),
    Rule("ROLE-3", "accounts", "must", "manual", "Roles in a table of their own, read through one function"),
    Rule("ROLE-4", "accounts", "must", "manual", "The minimum roles, mapped in the record"),
    Rule("ROLE-5", "accounts", "must", "manual", "The last administrator stays; role changes are recorded"),
    Rule("ROLE-6", "lan+accounts", "must", "manual", "Home app: an administrator's look into a silo is logged"),
    Rule("ROLE-7", "accounts", "must", "manual", "Nobody gives themselves a role"),
    Rule("ROLE-8", "accounts", "should", "manual", "Rights are read live"),
    Rule("ROLE-9", "public+accounts", "must", "manual", "Public app: administrators cannot see into a silo"),
    Rule("SILO-1", "accounts", "must", "manual", "The record names the silo"),
    Rule("SILO-2", "accounts", "must", "manual", "Everything stored belongs to one silo, enforced"),
    Rule("SILO-3", "accounts", "must", "manual", "Only members reach a silo"),
    Rule("SILO-4", "accounts", "must", "manual", "A cross-silo test for each kind of data"),
    Rule("SILO-5", "accounts", "must", "manual", "Nothing looks things up across silos"),
    Rule("SILO-6", "accounts", "must", "manual", "Test and demo environments have their own data"),
    Rule("SILO-7", "public+accounts", "must", "manual", "An owner can export and delete their silo"),
    Rule("SILO-8", "public+accounts", "should", "manual", "Shared machines are shared fairly"),
    Rule("AUDIT-1", "accounts", "must", "manual", "An audit log of what the table below lists"),
    Rule("AUDIT-2", "accounts", "must", "manual", "The audit log is append-only, tested"),
    Rule("AUDIT-3", "accounts", "must", "manual", "Audit rows hold ids, not names or emails"),
    Rule("AUDIT-4", "accounts", "must", "manual", "The audit log is never purged and is backed up"),
    Rule("AUDIT-5", "accounts", "should", "manual", "Administrators can read it in the app"),
    Rule("AUDIT-6", "public+accounts", "should", "manual", "A daily comparison with the last backup"),
    Rule("ADMIN-1", "public+accounts", "must", "manual", "An About page for administrators"),
    Rule("ADMIN-2", "accounts", "must", "manual", "Secrets shown only as present or a fingerprint"),
    Rule("ADMIN-3", "accounts", "must", "manual", "The copied report holds no personal data"),
    Rule("ADMIN-4", "split", "must", "manual", "A split app shows both halves' build ids"),
    Rule("RLS-1", "cloud", "must", "manual", "Row security on every table"),
    Rule("RLS-2", "cloud", "must", "manual", "Nothing granted to visitors unless listed"),
    Rule("RLS-3", "cloud", "must", "manual", "New functions are closed, then granted"),
    Rule("RLS-4", "cloud", "must", "manual", "The service key never reaches the browser"),
    Rule("RLS-5", "cloud", "must", "manual", "Stored files in private buckets with tested policies"),
    Rule("RLS-6", "cloud", "should", "manual", "Policies are fast"),
    Rule("RLS-7", "cloud", "must", "manual", "The platform's own schemas are never changed"),
    Rule("MOBILE-5", "web", "must", "auto", "The page fills the screen around the notch", mobile_5, older="should", blocks=False),
    Rule("MOBILE-6", "web", "must", "auto", "Text fields are 16 px on a phone", mobile_6, older="should", blocks=False),
    Rule("MOBILE-7", "web", "must", "auto", "Dialogs have a height limit and scroll inside it", mobile_7, older="should", blocks=False),
    Rule("MOBILE-8", "web", "should", "manual", "Tap targets 24 px at least, never under a fixed bar"),
    Rule("APP-1", "phone", "must", "auto", "The phone-app decisions are answered", app_1, older="should", blocks=False),
    Rule("APP-2", "phone", "must", "manual", "A release job fails on a missing secret"),
    Rule("APP-3", "phone", "must", "manual", "An emulator and simulator smoke test per release"),
    Rule("APP-4", "phone", "must", "manual", "Store forms and privacy files come from one data list"),
    Rule("APP-5", "phone", "must", "manual", "Signing keys have a copy outside git and GitHub"),
    Rule("HELP-1", "help", "must", "manual", "Help is Markdown, one file per role, shown in the app"),
    Rule("HELP-2", "help", "must", "manual", "The administrators' guide never reaches other browsers"),
    Rule("HELP-3", "help", "must", "manual", "Help for changed screens is reviewed at each release"),
    Rule("HELP-4", "help", "should", "manual", "Help is checked for reading level and stale names"),
    Rule("HELP-5", "help", "should", "manual", "Release notes go to the right readers"),
    Rule("PUB-1", "web", "must", "auto", "robots.txt exists and fits the exposure", pub_1, older="should", blocks=False),
    Rule("PUB-7", "web", "must", "manual", "Not published on the builder's hosting unless recorded"),
    Rule("PUB-2", "public", "should", "manual", "sitemap.xml: public pages for all, full list for the scanner"),
    Rule("PUB-3", "public", "must", "manual", "The API description needs a credential"),
    Rule("PUB-4", "public", "must", "manual", "security.txt with a contact and an expiry date"),
    Rule("PUB-5", "public", "must", "manual", "Hosts that are not production say noindex"),
    Rule("PUB-6", "public+passwords", "should", "manual", "A change-password address for password managers"),
    Rule("LEGAL-1", "public+accounts", "must", "manual", "Legal documents are versioned Markdown"),
    Rule("LEGAL-2", "public+accounts", "must", "manual", "An append-only acceptance history"),
    Rule("LEGAL-3", "public+accounts", "must", "manual", "Every binding document is on the gate"),
    Rule("LEGAL-4", "public+accounts", "must", "manual", "One function decides acceptance, used everywhere"),
    Rule("LEGAL-5", "public+accounts", "must", "manual", "The owner marks each new version material or not"),
    Rule("LEGAL-6", "public+accounts", "should", "manual", "A new version is tried on a demo first"),
    Rule("LEGAL-7", "public+accounts", "should", "manual", "Legal text enters through the import script"),
    Rule("CONSENT-1", "public", "must", "manual", "Nothing non-essential loads before consent"),
    Rule("CONSENT-2", "public", "must", "manual", "The choice is stored and every change audited"),
    Rule("CONSENT-3", "public", "must", "manual", "Withdrawing is as easy as agreeing"),
    Rule("CONSENT-4", "public", "must", "manual", "Strictly necessary services are recorded"),
    Rule("CONSENT-5", "public", "must", "manual", "One consent rule everywhere"),
    Rule("RET-1", "public+accounts", "must", "manual", "One retention table matches the privacy policy"),
    Rule("RET-2", "public+accounts", "must", "manual", "Every timer has a runbook row and an alarm"),
    Rule("RET-3", "public+accounts", "must", "manual", "Deleted data is unreadable, and gone after 30 days"),
    Rule("RET-4", "public+accounts", "must", "manual", "Deleting an account deletes everywhere"),
    Rule("RET-5", "public+accounts", "must", "manual", "A restore replays the deletion log; backups kept 90 days"),
    Rule("RET-6", "public+accounts", "must", "manual", "Timers never default to production"),
    Rule("RET-7", "public+accounts", "must", "manual", "Every account type can export and delete its data"),
    Rule("DEMO-1", "public+accounts", "must", "manual", "A demo has its own hosting and database"),
    Rule("DEMO-2", "public+accounts", "must", "manual", "One command builds a demo from main"),
    Rule("DEMO-3", "public+accounts", "must", "manual", "Demo data from named fixture states"),
    Rule("DEMO-4", "public+accounts", "must", "manual", "Outside services are off on a demo"),
    Rule("DEMO-5", "public+accounts", "should", "manual", "A demo is rebuilt after every migration"),
    Rule("DEMO-6", "public+accounts", "must", "manual", "A demo says noindex"),
    Rule("DAST-1", "public+accounts", "must", "manual", "Scans use a scan server with fixture data"),
    Rule("DAST-2", "public+accounts", "must", "manual", "One scan account per role, seeded and proved"),
    Rule("DAST-3", "public+accounts", "must", "manual", "The scanner's key works only in test mode"),
    Rule("DAST-4", "public+accounts", "should", "manual", "The app tells the scanner it is signed in"),
    Rule("DAST-5", "public+accounts", "must", "manual", "Actions that end the session are excluded"),
    Rule("DAST-6", "public+accounts", "must", "manual", "Never scan production or a published Lovable app"),
    Rule("DAST-7", "public+accounts", "must", "manual", "The scan server is rebuilt and ready before each scan"),
    Rule("DAST-8", "public+accounts", "must", "manual", "The scanner's addresses are let in on the scan host only"),
    Rule("DAST-9", "public+accounts", "must", "manual", "ZAP scans in CI on every release"),
    Rule("NET-1", "home", "must", "manual", "Every outside destination is listed in the record"),
    Rule("NET-2", "home", "must", "manual", "Nothing goes to an unlisted destination"),
    Rule("NET-3", "home", "must", "manual", "Built-in telemetry is switched off"),
    Rule("NET-4", "home", "must", "ratchet", "No unlisted outside destination in the code", net_4),
    Rule("NET-5", "home+web", "must", "manual", "The browser tests report the hosts the app contacts"),
    Rule("MOBILE-9", "web", "must", "manual", "Lists and grids show thumbnails, never originals"),
    Rule("MOBILE-10", "web", "should", "ratchet", "Pictures load lazily", mobile_10, blocks=False),
    Rule("MOBILE-11", "web", "must", "manual", "A part on every page fetches only what the viewer sees"),
    Rule("MOBILE-12", "web", "should", "manual", "The browser tests report each screen's weight"),
    Rule("DATA-8", "web", "must", "manual", "Paged lists are followed to the end"),
    Rule("DATA-9", "web", "should", "manual", "The data-fetching defaults are written down and tested"),
    Rule("SILO-9", "accounts", "must", "manual", "No row limit is shared across silos"),
    Rule("CI-11", "all", "must", "manual", "A merge waits for green checks; a red main comes first"),
    Rule("REPO-9", "all", "must", "manual", "Pull requests merge with a merge commit"),
    Rule("AGENT-6", "all", "must", "manual", "AI-written commits end with a co-author line"),
    Rule("TMP-1", "all", "must", "manual", "The project states where it writes"),
    Rule("TMP-2", "all", "must", "ratchet", "No fixed paths in /tmp", tmp_2),
    Rule("TMP-3", "all", "should", "manual", "A cache has a size or age limit"),
    Rule("TMP-4", "all", "must", "manual", "CI scratch goes to RUNNER_TEMP"),
    Rule("TMP-5", "all", "must", "auto", "The checks leave the checkout clean", tmp_5, older="should"),
    Rule("TMP-6", "all", "must", "manual", "An agent's scratch stays out of the repository"),
    Rule("WORK-5", "all", "should", "manual", "A friction log, added to at the end of each piece of work"),
    Rule("WORK-6", "all", "should", "manual", "Repeated friction becomes a tool proposal"),
    Rule("WORK-7", "all", "should", "manual", "A friction fix is dated and measured again"),
    Rule("WORK-8", "all", "should", "manual", "Branch names start with the issue number"),
    Rule("LAYOUT-6", "all", "should", "auto", "The category agrees with the record's facts", layout_6),
    Rule("LAYOUT-5", "all", "must", "auto", "The record states exposure, accounts and phone plans", layout_5, older="should"),
    Rule("CONST-1", "app", "must", "manual", "Limits and durations live in one module"),
    Rule("CONST-2", "app", "must", "manual", "Host names live in one file"),
    Rule("CONST-3", "app", "must", "manual", "Every free-text field has a length limit"),
    Rule("CONST-4", "web", "should", "manual", "Text shown to people follows I18N-2"),
    Rule("INVITE-1", "accounts", "must", "manual", "The record says who may invite whom"),
    Rule("INVITE-2", "accounts", "must", "manual", "Invitation rights are enforced and tested"),
    Rule("INVITE-3", "accounts", "must", "manual", "A silo owner invites into their own silo only"),
    Rule("INVITE-4", "public+accounts", "must", "manual", "Invitations per inviter are limited per day"),
    Rule("INVITE-5", "accounts", "must", "manual", "An invitation grants nothing until accepted"),
    Rule("INVITE-6", "accounts", "must", "manual", "Invitations expire after 14 days by default"),
    Rule("INVITE-7", "accounts", "must", "manual", "The inviter is told the truth"),
    Rule("INVITE-8", "accounts", "must", "manual", "Invitations are in the audit log"),
    Rule("USERS-1", "accounts", "must", "manual", "A users page with search, filters and paging"),
    Rule("USERS-2", "accounts", "must", "manual", "Last sign-in is recorded without consent"),
    Rule("USERS-3", "accounts", "must", "manual", "Stale accounts are listed, never disabled automatically"),
    Rule("USERS-4", "accounts", "must", "manual", "Administrators manage accounts from the page"),
    Rule("USERS-5", "accounts", "must", "manual", "Each account's audit history; view-only cannot change"),
    Rule("NOTIFY-1", "notifications", "must", "manual", "One list of notification kinds"),
    Rule("NOTIFY-2", "notifications", "must", "manual", "Per-kind switches and one pause for all"),
    Rule("NOTIFY-3", "notifications", "must", "manual", "Permission is asked only after the person acts"),
    Rule("NOTIFY-4", "notifications", "must", "manual", "A push or text says what happened, not the content"),
    Rule("NOTIFY-5", "notifications", "must", "manual", "Failed deliveries are retried and raise an alarm"),
    Rule("NOTIFY-6", "notifications", "must", "manual", "Marketing is opt-in and stops with one click"),
    Rule("NOTIFY-7", "notifications", "must", "manual", "Text messages only with the owner's approval"),
    Rule("NOTIFY-8", "notifications", "should", "manual", "Phone browser and phone app share the switches"),
    Rule("LIC-1", "licensing", "must", "manual", "One licence record per silo"),
    Rule("LIC-2", "licensing", "must", "manual", "Every limit is listed and enforced on the server"),
    Rule("LIC-3", "licensing", "must", "manual", "Read-only at expiry, deleted after a stated grace"),
    Rule("LIC-4", "licensing", "must", "manual", "Silo owners are warned before expiry"),
    Rule("LIC-5", "licensing", "must", "manual", "Licence changes are audited"),
    Rule("LIC-6", "licensing", "must", "manual", "Declined card, expiry and purge are tested first"),
    Rule("PREVIEW-1", "previews", "must", "manual", "Preview features are listed with an owner"),
    Rule("PREVIEW-2", "previews", "must", "manual", "A preview is on only when made available and opted into"),
    Rule("PREVIEW-3", "previews", "must", "manual", "Consent before a preview sends data out"),
    Rule("PREVIEW-4", "previews", "must", "manual", "Every preview switch is audited"),
    Rule("PREVIEW-5", "previews", "should", "manual", "Called \"Preview\" everywhere"),
    Rule("CONFIG-1", "accounts", "must", "manual", "Changeable values are listed with bounds"),
    Rule("CONFIG-2", "accounts", "must", "manual", "Bounds enforced on the server, copies tested"),
    Rule("CONFIG-3", "accounts", "must", "manual", "A bad stored value falls back to the default"),
    Rule("CONFIG-4", "accounts", "must", "manual", "Every change audited, full history shown"),
    Rule("CONFIG-5", "accounts", "must", "manual", "New keys are secret until listed as readable"),
    Rule("CONFIG-6", "accounts", "should", "manual", "An off switch for each outside service"),
    Rule("CONFIG-7", "accounts", "should", "manual", "The page says when a change takes effect"),
    Rule("CONFIG-8", "public+accounts", "must", "manual", "Retention settings only shorten the policy"),
]

# The version of the standard that added each rule, for `--to-judge --since` (what a steward judges after an
# update). A test keeps it complete: a new rule goes under the version that adds it.
RULE_SINCE: Dict[str, str] = {
    "0.1": (
        "REPO-1 REPO-2 REPO-3 REPO-4 REPO-5 REPO-6 REPO-7 REPO-8 AGENT-1 AGENT-2 AGENT-3 AGENT-4 AGENT-5 CI-1 "
        "CI-2 CI-3 CI-4 CI-5 CI-6 CI-7 CI-8 CI-9 CI-10 DEP-1 DEP-2 DEP-3 DEP-4 DEP-5 DEP-6 TEST-1 TEST-2 "
        "TEST-3 TEST-4 TEST-5 TEST-6 SEC-1 SEC-2 SEC-3 SEC-4 SEC-5 WORK-1 WORK-2 WORK-3 WORK-4 LOG-1 LOG-2 "
        "LOG-3 LOG-4 LOG-5 LAYOUT-1 LAYOUT-2 LAYOUT-3 LAYOUT-4 OPS-1 OPS-2 OPS-3 OPS-4 OPS-5 OPS-6 LANE-1 "
        "LANE-2 LANE-3 LANE-4 LANE-5 LANE-6 LANE-7 LANE-8 LANE-9 LANE-10 LANE-11 DATA-1 DATA-2 DATA-3 DATA-4 "
        "DATA-5 DATA-6 DATA-7 CLOUD-1 CLOUD-2 CLOUD-3 UI-1 UI-2 UI-3 UI-4 MOBILE-1 MOBILE-2 MOBILE-3 MOBILE-4 "
        "A11Y-1 A11Y-2 A11Y-3 A11Y-4 A11Y-5 A11Y-6 I18N-1 I18N-2"
    ),
    "0.2": (
        "VER-1 VER-2 VER-3 VER-4 VER-5 UPD-2 UPD-3 UPD-4 UPD-5 UPD-6 UPD-8 BKP-1 BKP-2 BKP-3 BKP-4 BKP-5 "
        "BKP-6 BKP-7 BKP-8 BKP-9 BKP-10 AUTH-1 AUTH-2 AUTH-3 AUTH-4 AUTH-5 AUTH-6 AUTH-7 AUTH-8 AUTH-9 "
        "AUTH-10 ROLE-1 ROLE-2 ROLE-3 ROLE-4 ROLE-5 ROLE-6 ROLE-7 ROLE-8 ROLE-9 SILO-1 SILO-2 SILO-3 SILO-4 "
        "SILO-5 SILO-6 SILO-7 SILO-8 AUDIT-1 AUDIT-2 AUDIT-3 AUDIT-4 AUDIT-5 AUDIT-6 ADMIN-1 ADMIN-2 ADMIN-3 "
        "ADMIN-4 RLS-1 RLS-2 RLS-3 RLS-4 RLS-5 RLS-6 RLS-7 MOBILE-5 MOBILE-6 MOBILE-7 MOBILE-8 APP-1 APP-2 "
        "APP-3 APP-4 APP-5 HELP-1 HELP-2 HELP-3 HELP-4 HELP-5 PUB-1 PUB-7 PUB-2 PUB-3 PUB-4 PUB-5 PUB-6 "
        "LEGAL-1 LEGAL-2 LEGAL-3 LEGAL-4 LEGAL-5 LEGAL-6 LEGAL-7 CONSENT-1 CONSENT-2 CONSENT-3 CONSENT-4 "
        "CONSENT-5 RET-1 RET-2 RET-3 RET-4 RET-5 RET-6 RET-7 DEMO-1 DEMO-2 DEMO-3 DEMO-4 DEMO-5 DEMO-6 DAST-1 "
        "DAST-2 DAST-3 DAST-4 DAST-5 DAST-6 DAST-7 DAST-8 DAST-9 LAYOUT-5"
    ),
    "0.3": (
        "CONST-1 CONST-2 CONST-3 CONST-4 INVITE-1 INVITE-2 INVITE-3 INVITE-4 INVITE-5 INVITE-6 INVITE-7 "
        "INVITE-8 USERS-1 USERS-2 USERS-3 USERS-4 USERS-5 NOTIFY-1 NOTIFY-2 NOTIFY-3 NOTIFY-4 NOTIFY-5 "
        "NOTIFY-6 NOTIFY-7 NOTIFY-8 LIC-1 LIC-2 LIC-3 LIC-4 LIC-5 LIC-6 PREVIEW-1 PREVIEW-2 PREVIEW-3 "
        "PREVIEW-4 PREVIEW-5 CONFIG-1 CONFIG-2 CONFIG-3 CONFIG-4 CONFIG-5 CONFIG-6 CONFIG-7 CONFIG-8"
    ),
    "0.4": (
        "A11Y-7"
    ),
    "0.5": (
        "LAYOUT-6"
    ),
    "0.6": (
        "NET-1 NET-2 NET-3 NET-4 NET-5 TMP-1 TMP-2 TMP-3 TMP-4 TMP-5 TMP-6 WORK-5 WORK-6 WORK-7 WORK-8"
    ),
    "0.7": (
        "MOBILE-9 MOBILE-10 MOBILE-11 MOBILE-12 DATA-8 DATA-9 SILO-9 CI-11"
    ),
    "1.0": (
        "REPO-9 AGENT-6"
    ),
}



# --------------------------------------------------------------------------
# Running the rules


@dataclass
class Finding:
    rule: Rule
    status: str
    detail: str
    count: Optional[int] = None
    allowed: Optional[int] = None


def load_baseline(path: Path) -> Dict[str, int]:
    if not path.is_file():
        return {}
    try:
        counts = json.loads(path.read_text(encoding="utf-8")).get("counts", {})
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(counts, dict):
        return {}
    return {str(k): v for k, v in counts.items() if isinstance(v, int) and not isinstance(v, bool)}


def load_held(path: Path) -> List[str]:
    """Rules that already failed when the project adopted the kit: reported, never blocking ("What may block")."""
    try:
        held = json.loads(path.read_text(encoding="utf-8")).get("held", []) if path.is_file() else []
    except (OSError, ValueError, AttributeError):
        return []
    return [str(rule) for rule in held] if isinstance(held, list) else []


def evaluate(repo: Repo, baseline: Dict[str, int]) -> List[Finding]:
    excepted, _ = read_exceptions(repo)
    attested = {
        entry["rule"]: entry
        for entry in repo.record.get("attested", [])
        if isinstance(entry, dict) and isinstance(entry.get("rule"), str)
    } if isinstance(repo.record.get("attested", []), list) else {}

    findings = []
    if not repo.in_scope:
        category = repo.record.get("category")
        return [Finding(rule, "na", f"out of scope: category {category}") for rule in RULES]
    for rule in RULES:
        if rule.older and repo.record.get("predates_standard") is True:
            rule = replace(rule, level=rule.older)
        if not applies_to(rule, repo):
            findings.append(Finding(rule, "na", "does not apply"))
            continue
        if rule.kind == "manual" or rule.check is None:
            entry = attested.get(rule.id)
            if entry and entry.get("result") in ("pass", "fail"):
                note = f"attested by {entry.get('by', '?')} on {entry.get('date', '?')}"
                finding = Finding(rule, entry["result"], note)
            else:
                finding = Finding(rule, "manual", "not assessed")
        else:
            result = rule.check(repo)
            if result.status == "count":
                allowed = baseline.get(rule.id, 0)
                count = result.count or 0
                status = "pass" if count <= allowed else "fail"
                note = f"count {count}, allowed {allowed}"
                if result.detail:
                    note += f" ({result.detail})"
                finding = Finding(rule, status, note, count, allowed)
            else:
                finding = Finding(rule, result.status, result.detail)
        if finding.status == "fail" and rule.id in excepted:
            finding = Finding(rule, "excepted", excepted[rule.id], finding.count, finding.allowed)
        findings.append(finding)
    return findings


def summarise(findings: List[Finding]) -> Dict[str, Optional[int]]:
    musts = [f for f in findings if f.rule.level == "must" and f.status != "na"]
    passed = sum(1 for f in musts if f.status == "pass")
    failed = sum(1 for f in musts if f.status == "fail")
    scored = passed + failed
    return {
        "must_pass": passed,
        "must_fail": failed,
        "must_excepted": sum(1 for f in musts if f.status == "excepted"),
        "must_not_assessed": sum(1 for f in musts if f.status == "manual"),
        "should_fail": sum(1 for f in findings if f.rule.level == "should" and f.status == "fail"),
        "score": round(100 * passed / scored) if scored else None,
    }


# --------------------------------------------------------------------------
# Output

MARK = {"pass": "pass", "fail": "FAIL", "manual": "not assessed", "excepted": "excepted", "na": "n/a"}


def as_json(repo: Repo, findings: List[Finding]) -> str:
    return json.dumps(
        {
            "standard_version": STANDARD_VERSION,
            "layers": repo.layers,
            "back_end": repo.back_end,
            "recorded": repo.record_error is None,
            "summary": summarise(findings),
            "rules": [
                {
                    "id": f.rule.id,
                    "level": f.rule.level,
                    "kind": f.rule.kind,
                    "title": f.rule.title,
                    "status": f.status,
                    "detail": f.detail,
                    "count": f.count,
                    "allowed": f.allowed,
                }
                for f in findings
            ],
        },
        indent=2,
    )


def as_text(repo: Repo, findings: List[Finding]) -> str:
    lines = [f"Standard {STANDARD_VERSION}: layers {', '.join(repo.layers)}; back end {repo.back_end}"]
    for status, heading in (("fail", "Failed"), ("excepted", "Excepted"), ("manual", "Not assessed (manual)")):
        chosen = [f for f in findings if f.status == status]
        if not chosen:
            continue
        lines.append(f"\n{heading}:")
        for f in chosen:
            lines.append(f"  {f.rule.id:<9} {f.rule.level:<6} {f.rule.title}" + (f": {f.detail}" if status != "manual" else ""))
    summary = summarise(findings)
    score = "no scored rules" if summary["score"] is None else f"{summary['score']}%"
    lines.append(
        f"\nMust rules: {summary['must_pass']} passed, {summary['must_fail']} failed, "
        f"{summary['must_excepted']} excepted, {summary['must_not_assessed']} not assessed. "
        f"Should rules failed: {summary['should_fail']}. Score: {score}."
    )
    return "\n".join(lines)


def as_markdown(repo: Repo, findings: List[Finding]) -> str:
    summary = summarise(findings)
    score = "none" if summary["score"] is None else f"{summary['score']}%"
    lines = [
        f"Layers: {', '.join(repo.layers)}. Back end: {repo.back_end}. "
        f"Record: {'present' if repo.record_error is None else 'none, defaults used'}.",
        "",
        f"**Score: {score}** ({summary['must_pass']} of {(summary['must_pass'] or 0) + (summary['must_fail'] or 0)} "
        f"scored must rules pass; {summary['must_not_assessed']} manual rules not assessed; "
        f"{summary['should_fail']} should rules failed).",
        "",
        "| ID | Level | Check | Result | Detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    for f in findings:
        if f.status == "na":
            continue
        detail = f.detail.replace("|", "\\|")
        lines.append(f"| {f.rule.id} | {f.rule.level} | {f.rule.kind} | {MARK[f.status]} | {detail} |")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# The lane guard: one question about one range of commits


def lane_guard(repo: Repo, revision: str) -> int:
    commits = builder_commits(repo, [revision])
    if commits is None:
        print(
            f"lane guard: cannot read the commits `{revision}` (not a git repository, an unknown range, "
            "or a shallow clone). Nothing was checked.",
            file=sys.stderr,
        )
        return 2
    strays = out_of_lane(repo, commits)
    overridden = out_of_lane(repo, commits, "override")
    if overridden:
        print("lane guard: the UI builder changed files it may change only on the owner's override:")
        for sha, path in overridden:
            print(f"  {sha}  {path}")
        print("Not held: the guard cannot see what the owner typed. Review these if no override was given.")
    if not strays:
        print(f"lane guard: {len(commits)} commits by the UI builder, none in a path closed to it")
        return 0
    print("lane guard: the UI builder changed files that are closed to it:")
    for sha, path in strays:
        print(f"  {sha}  {path}")
    print("No override opens these paths. Hold the deploy and review the change.")
    return 1


# browser tests in report mode ------------------------------------------------

BROWSER_KEY = "browser_failures"


def _failed_specs(suite: Any, found: List[str]) -> None:
    if not isinstance(suite, dict):
        return
    for spec in suite.get("specs", []) or []:
        if isinstance(spec, dict) and spec.get("ok") is False:
            found.append(f"{spec.get('file', '?')}: {spec.get('title', '?')}")
    for inner in suite.get("suites", []) or []:
        _failed_specs(inner, found)


def _version_key(version: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def rule_since(rule_id: str) -> Optional[str]:
    """The version of the standard that added a rule, or None for an unknown rule."""
    for version, ids in RULE_SINCE.items():
        if rule_id in ids.split():
            return version
    return None


def to_judge(repo: Repo, since: Optional[str]) -> int:
    """List the manual rules that apply here and have no recorded result; with `since`, only those added after it."""
    findings = evaluate(repo, {})
    waiting = [
        f for f in findings
        if f.status == "manual"
        and (since is None or _version_key(rule_since(f.rule.id) or "0") > _version_key(since))
    ]
    scope = f"added after standard {since}" if since else "in all"
    if not waiting:
        print(f"Nothing to judge ({scope}).")
        return 0
    print(f"{len(waiting)} manual rules to judge ({scope}). Record each with:")
    print('  python3 scripts/standard_check.py --attest RULE pass|fail "evidence: file and line, a test, or a document" --by NAME')
    for f in waiting:
        print(f"  {f.rule.id:10} {f.rule.level:6} {f.rule.title}  [since {rule_since(f.rule.id) or '?'}]")
    return 0


def _json_span(text: str, start: int) -> int:
    """The index just past the JSON object or array that opens at `start`, skipping strings."""
    depth = 0
    i = start
    in_string = False
    while i < len(text):
        char = text[i]
        if in_string:
            if char == "\\":
                i += 1
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced JSON")


def _record_entry(record_path: Path, field: str, rule_id: str, entry: Dict[str, str]) -> int:
    """Put `entry` in the list `field` of standard.json, replacing that rule's entry; only that text changes."""
    text = record_path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        print(f"{record_path} is not a JSON object", file=sys.stderr)
        return 2
    key = re.search(r'"' + field + r'"\s*:\s*\[', text)
    if key:
        open_at = key.end() - 1
        close_at = _json_span(text, open_at) - 1
        body = text[open_at + 1:close_at]
        indent = re.search(r"\n([ \t]*)\{", body)
        pad = indent.group(1) if indent else re.match(r"[ \t]*", text[text.rfind("\n", 0, key.start()) + 1:]).group(0) + "  "  # type: ignore[union-attr]
        rendered = json.dumps(entry, indent=2, ensure_ascii=False).replace("\n", "\n" + pad)
        objects = []
        i = 0
        while True:
            found = body.find("{", i)
            if found < 0:
                break
            stop = _json_span(body, found)
            objects.append((found, stop))
            i = stop
        old = next((span for span in objects if json.loads(body[span[0]:span[1]]).get("rule") == rule_id), None)
        if old:
            body = body[:old[0]] + rendered + body[old[1]:]
        elif objects:
            last = objects[-1][1]
            body = body[:last] + ",\n" + pad + rendered + body[last:]
        else:
            outer = pad[:-2] if len(pad) >= 2 else ""
            body = "\n" + pad + rendered + "\n" + outer
        new_text = text[:open_at + 1] + body + text[close_at:]
    else:
        last_brace = text.rstrip().rfind("}")
        before = text[:last_brace].rstrip()
        rendered = json.dumps(entry, indent=2, ensure_ascii=False).replace("\n", "\n    ")
        new_text = before + ',\n  "' + field + '": [\n    ' + rendered + "\n  ]\n" + text[last_brace:]
    check = json.loads(new_text)
    held = [e for e in check.get(field, []) if isinstance(e, dict) and e.get("rule") == rule_id]
    if held != [entry] or {k: v for k, v in check.items() if k != field} != {k: v for k, v in data.items() if k != field}:
        print("the edit did not come out as intended; nothing was written", file=sys.stderr)
        return 2
    record_path.write_text(new_text, encoding="utf-8")
    return 0




def attest(record_path: Path, rule_id: str, result: str, evidence: str, by: str) -> int:
    """Record a manual rule's result in standard.json, changing only that entry's text (the rest keeps its layout)."""
    if rule_id not in {rule.id for rule in RULES}:
        print(f"unknown rule: {rule_id}", file=sys.stderr)
        return 2
    if result not in ("pass", "fail"):
        print(f"the result is pass or fail, not {result}", file=sys.stderr)
        return 2
    if not evidence.strip() or not by.strip():
        print("evidence and --by are required: a result without them cannot be checked", file=sys.stderr)
        return 2
    entry = {"rule": rule_id, "result": result, "by": by, "date": datetime.date.today().isoformat(), "evidence": evidence}
    if _record_entry(record_path, "attested", rule_id, entry):
        return 2
    print(f"recorded {rule_id} {result} by {by} in {record_path.name}")
    return 0


def waive(record_path: Path, rule_id: str, reason: str, approved_by: str, date: str) -> int:
    """Record the owner's waiver of a rule under `exceptions` in standard.json, changing nothing else.

    A waiver is the owner's decision: `approved_by` names him and `date` is the day he decided, never the day it is
    written down. It covers a rule while that rule fails (a manual rule: once its fail is recorded with --attest).
    """
    if rule_id not in {rule.id for rule in RULES}:
        print(f"unknown rule: {rule_id}", file=sys.stderr)
        return 2
    if rule_id in NEVER_EXCEPTED:
        print(f"{rule_id} cannot be excepted", file=sys.stderr)
        return 2
    if not reason.strip() or not approved_by.strip() or not re.match(r"\d{4}-\d{2}-\d{2}\Z", date):
        print("a reason, --approved-by and --date YYYY-MM-DD (the day the owner decided) are required", file=sys.stderr)
        return 2
    entry = {"rule": rule_id, "reason": reason, "approved_by": approved_by, "date": date}
    if _record_entry(record_path, "exceptions", rule_id, entry):
        return 2
    print(f"recorded the waiver of {rule_id} by {approved_by} ({date}) in {record_path.name}")
    return 0


def weight_report(report: Path, top: int = 10) -> int:
    """Print the heaviest screens the browser tests loaded, by bytes downloaded (MOBILE-12).

    It reports and never fails the run: the owner chose to start with reporting and set a ceiling later (2026-10-13).
    """
    try:
        lines = report.read_text(encoding="utf-8").splitlines() if report.is_file() else []
    except OSError as error:
        print(f"cannot read {report}: {error}")
        return 0
    heaviest: Dict[Tuple[str, str], Tuple[int, int]] = {}
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("bytes"), int) or isinstance(entry.get("bytes"), bool):
            continue
        key = (str(entry.get("test", "")), str(entry.get("project", "")))
        size = int(entry["bytes"])
        requests = entry.get("requests")
        if size > heaviest.get(key, (-1, 0))[0]:
            heaviest[key] = (size, requests if isinstance(requests, int) else 0)
    if not heaviest:
        print("Page weight (MOBILE-12): no measurements (no browser tests ran, or the kit's fixture is missing).")
        return 0
    ranked = sorted(heaviest.items(), key=lambda item: -item[1][0])
    total = sum(size for size, _ in heaviest.values())
    print(f"Page weight (MOBILE-12): {len(heaviest)} tests, {total / 1_000_000:.1f} MB in all. The heaviest:")
    for (test, project), (size, requests) in ranked[:top]:
        where = f" [{project}]" if project else ""
        print(f"  {size / 1_000_000:7.2f} MB  {requests:4d} requests  {test}{where}")
    print("Reported only: this does not fail the run.")
    return 0


def outbound_report(repo: Repo, report: Path) -> int:
    """Print the outside hosts the browser tests saw the app contact that `outbound` does not list (NET-5).

    It reports and never fails the run: the owner chose to start with reporting (2026-10-13).
    """
    hosts, _ = approved_outbound(repo)
    seen: Dict[str, str] = {}
    try:
        lines = report.read_text(encoding="utf-8").splitlines() if report.is_file() else []
    except OSError as error:
        print(f"cannot read {report}: {error}")
        return 0
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        host = str(entry.get("host", "")).lower() if isinstance(entry, dict) else ""
        if host and not NOT_OUTBOUND.search(host) and host not in seen:
            seen[host] = str(entry.get("test", ""))
    unlisted = {h: t for h, t in seen.items() if h not in hosts and not any(h.endswith("." + a) for a in hosts)}
    if not unlisted:
        print(f"Outbound (NET-5): the browser tests contacted no unlisted host ({len(seen)} listed or none).")
        return 0
    print("Outbound (NET-5): the browser tests saw the app contact hosts that `outbound` does not list:")
    for host, test in sorted(unlisted.items()):
        print(f"  {host}  (first in: {test})")
    print("Reported only: this does not fail the run.")
    return 0


def browser_report(repo: Repo, report: Path, baseline_path: Path, write: bool) -> int:
    """Judge a Playwright JSON report by the project's mode.

    `enforce` (the default): any failing test fails the run. `report`: failing
    tests are counted, and the run fails only when the count is above the
    ceiling recorded in the baseline file.
    """
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
        failing = data["stats"]["unexpected"]
        if not isinstance(failing, int) or isinstance(failing, bool):
            raise ValueError("stats.unexpected is not a number")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"browser tests: cannot read the report {report}: {error}. Nothing was judged.", file=sys.stderr)
        return 2
    names: List[str] = []
    for suite in data.get("suites", []) or []:
        _failed_specs(suite, names)

    try:
        stored = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else {}
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    if write:
        stored.setdefault("standard_version", STANDARD_VERSION)
        stored.setdefault("counts", {})
        stored[BROWSER_KEY] = failing
        try:
            baseline_path.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
        except OSError as error:
            print(f"cannot write {baseline_path}: {error}", file=sys.stderr)
            return 2
        print(f"wrote {baseline_path}: the ceiling for failing browser tests is {failing}")
        return 0

    mode = repo.record.get("browser_tests", "enforce")
    ceiling = stored.get(BROWSER_KEY, 0)
    if not isinstance(ceiling, int) or isinstance(ceiling, bool):
        ceiling = 0
    lines = [f"Browser tests: {failing} failing."]
    if mode != "report":
        code = 1 if failing else 0
    else:
        lines[0] = f"Browser tests (report mode): {failing} failing, ceiling {ceiling}."
        code = 1 if failing > ceiling else 0
        if code:
            lines.append("More tests fail than the ceiling allows: a change broke something new.")
        elif failing < ceiling:
            lines.append(f"Fewer fail than the ceiling. Lower it to {failing}: rerun with --write-baseline and commit the file.")
    lines += [f"- {name}" for name in names[:50]]
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except OSError:
            pass
    return code


# --------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    try:
        return run(argv)
    except Exception as error:  # a crash must not look like a result
        import traceback

        traceback.print_exc()
        print(f"standard_check.py failed: {error}", file=sys.stderr)
        return 2


def run(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score a repository against the project standard.")
    parser.add_argument("--repo", default=".", help="repository to check (default: the current folder)")
    parser.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    parser.add_argument("--baseline", help=f"baseline file (default: {BASELINE_NAME} in the repository)")
    parser.add_argument("--write-baseline", action="store_true", help="record today's ratchet counts and exit")
    parser.add_argument(
        "--extend-baseline",
        action="store_true",
        help="record today's count for counted rules the baseline does not hold yet (a rule that a newer version of "
        "the standard added); counts already recorded are left as they are",
    )
    parser.add_argument("--report-only", action="store_true", help="always exit 0")
    parser.add_argument("--record", help="read the project's facts from this file instead of its standard.json")
    parser.add_argument("--lane-guard", metavar="RANGE", help="check only the UI builder's commits in a git range")
    parser.add_argument("--browser-report", metavar="FILE", help="judge a Playwright JSON report by the project's mode")
    parser.add_argument("--outbound-report", metavar="FILE", help="list unlisted hosts the browser tests contacted (NET-5)")
    parser.add_argument("--weight-report", metavar="FILE", help="list the heaviest screens the browser tests loaded (MOBILE-12)")
    parser.add_argument("--to-judge", action="store_true", help="list the manual rules here that have no recorded result")
    parser.add_argument("--since", metavar="VERSION", help="with --to-judge: only rules added after this standard version")
    parser.add_argument(
        "--attest", nargs=3, metavar=("RULE", "RESULT", "EVIDENCE"),
        help="record a manual rule's result (pass or fail) and its evidence in standard.json, changing nothing else",
    )
    parser.add_argument("--by", help="with --attest: who judged it")
    parser.add_argument(
        "--waive", nargs=2, metavar=("RULE", "REASON"),
        help="record the owner's waiver of a rule under exceptions in standard.json, changing nothing else",
    )
    parser.add_argument("--approved-by", help="with --waive: who waived it (the owner)")
    parser.add_argument("--date", help="with --waive: the day the owner decided, YYYY-MM-DD")
    parser.add_argument(
        "--files",
        choices=("builder", "other"),
        help="list the files the UI builder writes (its screens and their tests), or every other file, one per line",
    )
    args = parser.parse_args(argv)

    root = Path(args.repo).resolve()
    if not root.is_dir():
        print(f"not a folder: {root}", file=sys.stderr)
        return 2
    try:
        repo = Repo(root, Path(args.record) if args.record else None)
    except (OSError, ValueError) as error:
        print(f"cannot read the record {args.record}: {error}", file=sys.stderr)
        return 2

    if args.attest:
        rule_id, result, evidence = args.attest
        return attest(Path(args.record) if args.record else root / "standard.json", rule_id, result, evidence, args.by or "")
    if args.waive:
        rule_id, reason = args.waive
        return waive(Path(args.record) if args.record else root / "standard.json", rule_id, reason,
                     args.approved_by or "", args.date or "")
    if args.to_judge:
        return to_judge(repo, args.since)
    if args.lane_guard:
        return lane_guard(repo, args.lane_guard)
    if args.files:
        lanes = repo.lanes
        screens = lanes.get("builder", []) + lanes.get("builder_tests", [])
        closed = lanes.get("shared", []) + lanes.get("server", [])
        for name in repo.files:
            builders = repo.is_web and matches_any(name, screens) and not matches_any(name, closed)
            if builders == (args.files == "builder"):
                print(name)
        return 0

    baseline_path = Path(args.baseline) if args.baseline else root / BASELINE_NAME
    if args.outbound_report:
        return outbound_report(repo, Path(args.outbound_report))
    if args.weight_report:
        return weight_report(Path(args.weight_report))
    if args.browser_report:
        return browser_report(repo, Path(args.browser_report), baseline_path, args.write_baseline)
    if args.extend_baseline:
        if not baseline_path.is_file():
            # Without a baseline every count is allowed 0, so on a project whose checks passed before the update,
            # a count that blocks can only come from a rule new in this version: hold those at today's figure.
            # A should never blocks; it stays a reported failure rather than being hidden in a new baseline.
            found = {
                f.rule.id: f.count
                for f in evaluate(repo, {})
                if f.rule.kind == "ratchet" and f.count and f.status == "fail" and f.rule.level == "must" and f.rule.blocks
            }
            if found:
                started = {"standard_version": STANDARD_VERSION, "counts": found}
                baseline_path.write_text(json.dumps(started, indent=2) + "\n", encoding="utf-8")
            print("started the baseline with: " + (", ".join(f"{rule} {count}" for rule, count in sorted(found.items())) or "nothing"))
            return 0
        try:
            stored = json.loads(baseline_path.read_text(encoding="utf-8"))
            held = stored["counts"]
            if not isinstance(held, dict):
                raise ValueError("counts is not an object")
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"cannot read {baseline_path}: {error}", file=sys.stderr)
            return 2
        added = {
            f.rule.id: f.count
            for f in evaluate(repo, {})
            if f.rule.kind == "ratchet" and f.count is not None and f.rule.id not in held
        }
        if added:
            held.update(added)
        if added or stored.get("standard_version") != STANDARD_VERSION:
            stored["standard_version"] = STANDARD_VERSION  # the baseline now holds this version's counts
            baseline_path.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
        print("added to the baseline: " + (", ".join(f"{rule} {count}" for rule, count in sorted(added.items())) or "nothing"))
        return 0
    if args.write_baseline:
        counts = {
            f.rule.id: f.count
            for f in evaluate(repo, {})
            if f.rule.kind == "ratchet" and f.count is not None
        }
        content: Dict[str, Any] = {"standard_version": STANDARD_VERSION, "counts": counts}
        # A rule the checker decides that fails today is held too: on adoption it was already failing, so it is
        # reported and does not block until it is put right ("What may block").
        failing = sorted(
            f.rule.id for f in evaluate(repo, counts)
            if f.rule.kind == "auto" and f.rule.level == "must" and f.status == "fail" and f.rule.blocks
        )
        if failing:
            content["held"] = failing
        try:
            kept = json.loads(baseline_path.read_text(encoding="utf-8")).get(BROWSER_KEY)
            if isinstance(kept, int) and not isinstance(kept, bool):
                content[BROWSER_KEY] = kept  # the ceiling for browser tests is recorded separately
        except (OSError, ValueError, AttributeError):
            pass
        try:
            baseline_path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
        except OSError as error:
            print(f"cannot write {baseline_path}: {error}", file=sys.stderr)
            return 2
        print(f"wrote {baseline_path} with {len(counts)} counts")
        return 0

    findings = evaluate(repo, load_baseline(baseline_path))
    print({"text": as_text, "json": as_json, "markdown": as_markdown}[args.format](repo, findings))
    if args.report_only:
        return 0
    # Only what a machine found fails the run. A manual rule that a person recorded as not met lowers the score and
    # is listed, and does not fail the run: otherwise an honest "not met" would turn CI red and hold a deploy, and
    # nobody would record one.
    held = load_held(baseline_path)
    blocking = [
        f for f in findings
        if f.rule.level == "must" and f.status == "fail" and f.rule.kind != "manual" and f.rule.blocks
        and f.rule.id not in held
    ]
    if held and args.format == "text":
        still = sorted(f.rule.id for f in findings if f.rule.id in held and f.status == "fail")
        print(f"Held since adoption (reported, not blocking): {', '.join(still) or 'none, all put right'}.")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
