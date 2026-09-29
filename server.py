"""Agent Board server. Python 3 standard library only.

One file. Includes _selfcheck() run at every start.

Security model: the server binds 127.0.0.1 only. Remote access is via a tunnel
(Tailscale/Cloudflare) with login in front. All /api and the WebSocket require a
valid session cookie or a machine token in X-Board-Token, plus a Host/Origin
allow-list. Every action that reaches an agent or git is audited.
"""
from __future__ import annotations

import hmac
import http.server
import json
import os
import re
import secrets
import signal
import socket
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import cookies
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapters import ADAPTERS, capability_table, get as get_adapter  # noqa: E402
from adapters.base import Ask, Event as AEvent  # noqa: E402

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_DIR = ROOT / "state"
AUDIT_PATH = ROOT / "audit.jsonl"
ROSTER_PATH = ROOT / "roster.json"
SECRETS_PATH = ROOT / "secrets.json"

from board.config import (  # noqa: E402
    config, load_config, save_config, DEFAULT_CONFIG, _deep_merge,
    CONFIG_PATH as CFG_PATH,
)
_ = CFG_PATH  # re-exported for callers that import it from server

# --------------------------------------------------------------------------
from board.herdr import (  # noqa: E402
    HerdrError, herdr, herdr_text, snapshot, read_screen, valid_pane,
    valid_keys, PANE_RE, SHA_RE, NAME_RE, TICKET_RE, ALLOWED_KEYS,
)

# --------------------------------------------------------------------------
from board.security import (  # noqa: E402
    Sessions, SESSIONS, hash_password, verify_password, origin_allowed,
    audit, _load_machine_tokens, _save_machine_tokens,
)

