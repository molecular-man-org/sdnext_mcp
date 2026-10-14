#!/usr/bin/env bash
# Run every check. CI runs this same script, so a clean local run means a
# clean CI run. Locally, a missing tool is skipped with a warning; in CI
# (CI=true) a missing tool is a failure.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

MARKDOWNLINT_VERSION=0.23.3

failed=0

# TMP-5: the checks must leave the checkout as they found it. Compared with the
# state before they ran, so a local run with work in progress is not blamed.
status_before=$(git status --porcelain 2>/dev/null || true)
skipped=0

run() {
  local name=$1
  shift
  echo "==> $name"
  if ! "$@"; then
    echo "FAILED: $name" >&2
    failed=1
  fi
}

need() {
  local tool=$1
  if command -v "$tool" >/dev/null 2>&1; then
    return 0
  fi
  if [ "${CI:-}" = "true" ]; then
    echo "FAILED: $tool is not installed" >&2
    failed=1
  else
    echo "SKIPPED: $tool is not installed" >&2
    skipped=1
  fi
  return 1
}

# tsconfig files and editor settings allow comments, so they are not strict JSON.
check_json() {
  git ls-files -z -- '*.json' ':!:*tsconfig*.json' ':!:.vscode/*' ':!:.devcontainer/*' |
    python3 -c '
import json, sys
bad = 0
for name in filter(None, sys.stdin.buffer.read().decode().split("\0")):
    try:
        with open(name, encoding="utf-8") as handle:
            json.load(handle)
    except (OSError, ValueError) as error:
        print(f"{name}: {error}", file=sys.stderr)
        bad = 1
sys.exit(bad)'
}

check_shell() {
  local files=()
  while IFS= read -r -d '' file; do
    files+=("$file")
  done < <(git ls-files -z -- '*.sh')
  [ ${#files[@]} -eq 0 ] || shellcheck "${files[@]}"
}

check_executable() {
  local status=0 file
  while IFS= read -r -d '' file; do
    if [ ! -x "$file" ]; then
      echo "$file is not executable (git update-index --chmod=+x $file)" >&2
      status=1
    fi
  done < <(git ls-files -z -- '*.sh')
  return $status
}

need python3 && run "JSON syntax" check_json
need shellcheck && run "shellcheck" check_shell
run "scripts are executable" check_executable
need actionlint && run "actionlint" actionlint
need yamllint && run "yamllint" yamllint --strict .
need npx && run "markdownlint" npx --yes "markdownlint-cli2@$MARKDOWNLINT_VERSION"
run "standard checks" scripts/standard-checks.sh

# Add the project's own checks (linters, type checkers, tests) below this line.

status_after=$(git status --porcelain 2>/dev/null || true)
if [ "$status_after" != "$status_before" ]; then
  echo "FAILED: the checks left the checkout changed (TMP-5). New or changed:" >&2
  diff <(printf '%s\n' "$status_before") <(printf '%s\n' "$status_after") | grep '^>' | sed 's/^> /  /' >&2
  echo "Write scratch to a fresh temporary directory, or ignore build output in .gitignore." >&2
  failed=1
fi

if [ $failed -ne 0 ]; then
  echo "Checks failed." >&2
  exit 1
fi
if [ $skipped -ne 0 ]; then
  echo "Checks passed, but some were skipped (see SKIPPED above)."
else
  echo "All checks passed."
fi
