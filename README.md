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

Use native Node.js and npm in the Linux environment; Windows npm through WSL
cannot run this build correctly. Python 3 and herdr must also be available.

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

## Harness-native input: `/`, `@`, `$`

The chat composer does not fake autocomplete. Each harness owns its input buffer
and its own completion menu, so the board mirrors what you type into the agent
with `herdr pane send-text` (no Enter) and reads the agent's real menu back:

| trigger | what it opens | Claude | Codex | opencode |
| --- | --- | --- | --- | --- |
| `/` | slash commands | yes | yes | yes |
| `@` | files / mentions / agents | yes | yes | agents |
| `$` | shell or skill/app picker | — | yes | — |

Picking a completion replaces the typed token and mirrors the edit. "Send" only
presses Enter, because the text is already in the agent's composer. This means
the menus are exactly the ones the CLI would show, including fuzzy filtering and
any harness-specific behavior.

## Chat

Each agent's conversation is read from its own harness transcript and rendered
the way the harness draws it: prompts, replies (markdown), tool calls with their
input and result (edits as diffs), subagent runs, questions the agent asked you,
`!` shell commands, and the live task list pinned at the top. A transcript that
can't be read falls back to the screen tail; the same renderer serves every
harness because the server normalizes both paths to one item shape.

## Views: board and shop floor

Toggle **Board ⇄ Floor** in the header:

- **Board** — the kanban of tickets by work stage.
- **Floor** — a CSS-only shop floor: stations on a conveyor, each ticket a cart
  that slides to the stage it reached, agents as status lamps riding the cart.
  Motion happens only when a stage changes (a transform transition), never on a
  timer, and there is no canvas or WebGL, so an idle floor costs nothing.

## Notifications and reports

- **Push review** (agent panel → Push): what an agent asking to push would send
  — the commits, the stat and the full diff. The board never pushes.
- **While you were away** — after 30+ minutes off the page, a per-agent digest of
  what changed; anything waiting on you goes first.
- **Restart risk** (`/api/risk`) — per agent, uncommitted files, unpushed
  commits, context, and whether the repo is shared.
- **Usage** (`/api/usage`) — 5h/7d percentages with a rising trend and an ETA.

## Personas

A hire can carry a **role** — a system prompt passed to Claude at start
(`--append-system-prompt`) and saved per install for reuse. Set it in the Hire
dialog's *Role* field or via `POST /api/persona`.

## Workspaces and agents

A herdr **workspace holds one or more tabs; each tab is one agent**. Hiring
therefore either:

- creates a **new workspace** named by you, with this agent as its first tab, or
- adds a **tab** to an existing workspace, so several agents share one workspace.

The Hire dialog lists existing workspaces with their tab and agent counts.

## Design

The board is a **retro-cartoon mission control**: a status cockpit, not a
dashboard. It exists to answer one question in a glance — *who needs me right
now?* — so the single bold element is the board chrome and the red **"agents
need you"** marquee; everything else stays flat and quiet.

- **Palette (functional, never decorative):** ink `#1b1a17`, teletype paper
  `#f2e4c7`, signal red `#e4572e` (only for "needs you"), marigold `#f4a93c`
  (busy), teal `#2e7d6f` (ready), screen green `#7bb662` (shipped). A dark
  "night shift" theme remaps the same roles to glowing sign colours.
- **Type:** Bungee for the wordmark and column headers only, Space Grotesk for
  everything else. Both self-hosted (`web/public/fonts/`) so the board works
  with no network.
- **Form:** 2px ink outlines, hard offset shadows (a sticker, not a soft blur),
  zero border radius, rivets on panels. Status is colour-coded by function.
- **One motion:** cards move with a FLIP animation when their column changes.
  Nothing animates on a timer.
- Colours are validated for WCAG AA in both themes; `tests/a11y.mjs` asserts
  zero axe violations at 1280px and 375px.

## Layout