# --------------------------------------------------------------------------
from board.gitrepo import (  # noqa: E402
    git, repo_root, git_status, git_diff_file, git_diff_commit,
)
from board.notify import note_page_open, notify  # noqa: E402
from board.terminal import serve_terminal  # noqa: E402
from board.core import (  # noqa: E402
    STATE, State, reconcile_agents, poll_transcripts, read_screens, poll_loop,
    save_roster, check_roster_restart, _prune_closed, _norm_events,
    classify_git, edited_code_files, compute_stations, agent_stage,
    ticket_stage, derive_ticket, _branch_ticket, _first_prompt, _detect_alerts,
    _add_alert, STAGE_ORDER, STATIONS,
    build_board, build_agent, _build_chat, _last_line, _compat_cards,
    list_worktrees, list_workspaces, create_agent_worktree,
    remove_agent_worktree, yolo_args, _hire_impl, _rehire_impl, _find_closed,
    _new_shell_pane, _workspace_mode, _default_wt_branch, list_folders,
    _has_dir, settings_public,
    STATE_DIR, ROSTER_PATH,
)


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "AgentBoard/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # quiet; audit log is the record

    # -- helpers ---------------------------------------------------------
    def _json(self, code: int, obj) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(min(n, 1_000_000))
        try:
            return json.loads(raw)
        except ValueError:
            return {}

    def _host(self) -> str:
        return self.headers.get("Host", "")

    def _client_ip(self) -> str:
        return self.client_address[0] if self.client_address else "?"

    def _auth(self, require: bool = True) -> str | None:
        """Returns the user/token name, or None if unauthorized."""
        host = self._host()
        origin = self.headers.get("Origin")
        if not origin_allowed(host, origin):
            return None
        token = self.headers.get("X-Board-Token")
        if token:
            name = SESSIONS.check_machine_token(token)
            if name:
                return f"machine:{name}"
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        m = c.get("board_session")
        if m and SESSIONS.valid(m.value):
            return "user"
        return None

    def _deny(self) -> None:
        self._json(403, {"error": "forbidden"})

    def _body_screen(self, pane: str) -> list[str]:
        try:
            return read_screen(pane, 60)
        except HerdrError as e:
            return [f"<{e.code}: {e}>"]

    # -- routing ---------------------------------------------------------
    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        path = p.path
        if path == "/healthz":
            self._json(200, {"ok": True})
            return
        if path == "/ws/terminal":
            serve_terminal(self)
            return
        if path in ("/", "/index.html"):
            self._serve_static("index.html")
            return
        if path.startswith("/assets/") or path.startswith("/fonts/"):
            self._serve_static(path.lstrip("/"))
            return
        if path == "/api/capabilities":
            if not self._auth():
                return self._deny()
            self._json(200, {"adapters": capability_table()})
            return
        if path == "/api/board":
            if not self._auth():
                return self._deny()
            note_page_open()
            self._json(200, build_board())
            return
        if path == "/api/agent":
            if not self._auth():
                return self._deny()
            note_page_open()
            pane = urllib.parse.parse_qs(p.query).get("pane", [""])[0]
            self._json(200, build_agent(pane))
            return
        if path == "/api/events":
            if not self._auth():
                return self._deny()
            self._serve_sse()
            return
        if path == "/api/screen":
            if not self._auth():
                return self._deny()
            pane = urllib.parse.parse_qs(p.query).get("pane", [""])[0]
            if not valid_pane(pane):
                self._json(400, {"error": "bad_pane"})
                return
            audit("user", "read_screen", pane)
            self._json(200, {"lines": self._body_screen(pane)})
            return
        if path == "/api/worktrees":
            if not self._auth():
                return self._deny()
            self._json(200, {"worktrees": list_worktrees()})
            return
        if path == "/api/settings":
            if not self._auth():
                return self._deny()
            self._json(200, settings_public())
            return
        if path == "/api/folders":
            if not self._auth():
                return self._deny()
            q = urllib.parse.parse_qs(p.query).get("path", [""])[0]
            self._json(200, list_folders(q))
            return
        if path == "/api/workspaces":
            if not self._auth():
                return self._deny()
            self._json(200, {"workspaces": list_workspaces()})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        path = p.path
        if path == "/api/login":
            self._login()
            return
        if path == "/api/notify":
            # agents on other machines post alerts here with a machine token
            user = self._auth()
            if not user:
                return self._deny()
            body = self._read_json()
            note_page_open()
            pane = str(body.get("pane", ""))[:64]
            title = str(body.get("title", ""))[:200]
            text = str(body.get("body", ""))[:1000]
            _add_alert("agent_alert", text or title, pane=pane)
            notify(title or "Agent alert", text, pane=pane)
            STATE.changed()
            audit(user, "notify", pane)
            self._json(200, {"ok": True})
            return
        if path == "/api/dev/reset_lockout":
            # Localhost only, before the auth gate: lets an automated test that
            # exercises the lockout clear it afterwards, so it never leaves the
            # real user blocked.
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._deny()
                return
            SESSIONS.clear_failures(self._client_ip())
            audit("local", "reset_lockout")
            self._json(200, {"ok": True})
            return
        if not self._auth():
            return self._deny()
        if path == "/api/logout":
            c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            m = c.get("board_session")
            if m:
                SESSIONS.revoke(m.value)
            self._json(200, {"ok": True})
            return
        if path == "/api/answer":
            self._answer()
            return
        if path == "/api/prompt":
            self._prompt()
            return
        if path == "/api/keys":
            self._keys()
            return
        if path == "/api/type":
            self._type()
            return
        if path == "/api/menu":
            self._menu()
            return
        if path == "/api/focus":
            self._focus()
            return
        if path == "/api/hire":
            self._hire()
            return
        if path == "/api/rehire":
            self._rehire()
            return
        if path == "/api/dismiss":
            body = self._read_json()
            key = str(body.get("key", ""))
            with STATE.lock:
                STATE.dismissed.add(key)
                STATE.alerts = [a for a in STATE.alerts if a["key"] != key]
            STATE.changed()
            self._json(200, {"ok": True})
            return
        if path == "/api/remind":
            self._remind()
            return
        if path == "/api/machine_token":
            body = self._read_json()
            action = body.get("action")
            name = str(body.get("name", ""))[:64]
            if action == "create" and name:
                raw = SESSIONS.add_machine_token(name)
                audit("user", "machine_token_create", extra={"name": name})
                self._json(200, {"token": raw})
            elif action == "revoke" and name:
                SESSIONS.revoke_machine_token(name)
                audit("user", "machine_token_revoke", extra={"name": name})
                self._json(200, {"ok": True})
            else:
                self._json(400, {"error": "bad_request"})
            return
        if path == "/api/worktree_remove":
            self._worktree_remove()
            return
        if path == "/api/worktree_create":
            self._worktree_create()
            return
        if path == "/api/worktree_open":
            self._worktree_open()
            return
        if path == "/api/fire":
            self._fire()
            return
        if path == "/api/diff":
            self._diff()
            return
        self._json(404, {"error": "not_found"})

    # -- auth ------------------------------------------------------------
    def _login(self):
        ip = self._client_ip()
        if SESSIONS.locked_out(ip):
            self._json(429, {"error": "locked_out"})
            return
        body = self._read_json()
        pw = str(body.get("password", ""))
        cfg = config()
        stored = cfg["auth"].get("password_hash") or ""
        if stored and verify_password(pw, stored):
            SESSIONS.clear_failures(ip)
            tok = SESSIONS.create()
            # Secure only when the request arrived over TLS (tunnel) or when a
            # remote hostname is configured; otherwise a local http://127.0.0.1
            # browser would refuse to store/send the cookie.
            cfg2 = config()
            secure = (self.headers.get("X-Forwarded-Proto") == "https"
                      or bool(cfg2["remote"]["hostnames"]))
            cookie = (f"board_session={tok}; HttpOnly; "
                      f"{'Secure; ' if secure else ''}SameSite=Strict; Path=/; "
                      f"Max-Age={cfg['auth']['session_hours'] * 3600}")
            data = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            audit("user", "login_ok")
            return
        SESSIONS.record_failure(ip)
        audit("anonymous", "login_fail")
        self._json(401, {"error": "bad_credentials"})

    # -- agent actions ---------------------------------------------------
    def _answer(self):
        body = self._read_json()
        pane = str(body.get("pane", ""))
        if not valid_pane(pane):
            self._json(400, {"error": "bad_pane"})
            return
        # re-read the screen; refuse if the question changed (measured rule)
        with STATE.lock:
            rec = STATE.agents.get(pane)
        if not rec:
            self._json(404, {"error": "no_agent"})
            return
        adapter = get_adapter(rec.get("kind", ""))
        if not adapter:
            self._json(400, {"error": "no_adapter"})
            return
        screen = self._body_screen(pane)
        ask = adapter.parse_prompt(screen)
        if not ask:
            self._json(409, {"error": "prompt_changed",
                             "lines": screen[-30:]})
            return
        answer = body.get("answer")
        steps = adapter.plan_answer(ask, answer)
        if not steps:
            self._json(400, {"error": "cannot_answer"})
            return
        sent = []
        for st in steps:
            if st.keys:
                if not valid_keys(st.keys):
                    self._json(400, {"error": "bad_key", "keys": st.keys})
                    return
                herdr("agent", "send-keys", pane, *st.keys, timeout=10)
                sent.append({"keys": st.keys})
            if st.text:
                herdr("agent", "prompt", pane, st.text, timeout=15)
                sent.append({"text": True})
            time.sleep(st.wait)
        audit("user", "answer", pane, extra={"steps": len(steps)})
        notify("Agent Board answered", f"Answered a prompt for {rec.get('name', pane)}.", pane=pane)
        STATE.changed()
        self._json(200, {"ok": True, "steps": sent})

    def _prompt(self):
        body = self._read_json()
        pane = str(body.get("pane", ""))
        text = str(body.get("text", ""))[:8000]
        if not valid_pane(pane):
            self._json(400, {"error": "bad_pane"})
            return
        if not text.strip():
            self._json(400, {"error": "empty"})
            return
        try:
            herdr("agent", "prompt", pane, text, timeout=20)
        except HerdrError as e:
            self._json(409, {"error": e.code})
            return
        audit("user", "prompt", pane, extra={"len": len(text)})
        STATE.changed()
        self._json(200, {"ok": True})

    def _keys(self):
        body = self._read_json()
        pane = str(body.get("pane", ""))
        keys = [str(k) for k in body.get("keys", [])][:10]
        if not valid_pane(pane):
            self._json(400, {"error": "bad_pane"})
            return
        if not valid_keys(keys):
            self._json(400, {"error": "bad_key"})
            return
        try:
            herdr("agent", "send-keys", pane, *keys, timeout=10)
        except HerdrError as e:
            self._json(409, {"error": e.code})
            return
        audit("user", "keys", pane)
        self._json(200, {"ok": True})

    def _focus(self):
        body = self._read_json()
        pane = str(body.get("pane", ""))
        if not valid_pane(pane):
            self._json(400, {"error": "bad_pane"})
            return
        try:
            herdr("pane", "focus", pane, timeout=10)
        except HerdrError as e:
            self._json(409, {"error": e.code})
            return
        audit("user", "focus", pane)
        self._json(200, {"ok": True})

    def _type(self):
        """Send literal text without Enter, so `/`, `@` and `$` open the
        harness's native completion menu instead of submitting."""
        body = self._read_json()
        pane = str(body.get("pane", ""))
        text = str(body.get("text", ""))
        if not valid_pane(pane):
            self._json(400, {"error": "bad_pane"})
            return
        if len(text) > 2000:
            self._json(400, {"error": "too_long"})
            return
        if not text:
            self._json(400, {"error": "empty"})
            return
        try:
            herdr("pane", "send-text", pane, text, timeout=10)
        except HerdrError as e:
            self._json(409, {"error": e.code})
            return
        audit("user", "type", pane, extra={"len": len(text)})
        self._json(200, {"ok": True})

    def _menu(self):
        """Return the harness's current composer completion menu, if open."""
        body = self._read_json()
        pane = str(body.get("pane", ""))
        if not valid_pane(pane):
            self._json(400, {"error": "bad_pane"})
            return
        with STATE.lock:
            rec = STATE.agents.get(pane)
        kind = rec.get("kind", "") if rec else ""
        adapter = get_adapter(kind)
        if not adapter:
            self._json(400, {"error": "no_adapter"})
            return
        screen = self._body_screen(pane)
        items = [m.to_json() for m in adapter.parse_menu(screen)]
        self._json(200, {"items": items, "kind": kind})

    def _hire(self):
        body = self._read_json()
        kind = str(body.get("kind", ""))
        folder = str(body.get("folder", ""))
        name = str(body.get("name", ""))
        message = str(body.get("message", ""))[:8000]
        workspace = str(body.get("workspace", "new"))[:64]
        workspace_label = str(body.get("workspace_label", ""))[:64]
        use_worktree = bool(body.get("use_worktree"))
        worktree_branch = str(body.get("worktree_branch", ""))[:120]
        worktree_base = str(body.get("worktree_base", ""))[:120]
        yolo = bool(body.get("yolo"))
        if kind not in ADAPTERS:
            self._json(400, {"error": "bad_kind"})
            return
        if not NAME_RE.match(name):
            self._json(400, {"error": "bad_name"})
            return
        folder = os.path.expanduser(folder)
        home = os.path.expanduser("~")
        if not os.path.isdir(folder) or not os.path.realpath(folder).startswith(
                os.path.realpath(home)):
            self._json(400, {"error": "bad_folder"})
            return
        if use_worktree and not repo_root(folder):
            self._json(400, {"error": "not_a_repo"})
            return
        try:
            _hire_impl(kind, folder, name, message, workspace, workspace_label,
                       use_worktree, worktree_branch, worktree_base, yolo)
        except HerdrError as e:
            self._json(409, {"error": e.code, "message": str(e)})
            return
        audit("user", "hire", extra={"kind": kind, "name": name,
                                     "workspace": workspace,
                                     "worktree": use_worktree, "yolo": yolo})
        STATE.changed()
        self._json(200, {"ok": True})

    def _rehire(self):
        body = self._read_json()
        key = str(body.get("key", ""))
        entry = _find_closed(key)
        if not entry:
            self._json(404, {"error": "not_found"})
            return
        try:
            _rehire_impl(entry)
        except HerdrError as e:
            self._json(409, {"error": e.code, "message": str(e)})
            return
        audit("user", "rehire", extra={"name": entry.get("name", "")})
        STATE.changed()
        self._json(200, {"ok": True})

    def _remind(self):
        body = self._read_json()
        pane = str(body.get("pane", ""))
        if not valid_pane(pane):
            self._json(400, {"error": "bad_pane"})
            return
        with STATE.lock:
            rec = STATE.agents.get(pane)
            alerts = [a for a in STATE.alerts if a["pane"] == pane]
        if not rec:
            self._json(404, {"error": "no_agent"})
            return
        msg = "Please address these workflow checks you skipped:\n" + "\n".join(
            f"- {a['message']}" for a in alerts)
        try:
            herdr("agent", "prompt", pane, msg, timeout=20)
        except HerdrError as e:
            self._json(409, {"error": e.code})
            return
        with STATE.lock:
            for a in alerts:
                STATE.handled.add(a["key"])
        audit("user", "remind", pane, extra={"count": len(alerts)})
        STATE.changed()
        self._json(200, {"ok": True})

    def _worktree_remove(self):
        body = self._read_json()
        path = str(body.get("path", ""))
        if not path or not os.path.isdir(path):
            self._json(400, {"error": "bad_path"})
            return
        # Refused while anything is unsaved.
        rc, out = git(path, "status", "--porcelain")
        if rc == 0 and out.strip():
            self._json(409, {"error": "dirty", "detail": out[:500]})
            return
        try:
            p = subprocess.run(["git", "-C", path, "worktree", "remove", path],
                               capture_output=True, text=True, timeout=20)
        except (subprocess.TimeoutExpired, OSError):
            self._json(409, {"error": "failed"})
            return
        if p.returncode != 0:
            self._json(409, {"error": "failed", "detail": p.stderr[:500]})
            return
        audit("user", "worktree_remove", extra={"path": path})
        STATE.changed()
        self._json(200, {"ok": True})

    def _worktree_create(self):
        """Create a git worktree-backed workspace via native herdr.

        Measured 0.9.2: `herdr worktree create --cwd <repo> --branch <name>
        [--base <ref>] [--path <path>] [--label <text>] --no-focus`.
        """
        body = self._read_json()
        repo = str(body.get("repo", ""))
        branch = str(body.get("branch", ""))[:120]
        base = str(body.get("base", ""))[:120]
        label = str(body.get("label", ""))[:64]
        home = os.path.expanduser("~")
        if not repo or not os.path.isdir(repo) or not repo_root(repo):
            self._json(400, {"error": "bad_repo"})
            return
        if not branch or not re.match(r"^[A-Za-z0-9._/-]+$", branch):
            self._json(400, {"error": "bad_branch"})
            return
        args = ["worktree", "create", "--cwd", repo, "--branch", branch,
                "--no-focus"]
        if base:
            args += ["--base", base]
        if label:
            args += ["--label", label]
        try:
            herdr(*args, timeout=60)
        except HerdrError as e:
            self._json(409, {"error": e.code, "message": str(e)})
            return
        _ = home
        audit("user", "worktree_create",
              extra={"repo": repo, "branch": branch})
        STATE.changed()
        self._json(200, {"ok": True})

    def _worktree_open(self):
        body = self._read_json()
        path = str(body.get("path", ""))
        if not path or not os.path.isdir(path):
            self._json(400, {"error": "bad_path"})
            return
        try:
            herdr("worktree", "open", "--path", path, "--no-focus", timeout=30)
        except HerdrError as e:
            self._json(409, {"error": e.code, "message": str(e)})
            return
        audit("user", "worktree_open", extra={"path": path})
        STATE.changed()
        self._json(200, {"ok": True})

    def _fire(self):
        """Fire an agent: close its pane, which stops the harness process.

        Measured 0.9.2: closing a pane's sole tab removes the tab/workspace
        too. The snapshot loop then moves the agent into 'closed', so it stays
        rehirable from the roster like any other vanished agent.

        If the agent owned a worktree, remove it too — but only when safe
        (clean tree, no unpushed commits). Never uses --force.
        """
        body = self._read_json()
        pane = str(body.get("pane", ""))
        if not valid_pane(pane):
            self._json(400, {"error": "bad_pane"})
            return
        with STATE.lock:
            rec = STATE.agents.get(pane)
        if not rec:
            self._json(404, {"error": "no_agent"})
            return
        try:
            herdr("pane", "close", pane, timeout=20)
        except HerdrError as e:
            self._json(409, {"error": e.code})
            return
        # Record it as closed now, so it is rehirable before the next snapshot.
        with STATE.lock:
            rec = STATE.agents.pop(pane, rec)
            rec["closed_at"] = time.time()
            STATE.closed.append(rec)
            STATE.alerts = [a for a in STATE.alerts if a["pane"] != pane]
        # Clear the agent's worktree after the pane is gone, so nothing is
        # holding it open. A refusal is surfaced, never forced.
        wt_result = ""
        if rec.get("worktree_path"):
            wt_result = remove_agent_worktree(rec)
        audit("user", "fire", pane,
              extra={"name": rec.get("name", ""),
                     "worktree_removed": not wt_result,
                     "worktree_skip": wt_result})
        STATE.changed()
        self._json(200, {"ok": True, "worktree_removed": not wt_result,
                         "worktree_skip": wt_result})

    def _diff(self):
        body = self._read_json()
        repo = str(body.get("repo", ""))
        path = str(body.get("path", ""))
        sha = str(body.get("sha", ""))
        if not repo or not os.path.isdir(repo):
            self._json(400, {"error": "bad_repo"})
            return
        if sha:
            if not SHA_RE.match(sha):
                self._json(400, {"error": "bad_sha"})
                return
            diff = git_diff_commit(repo, sha)
        elif path:
            diff = git_diff_file(repo, path)
        else:
            self._json(400, {"error": "bad_request"})
            return
        audit("user", "diff", extra={"repo": repo, "sha": sha, "path": path})
        self._json(200, {"diff": diff})

    # -- SSE -------------------------------------------------------------
    def _serve_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = STATE.subscribe()
        last_push = 0.0
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                item = q.get(timeout=25)
                now = time.time()
                if item is None:
                    # keep-alive comment; not a real change
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                # Only push when something real changed, at most 1/s.
                if now - last_push < 1.0:
                    continue
                last_push = now
                payload = json.dumps({"type": "changed", "t": int(now)})
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            STATE.unsubscribe(q)

    # -- static ----------------------------------------------------------
    def _serve_static(self, rel: str):
        safe = os.path.normpath(rel).lstrip("/")
        base = ROOT / "web" / "dist"
        full = (base / safe).resolve()
        if not str(full).startswith(str(base.resolve())) or not full.exists():
            # SPA fallback
            full = base / "index.html"
        if not full.exists():
            self._json(404, {"error": "no_build",
                             "hint": "run npm run build in web/"})
            return
        ctype = {
            ".html": "text/html", ".js": "text/javascript",
            ".css": "text/css", ".json": "application/json",
            ".svg": "image/svg+xml", ".png": "image/png",
            ".woff2": "font/woff2", ".map": "application/json",
        }.get(full.suffix, "application/octet-stream")
        data = full.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# self-check: asserts over parsers, plan_answer, workflow rules, auth
