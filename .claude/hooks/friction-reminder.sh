#!/usr/bin/env bash
# Stop hook: once per session, when the session made commits, remind the agent to add to the
# repository's friction log (rule WORK-5) before it stops. Silent otherwise.
#
# Reads the hook input as JSON on stdin. It reminds at most once per session: when Claude is
# already continuing because of a stop hook (`stop_hook_active`), or this session was reminded
# before, it exits quietly, so it can never hold a session in a loop.
set -uo pipefail

# Without jq the input cannot be parsed; stay silent rather than get in the way.
command -v jq >/dev/null 2>&1 || exit 0

input=$(cat)
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false')" = "true" ] && exit 0
session=$(printf '%s' "$input" | jq -r '.session_id // empty' | tr -cd 'A-Za-z0-9-')
[ -n "$session" ] || exit 0

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# Commits by this machine's git user in the last 12 hours stand for "this session made commits".
author=$(git config user.email || true)
[ -n "$author" ] || exit 0
[ -n "$(git log --since='12 hours ago' --author="$author" --format=%h -1 2>/dev/null)" ] || exit 0

marks="${TMPDIR:-/tmp}/claude-friction-reminded"
mkdir -p "$marks" 2>/dev/null || exit 0
[ -e "$marks/$session" ] && exit 0
: > "$marks/$session"

jq -n '{decision: "block", reason: "Before stopping (rule WORK-5): if this session finished a piece of work (a pull request merged, an issue closed, or a long stretch of work), add one comment to this repository'"'"'s friction log issue (label `friction`): each friction item with a tag, how many times, and the rough cost in turns or minutes, or \"none\". Counts and short descriptions only. If it finished nothing, say so in one line and stop."}'
