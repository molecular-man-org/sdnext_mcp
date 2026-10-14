#!/usr/bin/env bash
# PreToolUse hook: block edits to files Claude should not modify.
# Reads the tool call as JSON on stdin; exit 2 blocks the call and returns
# stderr to Claude. Add patterns to PROTECTED to extend it.
set -euo pipefail

PROTECTED=(
  '.env'
  '.env.*'
  '*.pem'
  '*.key'
  'secrets/*'
  '.git/*'
  'LICENSE'
)

# Without jq the path cannot be parsed; fail open rather than block all edits.
command -v jq >/dev/null 2>&1 || exit 0

file_path=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty')
[ -n "$file_path" ] || exit 0

rel_path=${file_path#"${CLAUDE_PROJECT_DIR:-$PWD}"/}
base_name=${rel_path##*/}

for pattern in "${PROTECTED[@]}"; do
  # shellcheck disable=SC2254  # patterns are meant to glob
  case "$rel_path" in
    $pattern)
      echo "Blocked: $rel_path matches protected pattern '$pattern' (.claude/hooks/protect-files.sh)." >&2
      exit 2
      ;;
  esac
  # shellcheck disable=SC2254  # patterns are meant to glob
  case "$base_name" in
    $pattern)
      echo "Blocked: $rel_path matches protected pattern '$pattern' (.claude/hooks/protect-files.sh)." >&2
      exit 2
      ;;
  esac
done

exit 0
