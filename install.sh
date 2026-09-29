#!/usr/bin/env bash
# Idempotent installer for Agent Board (headless Linux).
set -euo pipefail
cd "$(dirname "$0")"
PORT="${AGENT_BOARD_PORT:-8792}"
# Quote the actual checkout for systemd, including spaces and literal percent signs.
UNIT_PROJECT_DIR="$(python3 -c 'import sys; print(sys.argv[1].replace("\\", "\\\\").replace("\"", "\\\"").replace("%", "%%"))' "$PWD")"

echo "== Agent Board install =="
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1 || [ "$(node -p 'process.platform')" = "win32" ]; then
  echo "Install native Linux Node.js and npm before running the installer." >&2
  exit 1
fi

# 1. Password
if ! python3 -c "import json;exit(0 if json.load(open('config.json'))['auth'].get('password_hash') else 1)" 2>/dev/null; then
  python3 server.py --set-password
fi

# 2. Front end
if command -v npm >/dev/null 2>&1; then
  ( cd web && npm ci --no-audit --no-fund && npm run build )
else
  echo "npm not found; skipping front-end build" >&2
fi

# 3. systemd user unit + linger
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/agent-board.service << UNIT
[Unit]
Description=Agent Board - live ticket board for herdr agents
After=network.target
[Service]
Type=simple
WorkingDirectory="$UNIT_PROJECT_DIR"
ExecStart=/usr/bin/python3 "$UNIT_PROJECT_DIR/server.py" --port $PORT
Environment=HOME=%h
Environment=PATH=%h/.local/bin:%h/.local/node/bin:%h/.opencode/bin:/usr/local/bin:/usr/bin:/bin
Restart=always
RestartSec=3
[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now agent-board.service
loginctl enable-linger "$USER" || true

# 4. Remote mode (asked, default none)
echo
echo "Remote access options:"
echo "  1) none (default) - reach it via: ssh -L $PORT:127.0.0.1:$PORT <server>"
echo "  2) Tailscale      - tailscale up && tailscale serve --bg $PORT"
echo "  3) Cloudflare     - cloudflared tunnel + Cloudflare Access"
read -rp "Choose [1/2/3]: " REMOTE || REMOTE=1
case "$REMOTE" in
  2) if command -v tailscale >/dev/null; then tailscale up; tailscale serve --bg "$PORT"; else echo "install tailscale first"; fi ;;
  3) if command -v cloudflared >/dev/null; then echo "run: cloudflared tunnel --url http://127.0.0.1:$PORT (add Access in the dashboard)"; else echo "install cloudflared first"; fi ;;
  *) : ;;
esac

echo "Done. Board: http://127.0.0.1:$PORT/"
