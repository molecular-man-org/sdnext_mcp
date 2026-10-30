#!/usr/bin/env bash
# The checks the project standard asks of every project: a secret scan, the
# standard itself, and (for a project with a front end) the lane guard.
#
# CHECK_RANGE, when set, is the range of commits being checked (`A..B`). CI
# sets it; a local run leaves it empty and looks at recent history instead.
#
# Owned by the template: change it there, not in a project.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(git -C "$here" rev-parse --show-toplevel) || exit 1
cd "$root" || exit 1

# The pinned tools scripts/ci-tools.sh installs: CI puts their folder on the PATH; a local clone has them here.
PATH="$root/.tools/bin:$PATH"

failed=0
range=${CHECK_RANGE:-}
guard_range=$range
if [ -n "$range" ] && ! git rev-list --quiet "$range" 2>/dev/null; then
  case "$range" in
    0000000000000000000000000000000000000000..*)
      echo "CHECK_RANGE starts at a new branch: there is nothing to compare with, so the lane guard is skipped."
      ;;
    *)
      echo "FAILED: CHECK_RANGE '$range' cannot be read. CI needs the full history (fetch-depth: 0)." >&2
      failed=1
      ;;
  esac
  range=
  guard_range=
fi

echo "==> secret scan"
if command -v gitleaks >/dev/null 2>&1; then
  gitleaks git --no-banner --redact --log-opts="${range:--n 50}" . || failed=1
elif [ "${CI:-}" = "true" ]; then
  echo "FAILED: gitleaks is not installed (run scripts/ci-tools.sh)" >&2
  failed=1
else
  echo "SKIPPED: gitleaks is not installed (scripts/ci-tools.sh installs it)" >&2
fi

echo "==> project standard"
python3 "$here/standard_check.py" --repo "$root" || failed=1

if [ -n "$guard_range" ]; then
  echo "==> lane guard"
  python3 "$here/standard_check.py" --repo "$root" --lane-guard "$guard_range" || failed=1
fi

exit $failed
