#!/usr/bin/env bash
# Update this project to a version of the template, and show what it did.
#
#   scripts/kit-update.sh v0.6.0
#
# It runs `copier update`, stops if a file that is not the kit's own changed
# (an update must never touch the project's files), runs the project's check
# command, and prints the standard's score before and after.
#
# Run it on a clean branch. It commits nothing.
#
# Owned by the template: change it there, not in a project.
set -uo pipefail

# After `copier update` the script hands over to the copy the update just wrote (`--after-update BEFORE`), so
# the list of the kit's files below is always the new version's. An old copy does not know the files that were
# added since it was made.
if [ "${1:-}" = "--after-update" ]; then
  after_update=1
  before=${2:-unknown}
else
  after_update=0
fi

version=${1:-}
if [ "$after_update" = 0 ] && [ -z "$version" ]; then
  echo "usage: scripts/kit-update.sh VERSION   (for example v0.6.0)" >&2
  exit 2
fi

here=$(cd "$(dirname "$0")" && pwd)
root=$(git -C "$here" rev-parse --show-toplevel) || exit 2
cd "$root" || exit 2

score() { python3 "$here/standard_check.py" --repo "$root" --report-only | tail -n 1; }

if [ "$after_update" = 0 ]; then
  if [ -n "$(git status --porcelain)" ]; then
    echo "The working tree is not clean. Commit or stash first, so the update's changes can be read on their own." >&2
    exit 2
  fi
  before=$(score)
  echo "==> update to $version"
  uvx --quiet copier@9.18.2 update --trust --defaults --vcs-ref "$version" || {
    echo "FAILED: copier update did not finish. Nothing else was done." >&2
    exit 1
  }
  exec bash "$root/scripts/kit-update.sh" --after-update "$before"
fi

# The kit's own files: everything an update is allowed to rewrite.
kit='^(\.copier-answers\.yml|standard-baseline\.json|scripts/(standard_check\.py|standard-checks\.sh|ci-tools\.sh|ci-verdict\.py|pr_flow\.py|fix_workflows\.py|fix_standard\.py|format-check\.sh|lint-check\.sh|lane-fix\.sh|kit-update\.sh|vuln-scan\.py|dep_merge\.py|work\.py|automerge\.py|branch_cleanup\.py|failure_issue\.py|builder_refresh\.py)|docs/phone-app-decisions\.md|e2e/kit/|\.claude/(hooks|agents|skills)/|\.github/workflows/(builder-(sync|cleanup)[^/]*|vuln-scan|dependency-merge|automerge|branch-cleanup|failure-issue|e2e-full)\.yml)'
changed=$(git status --porcelain | cut -c4-)
others=$(printf '%s\n' "$changed" | grep -v -E "$kit" | grep -v '^$' || true)

# A newer version of the standard may count something it did not count before. Such a rule starts at today's
# figure, so that the update itself never turns the checks red; counts already recorded are not touched.
echo "==> counted rules that are new in this version"
python3 "$here/standard_check.py" --repo "$root" --extend-baseline || true

echo "==> changed by the update"
printf '%s\n' "${changed:-(nothing)}"

failed=0
if [ -n "$others" ]; then
  echo "FAILED: the update changed files that are the project's own, or left a conflict:" >&2
  printf '%s\n' "$others" | sed 's/^/  /' >&2
  echo "Read each one before going on. A file ending in .rej is a change that did not apply." >&2
  failed=1
fi

# Where the project formats with Prettier, the kit's new and changed files get the project's format, so the update
# itself does not fail the project's format check (realm-weaver, v1.3.9: the two new workflows). rollout.py does the
# same.
prettier=$(python3 -c 'import json; d=json.load(open("package.json")); print({**d.get("dependencies",{}),**d.get("devDependencies",{})}.get("prettier","").lstrip("^~"))' 2>/dev/null || true)
if [ -n "$prettier" ]; then
  pretty=()
  while IFS= read -r name; do
    [ -f "$name" ] && pretty+=("$name")
  done < <(git status --porcelain --untracked-files=all | cut -c4- | grep -E "$kit" | grep -E '\.(md|json|ya?ml)$' || true)
  if [ "${#pretty[@]}" -gt 0 ]; then
    echo "==> formatting ${#pretty[@]} of the kit's files with the project's Prettier $prettier"
    npx --yes "prettier@$prettier" --write "${pretty[@]}" >/dev/null || echo "Prettier did not finish; the format check says what is left." >&2
  fi
fi

command=$(python3 -c 'import json,sys; print(json.load(open("standard.json")).get("check_command","scripts/check.sh"))' 2>/dev/null || echo scripts/check.sh)
# A check command that is a script file without its executable bit (realmsculpt's run_checks.py was) runs through
# its interpreter rather than failing with "Permission denied".
if [ -f "$command" ] && [ ! -x "$command" ]; then
  case "$command" in
    *.py) command="python3 $command" ;;
    *) command="bash $command" ;;
  esac
fi
echo "==> check command: $command"
if ! bash -c "$command"; then
  echo "FAILED: the check command does not pass after the update." >&2
  failed=1
fi

echo "==> score"
echo "before: $before"
echo "after:  $(score)"

exit $failed