# --------------------------------------------------------------------------

def _selfcheck() -> None:
    from adapters.claude import ClaudeAdapter
    from adapters.codex import CodexAdapter
    from adapters.opencode import OpencodeAdapter

    # pane / sha / name / key validation
    assert valid_pane("w1:p1")
    assert valid_pane("wA:p1")
    assert valid_pane("w10:p23")
    assert not valid_pane("w1"), "missing pane half"
    assert not valid_pane("w1:p")
    assert not valid_pane("1:p1")
    assert valid_keys(["enter", "esc", "up", "ctrl+c", "1", "9"])
    assert not valid_keys(["home"]), "home is rejected by herdr"
    assert not valid_keys(["pageup"])
    assert not valid_keys(["rm"])
    assert SHA_RE.match("a1b2c3d")
    assert SHA_RE.match("a" * 40)
    assert not SHA_RE.match("XYZ")
    assert not SHA_RE.match("a" * 41)
    assert NAME_RE.match("reviewer")
    assert not NAME_RE.match("Bad Name")
    assert TICKET_RE.search("feature/ABC-123-thing")

    # password hashing
    h = hash_password("hunter2")
    assert verify_password("hunter2", h)
    assert not verify_password("hunter3", h)
    assert "hunter2" not in h

    # origin allow-list
    cfg = config()
    assert origin_allowed(f"127.0.0.1:{cfg['port']}", None)
    assert origin_allowed(f"localhost:{cfg['port']}", f"http://localhost:{cfg['port']}")
    assert not origin_allowed("evil.com:8792", None)
    assert not origin_allowed("127.0.0.1:8792", "http://evil.com")

    # claude prompt parsing: single select + preview cut
    ca = ClaudeAdapter()
    ask = ca.parse_prompt([
        "Do you want to proceed?",
        "❯ 1. Yes          ┌──────────┐",
        "  2. No           │ preview  │",
        "     Allow once   └──────────┘",
        "Enter to select  Esc to cancel",
    ])
    assert ask is not None and not ask.multi
    nums = [o.number for o in ask.options]
    assert nums == [1, 2], nums
    assert ask.options[0].selected
    assert "┌" not in ask.options[0].label
    # single select, digit submits (no Enter) when no preview description
    steps = ca.plan_answer(ask, 1)
    assert steps and steps[0].keys == ["1"], steps

    # multi select with submit row
    ask2 = ca.parse_prompt([
        "Choose:", "[ ] A", "[✔] B", "❯   Submit",
        "Enter to confirm  Esc to cancel",
    ])
    assert ask2 is not None and ask2.multi
    assert ask2.submit_row == "Submit"
    steps2 = ca.plan_answer(ask2, {"multi": ["A"]})
    flat = [k for s in steps2 for k in s.keys]
    assert "1" in flat and "enter" in flat, flat

    # trust dialog: arrows + enter
    ask3 = ca.parse_prompt(["Do you trust the files in this folder?",
                            "Enter to confirm  Esc to cancel"])
    assert ask3 is not None and ask3.kind == "trust"
    st3 = ca.plan_answer(ask3, None)
    assert st3 and st3[-1].keys == ["enter"]

    # claude status line
    st = ca.status_line(["sonnet", "ctx 42% $1.23 5h 10% ↻1h08m "
                         "7d 3% ↻36h28m auto mode"])
    assert st["context_pct"] == 42
    assert st["cost"] == 1.23
    assert st["usage_5h_pct"] == 10
    assert st["usage_7d_pct"] == 3

    # claude transcript line parsing
    from adapters.claude import _parse_line
    evs = _parse_line({"type": "assistant", "message": {
        "content": [{"type": "tool_use", "id": "x", "name": "Edit",
                     "input": {"file_path": "/a/b.py"}}]}})
    assert evs and evs[0].kind == "edit" and evs[0].path == "/a/b.py"
    evs = _parse_line({"type": "assistant", "message": {
        "content": [{"type": "tool_use", "id": "x", "name": "Bash",
                     "input": {"command": "git push origin main"}}]}})
    assert evs and "push" in classify_git(evs[0].command)
    evs = _parse_line({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "x",
         "content": "User has answered your questions: \"Q\"=\"A\". done"}]}})
    assert evs and evs[0].kind == "answer"
    # sidechain skipped
    assert _parse_line({"type": "assistant", "isSidechain": True,
                        "message": {"content": [{"type": "text", "text": "x"}]}}) == []
    # queue-operation enqueue
    evs = _parse_line({"type": "queue-operation", "operation": "enqueue",
                       "content": "hello"})
    assert evs and evs[0].kind == "prompt"

    # codex adapter basics
    cx = CodexAdapter()
    assert cx.supports["resume"]
    assert cx.resume_args("abc") == ["resume", "abc"]
    # codex composer menu (`/` measured: '› /model   choose what model…')
    cm = cx.parse_menu([
        "› /model         choose what model and reasoning effort to use",
        "  /fast          2x speed, increased usage",
        "  /permissions   choose what Codex is allowed to do",
        "› /",
    ])
    assert len(cm) == 3, cm
    assert cm[0].trigger == "/" and cm[0].label == "/model" and cm[0].selected
    assert cm[1].label == "/fast" and cm[1].detail.startswith("2x speed")
    # codex `$` picker (measured: bare label + [Skill] detail)
    sm = cx.parse_menu([
        "› Analytics Dashboard           [Skill] Create spreadsheets with the template",
        "  Business Review               [Skill] Create presentations with the template",
    ])
    assert len(sm) == 2 and sm[0].kind == "skill", sm
    assert sm[0].label == "Analytics Dashboard"
    # codex `@` picker (measured: bare label + description + Plugin)
    am = cx.parse_menu([
        "› GitHub                Triage PRs, issues, CI, and publish flows     Plugin",
    ])
    assert len(am) == 1 and am[0].trigger == "@" and am[0].kind == "plugin", am
    # footer rows are not menu items
    assert cx.parse_menu(["  enter insert · esc close"]) == []
    # opencode menus
    oc2 = OpencodeAdapter()
    om = oc2.parse_menu([
        "  ┃ /agents       Switch agent          ┃",
        "  ┃ /compact      Compact session        ┃",
        "  ┃  /                                  ┃",
    ])
    assert len(om) == 2 and om[0].label == "/agents", om
    assert om[0].detail.startswith("Switch")
    atm = oc2.parse_menu(["  ┃ @explore   ┃", "  ┃ @general   ┃"])
    assert len(atm) == 2 and atm[0].trigger == "@", atm
    # claude menu
    clm = ca.parse_menu([
        "❯ /clear    Clear conversation history",
        "  /compact  Compact the conversation",
    ])
    assert len(clm) == 2 and clm[0].selected, clm
    assert clm[0].label == "/clear"
    cxask = cx.parse_prompt([
        "Continue only if you trust these files.",
        "› 1. Trust and continue",
        "  2. Back to Agent Command Center",
        "  enter continue · esc back",
    ])
    assert cxask is not None and cxask.options[0].selected
    assert cxask.options[0].label == "Trust and continue"
    assert cx.plan_answer(cxask, 1)[0].keys == ["1"]
    # Codex idle input box must NOT parse as a menu (measured false positive).
    assert cx.parse_prompt([
        "›Ask Codex to do anything",
        "  GPT-6-Astra default · ~/board-scratch",
        "  ← for agents · ? for shortcuts",
    ]) is None
    # codex discovers its own session id when herdr reports none.
    assert isinstance(cx.discover_session("/home/ubuntu"), str)

    # opencode adapter basics
    oc = OpencodeAdapter()
    assert oc.resume_args("ses_x") == ["--session", "ses_x"]

    # workflow: git classification
    assert "push" in classify_git("git -C /repo push origin main")
    assert "stage_all" in classify_git("git add -A")
    assert "stage_all" in classify_git("git commit -am 'x'")
    assert "commit" in classify_git("git commit -m x")
    assert "stage_all" not in classify_git("git commit --amend -m x")
    assert "show" in classify_git("git show abc1234:file.py")
    assert "pr" in classify_git("gh pr create --title 'ABC-1 - a - b - 1'")

    # edited code files
    ev = [{"kind": "edit", "path": "/x/y.py"}, {"kind": "edit", "path": "/x/z.txt"},
          {"kind": "bash", "command": "git commit -m x"}]
    code = edited_code_files(ev)
    assert "/x/y.py" in code and "/x/z.txt" not in code

    # stations
    stt = compute_stations({"pane": "w1:p1"}, ev, None)
    names = [s for s, _ in stt["stations"]]
    assert names == STATIONS
    assert stt["current"] in STATIONS

    # session lockout
    S = Sessions()
    S.failures["1.2.3.4"] = [time.time()] * 10
    assert S.locked_out("1.2.3.4")
    S.clear_failures("1.2.3.4")
    assert not S.locked_out("1.2.3.4")

    # machine tokens hashed, not stored raw
    S2 = Sessions()
    raw = S2.add_machine_token("box1")
    assert S2.check_machine_token(raw) == "box1"
    assert S2.machine_tokens["box1"] != raw
    assert all(v != raw for v in S2.machine_tokens.values())

    # hire targets: a workspace name is optional, a tab is added to an existing
    # workspace; the router picks tab-create vs workspace-create.
    assert _workspace_mode("new", "") == "create"
    assert _workspace_mode("wE", "") == "tab"
    assert _workspace_mode("", "") == "create"
    # folder picker prunes dot-dirs and depth beyond 4.
    fl = list_folders("~")
    assert fl["path"] == os.path.realpath(os.path.expanduser("~"))
    assert all(not d["name"].startswith(".") for d in fl["dirs"])
    assert list_folders("/etc")["path"] == fl["home"], "outside $HOME is clamped"

    # ticket inference: first source wins, and each is a concrete observed value
    rec = {"tab_label": "opencode-coding"}
    ev_prompt = [{"kind": "prompt", "text": "/clear"},
                 {"kind": "prompt", "text": "wire up the checkout endpoint"}]
    t = derive_ticket(rec, ev_prompt, "/home/x/board-scratch", "feature/ABC-123-api",
                      worktree_branch="feature/ABC-123-wt")
    assert t["name"] == "ABC-123" and t["source"] == "worktree branch", t
    t = derive_ticket(rec, ev_prompt, "/home/x/board-scratch", "feature/ABC-123-api")
    assert t["name"] == "ABC-123" and t["source"] == "branch ticket", t
    t = derive_ticket(rec, ev_prompt, "/home/x/board-scratch", "spike/login")
    assert t["name"] == "spike/login" and t["source"] == "branch", t
    t = derive_ticket(rec, ev_prompt, "/home/x/board-scratch", "master")
    assert t["name"] == "opencode-coding" and t["source"] == "tab label", t
    t = derive_ticket({"tab_label": "1"}, ev_prompt, "/home/x/board-scratch", "master")
    assert t["name"].startswith("wire up") and t["source"] == "first prompt", t
    t = derive_ticket({"tab_label": "1"}, [], "/home/x/board-scratch", "master")
    assert t["source"] == "repo" and "board-scratch" in t["name"], t
    t = derive_ticket({"tab_label": "1"}, [], None, "")
    assert t["name"] == "Unnamed work", t
    # branch change to a differently suffixed branch keeps the same ticket id
    assert _branch_ticket("feature/ABC-123-fix") == "ABC-123"

    # stages: ticket takes the furthest stage of its agents
    a1 = {"pane": "w1:p1", "stage": "In progress"}
    a2 = {"pane": "w1:p2", "stage": "Review"}
    assert ticket_stage([a1, a2]) == "Review"
    assert ticket_stage([]) == "To do"
    assert STAGE_ORDER.index("Shipped") > STAGE_ORDER.index("To do")

    # measured yolo flags per harness
    assert yolo_args("claude") == ["--dangerously-skip-permissions"]
    assert yolo_args("codex") == ["--dangerously-bypass-approvals-and-sandbox"]
    assert yolo_args("opencode") == ["--auto"]
    assert yolo_args("unknown") == []
    # worktree branch defaults are safe slugs
    assert _default_wt_branch("My Agent!") == "agent/my-agent"
    assert _default_wt_branch("") == "agent/agent"

    print("selfcheck: OK")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def _args():
    port = None
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
        if a == "--set-password" and i + 1 < len(sys.argv):
            cfg = load_config()
            cfg["auth"]["password_hash"] = hash_password(sys.argv[i + 1])
            save_config()
            print("password set")
            sys.exit(0)
    return port


def main() -> None:
    load_config()
    _load_machine_tokens()
    STATE_DIR.mkdir(exist_ok=True)
    _selfcheck()
    port = _args() or config()["port"]
    host = config()["bind"]

    threading.Thread(target=poll_loop, daemon=True).start()

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = Server((host, port), Handler)
    print(f"Agent Board on http://{host}:{port} "
          f"(herdr session: {config()['herdr_session']})")
    if config()["remote"]["mode"] != "none":
        print(f"REMOTE ON: {config()['remote']['hostnames']}")
    else:
        print("Remote off. SSH tunnel: "
              f"ssh -L {port}:127.0.0.1:{port} <server>")
    if not config()["auth"].get("password_hash"):
        print("WARNING: no password set. Run: python3 server.py --set-password <pw>")

    def stop(*_):
        # shutdown() blocks until serve_forever() returns; calling it directly
        # from the signal handler in the same thread deadlocks. Run it in a
        # daemon thread and let serve_forever() unwind.
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    srv.serve_forever()


if __name__ == "__main__":
    main()
