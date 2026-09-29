#!/usr/bin/env bash
# Start Agent Board fully detached from the calling shell.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8792}"
LOG="${AGENT_BOARD_LOG:-/tmp/opencode/srv.log}"
setsid bash -c "exec python3 server.py --port '$PORT'" >"$LOG" 2>&1 </dev/null &
echo "started pid $!"