```
server.py            entrypoint: HTTP routes, WebSocket, self-check, main()
board/
  config.py          config load/merge/save
  herdr.py           herdr CLI wrapper + argument validation
  security.py        login, sessions, machine tokens, origin allow-list, audit
  gitrepo.py         read-only git helpers (always --no-optional-locks)
  notify.py          in-page + webhook notifications
  terminal.py        browser terminal: herdr TUI over WebSocket + pty
  workflow.py        git command rules, the nine stations, ticket inference
  worktrees.py       parked worktrees (native herdr, git fallback)
  chat.py            rich chat from a transcript (tools, questions, todos)
  suggest.py         `/` commands and `@` files for the composer
  reports.py         push review, digest, restart risk, usage forecast
  core.py            live state, polling, board building
  hire.py            hire/fire, workspaces, worktrees, personas
adapters/            one module per harness (claude, codex, opencode)
  ask.py             rich Claude prompt parsing (ported, pure functions)
web/                 Vite + React + Mantine front end
  src/components/    Chat, Composer, Tools, Floor, Crew, TicketCard, …
```

`server.py` runs `_selfcheck()` at every start: asserts over the parsers,
`plan_answer`, the workflow rules, the ticket inference, the upload path guard
and the auth checks.

## HTTP API

| route | what |
| --- | --- |
| `GET /api/board` | tickets, agents, worktrees, alerts |
| `GET /api/agent?pane=` | one agent: chat, git, status, ask |
| `GET /api/chat?pane=` | rich chat items + todos |
| `GET /api/suggest?pane=&kind=&q=` | composer completions |
| `GET /api/pushinfo?pane=` | what a push would send |
| `GET /api/risk` | restart risk per agent |
| `GET /api/usage` | 5h/7d with ETA |
| `GET /api/digest?since=` | while-you-were-away |
| `GET /api/personas` · `POST /api/persona` | saved roles |
| `POST /api/prompt` `/api/answer` `/api/keys` `/api/type` `/api/menu` | drive an agent |
| `POST /api/hire` `/api/fire` `/api/rehire` | crew changes |
| `POST /api/worktree_create` `/api/worktree_remove` `/api/worktree_open` | worktrees |
| `GET /ws/terminal?pane=&cols=&rows=` | full herdr TUI |

All `/api` routes and the WebSocket require a valid session or a machine token,
and an allow-listed `Host`/`Origin`.

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

The isolated audit suites do not require a herdr session and never send input to
live agents:

```bash
npm ci
npm ci --prefix web
npm test                       # HTTP, streaming, workflow and safety regression tests
npm run build
npm run test:ui                # self-contained fixture server, desktop/mobile + both themes
```

For a read-only check of the revised board against an existing herdr session:

```bash
python3 tests/live_server.py    # separate candidate at 8793, temporary state, webhooks off
npm run test:live               # candidate password is board-test-pw
```

`BOARD_URL` and `BOARD_PW` select another server for `test:live`.
`AUDIT_DIST` supplies a build directory to `test:ui` and `--dist` does the same
for `live_server.py`. The candidate server rejects agent mutations and terminal connections, and does not replace the installed service.
Audit findings and verification are recorded in [docs/audit.md](docs/audit.md).

The older harness lifecycle suites create agents and exercise login lockout.
Point them at a disposable board server with `BOARD_URL` before running them:

```bash
# browser system libs (no root): extract .deb libs into ~/.local/lib/agent-board
# or, with sudo: npx playwright-core install-deps chromium

node tests/a11y.mjs                 # axe, both themes, 1280px + 375px
node tests/a11y-floor.mjs           # axe on the shop floor view
node tests/smoke.mjs                # screenshots
node tests/tickets.mjs              # ticket model: chips, stage rollup, parked
node tests/floor.mjs                # shop floor renders and toggles
node tests/chat-rich.mjs            # chat bubbles/tools, auto-scroll
node tests/chat-scroll.mjs          # chat stays pinned to the newest message
node tests/composer2.mjs            # / and @ suggestions, ! bash mode
node tests/persona.mjs              # role field in Hire
node tests/terminal.mjs             # browser terminal over WebSocket
node tests/closed.mjs               # off-shift strip
BOARD_URL=http://127.0.0.1:8794 E2E_KIND=opencode node tests/e2e.mjs
BOARD_URL=http://127.0.0.1:8794 E2E_KIND=codex   node tests/e2e.mjs
BOARD_URL=http://127.0.0.1:8794 E2E_KIND=claude  node tests/e2e.mjs
node tests/cpu.mjs                  # browser-tab idle CPU
```

`server.py` runs `_selfcheck()` at every start: asserts over the parsers,
`plan_answer`, the workflow rules and the auth checks.
