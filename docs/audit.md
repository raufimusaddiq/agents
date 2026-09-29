# Project audit — 30 September 2026

The audit covers the Python API, harness adapters, React frontend, styling,
agent details, live feedback, Git/worktree safety, authentication, notifications,
and local installation scripts. Fixes are in this checkout. The installed board
on port 8792 has not been replaced.

## UI styling and interaction consistency

The board, agent details, login and dialogs now share palette, border, radius,
button sizing and focus rules. Mantine inputs, modals and menus follow the same
visual treatment as the custom board controls. Display typography is restricted
to prominent branding; agent names, tickets and dialog content use readable body
type. Selected tabs use neutral emphasis, attention and failure states use the
error palette, and success badges retain readable contrast in both themes.

Mobile header, crew, panel and login layouts handle narrow screens and long
content. Keyboard users can focus chat, diff and terminal feedback scroll areas.
Modal close controls have accessible names. Reduced-motion preferences are
respected. Login, detail loading, fire, diff and workflow failures have visible
recovery states. Hire fields display the values actually submitted.

## Agent details and live feedback

- Codex custom `exec` events carried raw JavaScript, which previously produced
  empty shell records. The adapter now extracts literal commands without
  evaluating source, ignores quoted examples, records patch files, and exposes
  model/token metadata. The active Codex agent correctly showed Testing instead
  of To do after its test commands became visible.
- opencode streaming parts now update existing chat rows using stable identities.
  Equal timestamps and updates within the same second no longer lose content or
  duplicate assistant replies. Session model, token and cost data are exposed.
- Attention detection uses terminal fallback for every harness, including
  permission prompts. Codex ordinary input boxes exclude historical login text
  from attention detection. Unsupported prompts present terminal key controls rather
  than an answer form the adapter cannot execute.
- Board refreshes are serialized. Detail polling avoids overlapping requests and
  stale responses, preserves existing detail and drafts during failures, and
  clears stale authenticated state after logout or session expiry.
- SSE queues coalesce updates without dropping the last change during rate
  limiting. Changes use meaningful feedback fingerprints rather than poll time.
  The UI shows Live, Connecting or Reconnecting and the last refresh time.
- Composer drafts and synchronization state survive closing/reopening details.
  Failed input synchronization blocks submission; Unicode deletion and large
  text batches are handled consistently. Prompt answers compare the displayed
  prompt with the current prompt before sending input.
- Workflow stages and skipped-step alerts recognize executable commands rather
  than quoted snippets/heredoc source, keep test/review observations within the
  relevant commit interval, and avoid claiming code changes for docs-only commits.

Workflow stages describe observed activity. A test command appearing in a
transcript does not certify a passing test run, and review observations do not
certify review quality.

## Backend and solution safety

- JSON requests validate body shape, framing, size, Unicode, keys and terminal
  dimensions. Error responses stay JSON and rejected requests close the connection
  when unread bytes could remain.
- Host/Origin checks compare exact scheme and authority. Static files and folder
  selection enforce path containment, including symlink escapes. The server binds
  to loopback and respects CLI port overrides in its auth configuration.
- Manual and automatic worktree removal share a fail-closed Git safety check.
  Dirty/unpushed checkouts and checkouts occupied by an agent are refused; removal
  preserves branches. Git rename paths and commit subjects with separators parse
  correctly.
- WebSocket frames require masking and have a size limit. Terminal writes are
  serialized, PTY children are reaped, EOF unblocks readers, and revoked sessions
  close terminal connections. SSE also rechecks session validity.
- Expired login sessions are pruned; logout revokes and expires the cookie.
  Machine-token persistence is atomic with private file permissions. Login/JSON
  responses prevent caching. Password setup uses a secure prompt.
- Automatic notifications omit transcript content, account for an open SSE page,
  avoid repeated attention pushes, and retain rate quota when a push is suppressed.
  Notification tests mock outbound delivery; the preview disables webhooks.
- Install/run scripts validate native Node availability, use reproducible dependency
  installs and the actual checkout path. Background startup avoids shell command
  interpolation. The terminal frontend loads on demand, reducing the initial
  JavaScript bundle from roughly 805 KB to 476 KB (146 KB gzip).

## Verification

- `npm test`: 37 regression tests passed, plus backend startup self-check.
  Tests include isolated HTTP requests, streamed transcript updates, prompt
  identity, notifications, workflow classification, terminal session revocation,
  and real temporary Git worktrees/local remote refs.
- `npm run build`: TypeScript and Vite production build passed.
- `npm run test:ui`: isolated browser fixture exercises desktop 1280px and mobile
  375px, both themes, accessibility checks, dialogs, draft persistence, Unicode
  input, synchronization failure, refresh failure and logout.
- `npm run test:live`: reads four existing agents; covers Codex and opencode,
  all four detail tabs, both themes and both widths (32 detail/theme/width cases).
  Checks accessibility, horizontal overflow, browser errors, live connection and
  SSE delivery. The final board response took approximately 119ms.
- Shell syntax, Python compilation, browser-script syntax and `git diff --check`
  passed.

The preview is available at `http://127.0.0.1:8793` with audit password
`board-test-pw`. It uses temporary board state and live agent reads, rejects
agent mutation endpoints and terminal connections, and disables agent actions in
its UI. It does not deploy these changes to the installed service.

## Verification limits

No live Claude agent was available. Live hire/fire, worktree deletion, prompt
answers and terminal input were intentionally not exercised against active
agents; their regression checks use disposable fixtures. No outbound webhook was
sent and the system service installer was not executed. The older lifecycle
browser suite requires a separate disposable mutable board and was not run on
the active session. Automated accessibility checks complement the layout checks;
they do not replace a full assistive-technology review.
