#!/usr/bin/env bash
# Read-only preparation of an isolated dispatcher checkout on Brainy Cygnus.
# Does not restart services, install timers or call OpenAI.
set -Eeuo pipefail
REPO=/opt/kartashova/symmetrical-guide
STAGE=/opt/kartashova/auto003-stage
UV=/opt/kartashova/tools/bin/uv
test -d "$REPO/.git" || { echo "Repository not found"; exit 2; }
test -x "$UV" || { echo "uv not found"; exit 2; }
cd "$REPO"
git fetch origin codex/06-auto-003-implementation
if [ ! -d "$STAGE" ]; then
  git worktree add --detach "$STAGE" FETCH_HEAD
else
  echo "Staging directory already exists. Not overwriting."
  exit 3
fi
cd "$STAGE/system/runtime"
"$UV" sync --locked --extra test
"$UV" run --locked --extra test python -m compileall -q corporate_brain/dispatcher.py corporate_brain/managed_agent.py
"$UV" run --locked --extra test pytest -q tests/test_dispatcher.py tests/test_managed_agent.py
echo "DISPATCHER STAGE: OFFLINE TESTS PASSED"
echo "Existing service and Telegram unchanged. No OpenAI calls."
