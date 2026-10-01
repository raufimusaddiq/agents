"""Agent Board server. Python 3 standard library only.

HTTP entry point backed by the board package. Includes startup self-check.

Security model: the server binds 127.0.0.1 only. Remote access is via a tunnel
(Tailscale/Cloudflare) with login in front. All /api and the WebSocket require a
valid session cookie or a machine token in X-Board-Token, plus a Host/Origin
allow-list. Every action that reaches an agent or git is audited.
"""
from __future__ import annotations

from datetime import datetime
import hmac
import http.server
import json
import os
import re
import secrets
import shlex
import signal
import socket
import socketserver
import sqlite3
import subprocess
import sys
import tempfile
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
UPLOADS = ROOT / "uploads"


def _save_upload(name: str, data: bytes) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name or "file").name)
    safe = safe.lstrip(".")[:80] or "file"
    d = UPLOADS / time.strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{secrets.token_hex(4)}-{safe}"
    p.write_bytes(data)
    p.chmod(0o600)
    return p


def _upload_path(q: str) -> Path | None:
    """Only files this app saved: anything else, including ../ and symlinks out,
    is refused."""
    try:
        p = Path(q).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return p if UPLOADS.resolve() in p.parents and p.is_file() else None
from board.config import DEFAULT_CONFIG, _config_lock, load_config, config, _deep_merge, save_config
from board.herdr import HerdrError, _herdr_env, herdr, herdr_text, snapshot, PANE_RE, SHA_RE, NAME_RE, TICKET_RE, ALLOWED_KEYS, valid_pane, valid_keys, read_screen, _extract_read_text
from board.security import hash_password, verify_password, Sessions, SESSIONS, _secrets_lock, _save_machine_tokens, _load_machine_tokens, origin_allowed, _audit_lock, audit
from board.gitrepo import git, repo_root, git_status, git_diff_file, git_diff_commit
from board.core import State, _Queue, STATE, _norm_events, reconcile_agents, _detect_kind, _session_id_for, poll_transcripts, read_screens, _NEEDS_USER_MARKERS, _screen_needs_user, _track_usage, feedback_signature, poll_loop, save_roster, check_roster_restart, _prune_closed, _event_is_recent, _detect_alerts, _add_alert, build_board, _compat_cards, _worktree_index, _last_line, build_agent, _build_chat, chat_payload, agent_cwd, push_payload, risk_payload, usage_payload, digest_payload
from board.workflow import CODE_EXT, STATIONS, shell_commands, classify_git, edited_code_files, has_test_action, has_review_action, compute_stations, STAGE_ORDER, _STATION_TO_STAGE, agent_stage, ticket_stage, _first_prompt, _branch_ticket, derive_ticket
from board.notify import _last_page_seen, _page_lock, note_page_open, page_recently_open, notify, _send_webhook, _board_url
from board.terminal import _WS_TEXT, _WS_BINARY, _WS_CLOSE, _WS_PING, _WS_PONG, _WS_GUID, _ws_accept, _ws_send, _ws_recv, TerminalSession, serve_terminal
from board.suggest import suggest, rank
from board.worktrees import list_worktrees, _repos_from_herdr, _herdr_worktrees, _git_worktrees, _clean_branch, YOLO_ARGS, yolo_args
from board.hire import _hire_impl, _default_wt_branch, create_agent_worktree, worktree_removal_blocker, remove_agent_worktree, _remove_worktree_by_path, _new_shell_pane, _workspace_mode, list_workspaces, _rehire_impl, _find_closed, list_folders, _has_dir, settings_public, personas, save_persona, clean_persona, persona_args, agent_name

