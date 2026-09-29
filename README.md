# Agent Board

A live ticket board for coding agents running in a **herdr** session. One card =
one piece of work = one agent on one branch or worktree. Works the same for
**Claude Code**, **Codex** and **opencode**; harness-specific behavior lives
behind one adapter interface.

- Server: Python 3 standard library only, one file (`server.py`).
- Front end: Vite + React + TypeScript + Mantine.
- Local or remote through an authenticated tunnel. The server always binds
  `127.0.0.1`.

## Quick start

```bash
./install.sh          # password, front-end build, systemd user unit + linger
./run.sh              # or start it in the foreground
```

Open `http://127.0.0.1:8792/`. On a headless server, use an SSH tunnel first:

```bash
ssh -L 8792:127.0.0.1:8792 <server>
# then open http://127.0.0.1:8792/
```

`install.sh` asks about remote access after it sets everything up (see below).

## Configuration — `config.json`

| key | meaning |
| --- | --- |
| `herdr_session` | session to attach to (`herdr --session <name>`) |
| `port`, `bind` | listen address (bind is always `127.0.0.1` in practice) |
| `remote.mode` | `none` \| `tailscale` \| `cloudflare` |
| `remote.hostnames` | extra Host/Origin allow-list entries for the tunnel |
| `notifications.webhook` | `none` \| `ntfy` \| `slack` \| `telegram` |
| `docs_repo.local_path` | local clone used by the "PR without docs" rule (off when null) |
| `project_roots` | extra roots scanned for parked worktrees |
| `pr_title_pattern` | regex a PR title must match |

## Server setup on a headless box

```bash
./install.sh
loginctl enable-linger "$USER"        # keep running with nobody logged in
systemctl --user status agent-board   # Restart=always
```

The unit lives at `~/.config/systemd/user/agent-board.service`. herdr's own
persistent server must also survive logout; it runs as a user server, so
`loginctl enable-linger` covers it too. Verify with `herdr status`.

### Agent logins on a headless box

Agent Board never stores or reads the agents' credentials.

- **Claude Code** — run `claude` once and complete the login flow. On a headless
  box it prints an OAuth URL to paste into a browser; the board shows that
  screen as a **Needs you** card with raw keys if it needs input.
- **Codex** — `codex login` (or `codex login status`). On first use in a folder
  Codex asks to trust the workspace; the board surfaces that prompt and can
  answer it.
- **opencode** — `opencode providers` (auth.json). The board only reads its
  session database, never the credentials.

## Remote access

Remote mode is **off by default** and the board shows a "remote on" badge while
it is enabled. Never port-forward and never bind `0.0.0.0`.

- **Tailscale (default):** `tailscale up && tailscale serve --bg 8792`.
- **Cloudflare Tunnel + Access:** `cloudflared tunnel --url http://127.0.0.1:8792`
  and add a Cloudflare Access policy (SSO) in front.

If the server is company-managed or company-owned, get IT approval before
enabling a tunnel.

## Notifications when no page is open

The page raises browser notifications and a chime on the user's own device.
With no page open, the server posts to the configured webhook (ntfy topic,
Slack, or Telegram bot) with a title, a body and a link to the board — nothing
secret. Push is rate-limited to one per agent per minute.

## Security

- Login password stored as a **scrypt** hash; sessions are random 256-bit
  cookies, `HttpOnly`, `SameSite=Strict`, 12-hour expiry, revoked on logout.
  15-minute lockout after 10 failed logins.
- Every `/api` route (and the SSE stream) requires a valid session **or** a
  machine token in `X-Board-Token`; `Host` and `Origin` must be in the
  allow-list (blocks DNS rebinding / CSRF).
- Machine tokens are created and revoked on demand and stored hashed.
- Everything that reaches an agent or git is validated: pane ids
  (`^w[0-9A-Za-z]+:p[0-9A-Za-z]+$`), allow-listed key names, capped text,
  hire folders under `$HOME`, commit shas by regex, diffs only for files git
  lists as changed.
- Audit log `audit.jsonl` records time, user/token, action and pane — never the
  text sent to an agent.
- No code path ever pushes, force-pushes, deletes branches, or puts a secret in
  a notification or log.

## The adapter interface

Everything harness-specific lives in `adapters/`. Nothing else branches on the
harness name.

```python
class Adapter:
    kind: str
    def events(self, session_id) -> list[Event]      # incremental transcript
    def parse_prompt(self, screen_lines) -> Ask | None
    def plan_answer(self, ask, answer) -> list[Step]  # keys / text + short waits
    def resume_args(self, session_id) -> list[str]
    def status_line(self, screen_lines) -> dict
```

`Event` normalizes prompts, replies, tool calls, file edits (path), shell
commands, subagent runs, questions and answers across harnesses.

**Fallback, always available:** when a transcript can't be read or a prompt
doesn't parse, the board shows the live screen tail and offers raw keys
(digits, Enter, Esc, arrows). It never guesses an answer.

### What each adapter supports

| feature | Claude Code | Codex | opencode |
| --- | --- | --- | --- |
| transcript events | yes | yes | yes |
| prompt parsing | yes | yes | questions only |
| status line (ctx %, 5h/7d) | yes | tokens only | not reported |
| subagents | yes | not reported | not reported |
| resume | `--resume <id>` | `codex resume <id>` | `--session <id>` |
| questions/permissions | yes | yes (trust/menus) | yes (`question` tool) |
| session id source | `agent_session.value` | discovered from `state_5.sqlite` | `agent_session.value` |
| fallback | raw keys | raw keys | raw keys |

Measured notes are recorded as one-line comments beside the code that relies on
them (e.g. `adapters/claude.py`, `adapters/codex.py`, `adapters/opencode.py`).

### Adding a fourth harness

1. Create `adapters/<name>.py` with a subclass of `Adapter`.
2. Map that harness's records onto `Event`.
3. Measure its prompt shapes by starting a throwaway agent in a scratch folder,
   triggering a permission prompt, and reading the screen — write each measured
   fact as a comment.
4. Fill in `supports` so the UI shows "not reported by <harness>" instead of a
   blank.
5. Register it in `adapters/__init__.py` `ADAPTERS`.

## Testing

```bash
# browser system libs (no root): extract .deb libs into ~/.local/lib/agent-board
# or, with sudo: npx playwright-core install-deps chromium

node tests/a11y.mjs                 # axe, both themes, 1280px + 375px
node tests/smoke.mjs                # screenshots
E2E_KIND=opencode node tests/e2e.mjs
E2E_KIND=codex   node tests/e2e.mjs
E2E_KIND=claude  node tests/e2e.mjs
node tests/cpu.mjs                  # browser-tab idle CPU
```

`server.py` runs `_selfcheck()` at every start: asserts over the parsers,
`plan_answer`, the workflow rules and the auth checks.
