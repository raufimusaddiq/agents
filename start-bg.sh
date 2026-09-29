#!/usr/bin/env bash
# Start Agent Board fully detached from the calling shell.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8792}"
LOG="${AGENT_BOARD_LOG:-${XDG_STATE_HOME:-$HOME/.local/state}/agent-board/server.log}"
mkdir -p "$(dirname "$LOG")"
setsid python3 server.py --port "$PORT" >"$LOG" 2>&1 </dev/null &
echo "started pid $!"