def prompt_identity(ask: dict) -> dict:
    """Compare the question and choices, ignoring live cursor/screen changes."""
    if not isinstance(ask, dict) or not isinstance(ask.get("options", []), list):
        raise ValueError("bad_prompt")
    if any(not isinstance(option, dict) for option in ask.get("options", [])):
        raise ValueError("bad_prompt")
    return {
        "question": ask.get("question", ""),
        "kind": ask.get("kind", ""),
        "multi": ask.get("multi", False),
        "tabs": ask.get("tabs", []),
        "options": [{key: option.get(key) for key in
                     ("number", "label", "description", "kind", "preview")}
                    for option in ask.get("options", [])],
    }


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "AgentBoard/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # quiet; audit log is the record

    # -- helpers ---------------------------------------------------------
    def _json(self, code: int, obj, headers: dict | None = None) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("unsupported_transfer_encoding")
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("bad_content_length") from None
        if n < 0:
            raise ValueError("bad_content_length")
        if n > 1_000_000:
            raise ValueError("body_too_large")
        if n == 0:
            return {}
        try:
            body = json.loads(self.rfile.read(n))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("bad_json") from None
        if not isinstance(body, dict):
            raise ValueError("json_object_required")
        return body

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
                self._request_user = f"machine:{name}"
                return self._request_user
        try:
            c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return None
        m = c.get("board_session")
        if m and SESSIONS.valid(m.value):
            self._request_user = "user"
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
        if path == "/api/chat":
            if not self._auth():
                return self._deny()
            note_page_open()
            pane = urllib.parse.parse_qs(p.query).get("pane", [""])[0]
            if not valid_pane(pane):
                self._json(400, {"error": "bad_pane"})
                return
            self._json(200, chat_payload(pane))
            return
        if path in ("/api/pushinfo", "/api/risk", "/api/usage", "/api/digest"):
            if not self._auth():
                return self._deny()
            note_page_open()
            q = urllib.parse.parse_qs(p.query)
            if path == "/api/pushinfo":
                pane = q.get("pane", [""])[0]
                if not valid_pane(pane):
                    self._json(400, {"error": "bad_pane"})
                    return
                self._json(200, push_payload(pane))
            elif path == "/api/risk":
                self._json(200, risk_payload())
            elif path == "/api/usage":
                self._json(200, usage_payload())
            else:
                try:
                    since = float(q.get("since", ["0"])[0])
                except ValueError:
                    since = 0.0
                self._json(200, digest_payload(since))
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
            audit(getattr(self, "_request_user", "user"), "read_screen", pane)
            self._json(200, {"lines": self._body_screen(pane)})
            return
        if path == "/api/suggest":
            if not self._auth():
                return self._deny()
            q = urllib.parse.parse_qs(p.query)
            pane = q.get("pane", [""])[0]
            kind = q.get("kind", ["file"])[0]
            text = q.get("q", [""])[0][:200]
            if not valid_pane(pane):
                self._json(400, {"error": "bad_pane"})
                return
            cwd = agent_cwd(pane)
            self._json(200, {"items": suggest(cwd, kind, text)})
            return
        if path == "/api/upload":
            if not self._auth():
                return self._deny()
            q = urllib.parse.parse_qs(p.query).get("path", [""])[0]
            fp = _upload_path(q)
            if not fp:
                self._json(404, {"error": "not_found"})
                return
            data = fp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
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
        if path == "/api/personas":
            if not self._auth():
                return self._deny()
            self._json(200, {"personas": {
                k: {kk: vv for kk, vv in v.items() if kk != "prompt"}
                | {"has_prompt": bool(v.get("prompt"))}
                for k, v in personas().items()}})
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
        # Close rejected requests: unread bytes must never become a new request.
        self.close_connection = True
        try:
            self._dispatch_post()
        except HerdrError as e:
            self._json(409, {"error": e.code})
        except (TypeError, OverflowError):
            self._json(400, {"error": "bad_request"})
        except ValueError as e:
            self._json(413 if str(e) == "body_too_large" else 400,
                       {"error": str(e)})

    def _dispatch_post(self):
        p = urllib.parse.urlparse(self.path)
        path = p.path
        if path == "/api/login":
            self._login()
            return
        if path == "/api/upload":
            # Raw bytes, not JSON: the file name comes in a header. The file is
            # saved under our own uploads dir and handed to the agent as an
            # @path mention; the agent never sees a client-supplied path.
            user = self._auth()
            if not user:
                return self._deny()
            name = urllib.parse.unquote(self.headers.get("X-Filename", "file"))
            n = int(self.headers.get("Content-Length", "0"))
            if n <= 0 or n > 25_000_000:
                self._json(413, {"error": "too_large"})
                return
            data = self.rfile.read(n)
            p2 = _save_upload(name, data)
            audit(user, "upload", extra={"bytes": len(data)})
            self._json(200, {"ok": True, "path": str(p2)})
            return
        if path == "/api/notify":
            # agents on other machines post alerts here with a machine token
            user = self._auth()
            if not user:
                return self._deny()
            body = self._read_json()
            pane = str(body.get("pane", ""))[:64]
            title = str(body.get("title", ""))[:200]
            text = str(body.get("body", ""))[:1000]
            _add_alert("agent_alert", text or title, pane=pane)
            notify("Agent alert", "An agent posted an alert. Open the board to review it.", pane=pane)
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
            self._json(200, {"ok": True}, {"Set-Cookie": "board_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/"})
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
        if path == "/api/persona":
            body = self._read_json()
            label = str(body.get("label", ""))[:40]
            if not label:
                self._json(400, {"error": "bad_label"})
                return
            p = clean_persona(body.get("persona"))
            save_persona(label, p)  # p None clears it
            audit("user", "persona", extra={"label": label, "set": bool(p)})
            self._json(200, {"ok": True})
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
                audit(getattr(self, "_request_user", "user"), "machine_token_create", extra={"name": name})
                self._json(200, {"token": raw})
            elif action == "revoke" and name:
                SESSIONS.revoke_machine_token(name)
                audit(getattr(self, "_request_user", "user"), "machine_token_revoke", extra={"name": name})
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
        if not origin_allowed(self._host(), self.headers.get("Origin")):
            return self._deny()
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
            local_hosts = {f"127.0.0.1:{cfg2['port']}", f"localhost:{cfg2['port']}"}
            secure = (self.headers.get("X-Forwarded-Proto") == "https"
                      or self._host() not in local_hosts)
            cookie = (f"board_session={tok}; HttpOnly; "
                      f"{'Secure; ' if secure else ''}SameSite=Strict; Path=/; "
                      f"Max-Age={cfg['auth']['session_hours'] * 3600}")
            data = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            audit(getattr(self, "_request_user", "user"), "login_ok")
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
        if not adapter or not adapter.supports.get("prompts"):
            self._json(400, {"error": "no_adapter"})
            return
        screen = self._body_screen(pane)
        ask = adapter.parse_prompt(screen)
        if not ask:
            self._json(409, {"error": "prompt_changed",
                             "lines": screen[-30:]})
            return
        expected = body.get("expected_prompt")
        if expected is not None and prompt_identity(expected) != prompt_identity(ask.to_json()):
            self._json(409, {"error": "prompt_changed"})
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
        audit(getattr(self, "_request_user", "user"), "answer", pane, extra={"steps": len(steps)})
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
        audit(getattr(self, "_request_user", "user"), "prompt", pane, extra={"len": len(text)})
        STATE.changed()
        self._json(200, {"ok": True})

    def _keys(self):
        body = self._read_json()
        pane = str(body.get("pane", ""))
        keys = body.get("keys", [])
        if not valid_pane(pane):
            self._json(400, {"error": "bad_pane"})
            return
        if not keys or not valid_keys(keys):
            self._json(400, {"error": "bad_key"})
            return
        try:
            herdr("agent", "send-keys", pane, *keys, timeout=10)
        except HerdrError as e:
            self._json(409, {"error": e.code})
            return
        audit(getattr(self, "_request_user", "user"), "keys", pane)
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
        audit(getattr(self, "_request_user", "user"), "focus", pane)
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
        audit(getattr(self, "_request_user", "user"), "type", pane, extra={"len": len(text)})
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
        if not os.path.isdir(folder) or not Path(folder).resolve().is_relative_to(
                Path(home).resolve()):
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
        audit(getattr(self, "_request_user", "user"), "hire", extra={"kind": kind, "name": name,
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
        audit(getattr(self, "_request_user", "user"), "rehire", extra={"name": entry.get("name", "")})
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
        audit(getattr(self, "_request_user", "user"), "remind", pane, extra={"count": len(alerts)})
        STATE.changed()
        self._json(200, {"ok": True})

    def _worktree_remove(self):
        body = self._read_json()
        path = str(body.get("path", ""))
        if not path or not os.path.isdir(path):
            self._json(400, {"error": "bad_path"})
            return
        target = Path(path).resolve()
        with STATE.lock:
            occupied = any(rec.get("cwd") and Path(rec["cwd"]).resolve().is_relative_to(target)
                           for rec in STATE.agents.values())
        if occupied:
            self._json(409, {"error": "occupied"})
            return
        reason = worktree_removal_blocker(path)
        if reason:
            self._json(409, {"error": reason})
            return
        try:
            _remove_worktree_by_path(path)
        except HerdrError as e:
            self._json(409, {"error": e.code, "detail": str(e)})
            return
        audit(getattr(self, "_request_user", "user"), "worktree_remove", extra={"path": path})
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
        audit(getattr(self, "_request_user", "user"), "worktree_create",
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
        audit(getattr(self, "_request_user", "user"), "worktree_open", extra={"path": path})
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
        audit(getattr(self, "_request_user", "user"), "fire", pane,
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
        audit(getattr(self, "_request_user", "user"), "diff", extra={"repo": repo, "sha": sha, "path": path})
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
                if not self._auth():
                    break
                now = time.time()
                if item is None:
                    # keep-alive comment; not a real change
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                # Only push when something real changed, at most 1/s.
                if now - last_push < 1.0:
                    time.sleep(1.0 - (now - last_push))
                last_push = time.time()
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
        if not full.is_relative_to(base.resolve()):
            self._json(404, {"error": "not_found"})
            return
        if not full.is_file():
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

    # a working agent is never "needs you", even if tool output mentions login.
    assert not _screen_needs_user(
        ["npm run test:live", "PASS login flow", "Do you want to continue?",
         "", "esc to interrupt"], "working")
    # a real bottom-of-screen prompt does flag it.
    assert _screen_needs_user(
        ["lots of history", "Paste code here if prompted >"],
        "idle")
    # scrolled-off history is not a prompt (bottom of screen is the composer).
    assert not _screen_needs_user(
        ["Do you want to continue?"] + [f"  output line {i}" for i in range(40)]
        + ["› Ask Codex to do anything", "GPT default · ~/repo"], "idle")
    # codex: a numbered menu under the composer is a real prompt.
    assert _screen_needs_user(
        ["Do you want to allow this?", "› 1. Yes", "  2. No"],
        "unknown", "codex")
    # codex: a plain composer with no numbered options is not a prompt.
    assert not _screen_needs_user(
        ["› Ask Codex to do anything", "GPT default · ~/repo"],
        "idle", "codex")

    # upload path guard: only files under our own uploads dir are ever served.
    assert _upload_path("/etc/passwd") is None
    assert _upload_path("/home/ubuntu/.ssh/id_rsa") is None
    # suggestion ranking: prefix beats substring beats subsequence
    items = [{"name": "model"}, {"name": "mcp"}, {"name": "reload-plugins"}]
    top = rank(items, "mod", key=lambda c: c["name"])
    assert top and top[0]["name"] == "model", top

    print("selfcheck: OK")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def _args():
    port = None
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
        if a == "--set-password":
            import getpass
            password = sys.argv[i + 1] if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--") else getpass.getpass("Set board password: ")
            if not password:
                print("Password cannot be empty", file=sys.stderr)
                sys.exit(1)
            cfg = load_config()
            cfg["auth"]["password_hash"] = hash_password(password)
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
    config()["port"] = port
    host = "127.0.0.1"

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
        print("WARNING: no password set. Run: python3 server.py --set-password")

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
