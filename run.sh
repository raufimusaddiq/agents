#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PORT="${AGENT_BOARD_PORT:-8792}"

if [ ! -d web/dist ] && [ -d web ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "Building front end (first run)..."
    (cd web && npm install --no-audit --no-fund && npm run build)
  else
    echo "npm not found: front end not built." >&2
  fi
fi

exec python3 server.py --port "$PORT"
