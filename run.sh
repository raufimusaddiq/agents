#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PORT="${AGENT_BOARD_PORT:-8792}"

if [ ! -d web/dist ] && [ -d web ]; then
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 && [ "$(node -p 'process.platform')" != "win32" ]; then
    echo "Building front end (first run)..."
    (cd web && npm ci --no-audit --no-fund && npm run build)
  else
    echo "Native Node.js and npm are required to build the front end." >&2
    exit 1
  fi
fi

exec python3 server.py --port "$PORT"
