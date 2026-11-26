#!/usr/bin/env bash
# Make the demo-day copy from the rehearsed project (runbook part B).
#
# Usage: demo/make-live-copy.sh [--from DIR] [--to DIR] [--user ID]
#   --from  the rehearsed project with the demo/* tags (default: ~/Projects/Exp/clinic-demo)
#   --to    the new live copy (default: <from>-live). Must not exist.
#   --user  optional: switch the event sender to this developer id (asks for its key, hidden)
#
# The copy starts at demo/0-setup with its own bare remote, so the live push starts clean.
# Jump to a later stage in your own terminal: git switch --discard-changes -C main demo/<N>-<name>
set -euo pipefail

MODULE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FROM="$HOME/Projects/Exp/clinic-demo"
TO=""
DEV_USER=""
COLLECTOR="http://127.0.0.1:4318"
TAGS="demo/0-setup demo/1-brief demo/2-prd demo/3-arch demo/4-stories demo/5-story1-built demo/6-reviewed"

while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --to) TO="$2"; shift 2 ;;
    --user) DEV_USER="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "make-live-copy: unknown option $1" >&2; exit 2 ;;
  esac
done
FROM="$(cd "$FROM" && pwd)"
TO="${TO:-$FROM-live}"
REMOTE="$TO-remote.git"

fail() { echo "make-live-copy: $*" >&2; exit 1; }

git -C "$FROM" rev-parse -q --verify refs/tags/demo/0-setup >/dev/null || fail "$FROM has no tag demo/0-setup (run demo/prepare.sh first)"
[ -e "$TO" ] && fail "$TO already exists. Move it away first."
[ -e "$REMOTE" ] && fail "$REMOTE already exists. Move it away first."

missing=""
for t in $TAGS; do
  git -C "$FROM" rev-parse -q --verify "refs/tags/$t" >/dev/null || missing="$missing $t"
done
[ -n "$missing" ] && echo "warning: no tag for:$missing. Those stages cannot be skipped on the day."

git clone -q "$FROM" "$TO"
cd "$TO"
git switch -q -C main demo/0-setup
git remote remove origin

# Guard setup again: core.hooksPath is local git config and does not travel with a clone.
python3 "$MODULE/scripts/setup_guard.py" --project . >/dev/null
python3 "$MODULE/scripts/setup_guard.py" --project . --check
[ -z "$(git status --porcelain)" ] || fail "setup changed files in the copy; check the module version (git status)"

# Fresh remote with main only, made by clone so no hook runs.
git clone -q --bare --single-branch --branch main --no-tags . "$REMOTE"
git remote add origin "$REMOTE"
git fetch -q origin && git branch -q -u origin/main

if [ -n "$DEV_USER" ]; then
  python3 "$MODULE/scripts/setup_guard.py" --credentials --force --endpoint "$COLLECTOR" --user "$DEV_USER"
fi

echo
echo "Live copy: $TO (at $(git log -1 --format='%h %s'))"
echo "Remote:    $REMOTE"
echo "Tags:      $(git tag -l 'demo/*' | tr '\n' ' ')"
