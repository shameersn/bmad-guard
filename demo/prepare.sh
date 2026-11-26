#!/usr/bin/env bash
# Prepare the Guarded BMAD demo project (runbook parts A1, A4, A5, A6, A7).
#
# Usage: demo/prepare.sh [--project DIR] [--name NAME] [--config-dir DIR]
#   --project     new demo project folder (default: ~/Projects/Exp/clinic-demo). Must not exist or be empty.
#   --name        your name: BMAD user name and the tech-stack approver (default: git config user.name)
#   --config-dir  where the telemetry settings file goes (default: ~/.config/bmad-guard-demo)
#
# It does NOT start the collector stack and does NOT make developer keys: do those off-screen
# (deploy/collector/README.md). Fake data only. Stops at the first failure.
set -euo pipefail

MODULE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$HOME/Projects/Exp/clinic-demo"
NAME="$(git config user.name || true)"
CONFIG_DIR="$HOME/.config/bmad-guard-demo"
BMAD_VERSION="6.12.4"
COLLECTOR="http://127.0.0.1:4318"

while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --config-dir) CONFIG_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "prepare: unknown option $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n== %s\n' "$*"; }
fail() { echo "prepare: $*" >&2; exit 1; }

step "A1. Check the tools"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || fail "python3 is $(python3 --version 2>&1); 3.10 or later is needed"
for tool in git npx uv claude; do
  command -v "$tool" >/dev/null || fail "$tool is not on PATH"
done
echo "python3 $(python3 -c 'import platform; print(platform.python_version())'), $(git --version), claude $(claude --version 2>/dev/null | head -1)"
[ -n "$NAME" ] || fail "no name: pass --name or set git config user.name"
if command -v curl >/dev/null && curl -s -o /dev/null --max-time 2 "$COLLECTOR"; then
  echo "collector answers on $COLLECTOR"
else
  echo "note: no collector on $COLLECTOR. Events will queue locally until the stack is up."
fi

if [ -e "$PROJECT" ] && [ -n "$(ls -A "$PROJECT")" ]; then
  fail "$PROJECT exists and is not empty. Pick another --project or move it away."
fi
REMOTE="$(dirname "$PROJECT")/$(basename "$PROJECT")-remote.git"
[ -e "$REMOTE" ] && fail "$REMOTE already exists. Move it away first."

step "A4. New project and BMAD $BMAD_VERSION"
mkdir -p "$PROJECT"
cd "$PROJECT"
git init -q -b main
printf '.venv/\n__pycache__/\n*.db\ntmp/\n' > .gitignore
npx -y "bmad-method@$BMAD_VERSION" install --directory . --modules bmm \
  --tools claude-code,github-copilot --user-name "$NAME" --yes
git add -A && git commit -q -m "Add BMAD $BMAD_VERSION"

step "A4. Guard setup"
python3 "$MODULE/scripts/setup_guard.py" --project .
python3 "$MODULE/scripts/setup_guard.py" --project . --check
git add -A && git commit -q -m "Add bmad-guard"

step "A5. HIPAA mode and the tech-stack list"
printf '[agent]\nphi = "block"\n' > _bmad/custom/prompt-guard.toml
mkdir -p _bmad/custom/project/guard
cat > _bmad/custom/project/guard/tech-stack.toml <<'EOF'
---
status: draft
---
# Packages this project may use. Names only. Approved by the architect with Warden.
[allowed]
python = ["fastapi", "uvicorn", "pydantic", "httpx", "pytest"]
EOF
git add -A && git commit -q -m "HIPAA mode and tech stack list (draft)"
python3 .claude/skills/agent-approval-grant/scripts/grant_approval.py \
  --doc-path _bmad/custom/project/guard/tech-stack.toml --doc-type tech-stack \
  --state-file _bmad/custom/approval-state.json --approved-by "$NAME" >/dev/null
git log -1 --format='%s' | grep -q '^Approve tech-stack' || fail "tech-stack approval was not committed"
printf 'fastapi\nuvicorn\n' > requirements.txt
printf 'APP_NAME=clinic-demo\n' > .env.example
git add -A && git commit -q -m "Add dependencies"

step "A6. Telemetry settings for this demo"
SETTINGS="$CONFIG_DIR/telemetry.json"
if [ -e "$SETTINGS" ]; then
  echo "keep     $SETTINGS (already there)"
else
  mkdir -p "$CONFIG_DIR"
  HELPER="$MODULE/module/skills/agent-project-logger/scripts/otel_headers.py"
  python3 - "$MODULE/demo/telemetry.json.example" "$SETTINGS" "$HELPER" <<'EOF'
import json, sys
src, dst, helper = sys.argv[1:4]
data = json.load(open(src))
data["otelHeadersHelper"] = f'python3 "{helper}"'
with open(dst, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
EOF
  echo "create   $SETTINGS"
fi

step "A7. Local bare remote and the baseline tag"
git clone -q --bare . "$REMOTE"
git remote add origin "$REMOTE"
git fetch -q origin && git branch -q -u origin/main
git tag demo/0-setup

step "Done"
git log --oneline
cat <<EOF

Project: $PROJECT
Remote:  $REMOTE
Next:
  1. Collector stack and keys, off-screen (deploy/collector/README.md), then
     python3 $MODULE/scripts/setup_guard.py --credentials --endpoint $COLLECTOR --user rehearsal
  2. alias gclaude='claude --settings $SETTINGS'
  3. Rehearse with gclaude in $PROJECT and tag each stage (docs/guides/demo-run.md, part B).
EOF
