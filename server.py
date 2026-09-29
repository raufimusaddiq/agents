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

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "herdr_session": "default",
    "port": 8792,
    "bind": "127.0.0.1",
    "remote": {"mode": "none", "hostnames": [], "company_managed": False},
    "notifications": {"webhook": {"kind": "none"}, "rate_limit_seconds": 60},
    "docs_repo": {"url": "", "local_path": None},
    "project_roots": [],
    "pr_title_pattern": r"^[A-Z][A-Z0-9]+-\d+ - .+ - .+ - \d+$",
    "auth": {"password_hash": "", "session_hours": 12,
             "lockout_attempts": 10, "lockout_minutes": 15},
    "roster": {"max_closed": 30, "closed_ttl_hours": 24, "save_seconds": 60},
    "workflow": {"recompute_seconds": 10, "git_reread_seconds": 60},
}

_config_lock = threading.Lock()
_config: dict = {}


def load_config() -> dict:
    global _config
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text())
            _deep_merge(cfg, user)
        except (ValueError, OSError) as e:
            print(f"config.json unreadable, using defaults: {e}", file=sys.stderr)
    with _config_lock:
        _config = cfg
    return cfg


def config() -> dict:
    with _config_lock:
        return _config


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


def save_config() -> None:
    CONFIG_PATH.write_text(json.dumps(config(), indent=2) + "\n")


# --------------------------------------------------------------------------
# herdr wrapper: every call goes through here, adds --session, has a timeout
# --------------------------------------------------------------------------

class HerdrError(RuntimeError):
    def __init__(self, code: str, message: str, raw: str = ""):
        super().__init__(message)
        self.code = code
        self.raw = raw


def _herdr_env() -> dict:
    # Measured: a nested client must not think it is inside herdr. Strip HERDR_*.
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}
    env.setdefault("PATH", os.environ.get("PATH", ""))
    return env


def herdr(*args: str, timeout: float = 20.0) -> dict:
    """Run `herdr --session <name> <args...>` and parse JSON from stdout/stderr.

    Measured 0.9.2: CLI server errors are JSON on stderr with exit status 1;
    syntax errors exit 2. The --session flag goes before the subcommand.
    """
    cfg = config()
    cmd = ["herdr", "--session", cfg["herdr_session"], *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=_herdr_env())
    except subprocess.TimeoutExpired:
        raise HerdrError("timeout", f"herdr timed out: {' '.join(args)}")
    except FileNotFoundError:
        raise HerdrError("no_herdr", "herdr binary not found on PATH")
    out = p.stdout.strip()
    err = p.stderr.strip()
    if p.returncode != 0:
        code = "herdr_error"
        msg = err or out or f"exit {p.returncode}"
        try:
            j = json.loads(err or out)
            code = j.get("error", {}).get("code", code) if isinstance(j, dict) else code
            msg = j.get("error", {}).get("message", msg) if isinstance(j, dict) else msg
        except ValueError:
            pass
        raise HerdrError(code, msg, raw=(err or out))
    if not out:
        return {}
    try:
        return json.loads(out)
    except ValueError:
        # Some commands print JSON lines; take the last object.
        for line in reversed(out.splitlines()):
            try:
                return json.loads(line)
            except ValueError:
                continue
        raise HerdrError("bad_json", "herdr returned non-JSON", raw=out[:500])


def herdr_text(*args: str, timeout: float = 20.0) -> str:
    """Run a herdr command that returns plain text (e.g. `agent read`)."""
    cfg = config()
    cmd = ["herdr", "--session", cfg["herdr_session"], *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=_herdr_env())
    except subprocess.TimeoutExpired:
        raise HerdrError("timeout", f"herdr timed out: {' '.join(args)}")
    except FileNotFoundError:
        raise HerdrError("no_herdr", "herdr binary not found on PATH")
    if p.returncode != 0:
        raise HerdrError("herdr_error", p.stderr.strip() or "failed", raw=p.stderr)
    return p.stdout


def snapshot() -> dict:
    j = herdr("api", "snapshot")
    return j.get("result", {}).get("snapshot", {})


# Measured 0.9.2: pane ids look like w1:p3 and go past 9 into letters wA:p1.
PANE_RE = re.compile(r"^w[0-9A-Za-z]+:p[0-9A-Za-z]+$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")  # measured: herdr agent names
TICKET_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")

# Measured 0.9.2 accepted key names. esc is canonical (escape also accepted).
ALLOWED_KEYS = {
    "enter", "backspace", "tab", "shift+tab", "up", "down", "left", "right",
    "esc", "escape", "space",
}
for _c in "abcdefghijklmnopqrstuvwxyz":
    ALLOWED_KEYS.add("ctrl+" + _c)


def valid_pane(pane: str) -> bool:
    return bool(PANE_RE.match(pane or ""))


def valid_keys(keys: list[str]) -> bool:
    return all(k in ALLOWED_KEYS for k in keys)


def read_screen(pane: str, lines: int = 60) -> list[str]:
    if not valid_pane(pane):
        raise HerdrError("bad_pane", "invalid pane id")
    lines = max(1, min(int(lines), 400))
    # Measured 0.9.2: `agent read` prints plain text, not JSON.
    text = herdr_text("agent", "read", pane, "--source", "recent",
                      "--lines", str(lines), timeout=15)
    return text.splitlines()


def _extract_read_text(j: dict) -> str:
    r = j.get("result", j)
    if isinstance(r, dict):
        for key in ("text", "output", "content", "data"):
            if isinstance(r.get(key), str):
                return r[key]
    if isinstance(r, str):
        return r
    return ""


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    n = 2 ** 14
    dk = __import__("hashlib").scrypt(password.encode(), salt=salt, n=n,
                                      r=8, p=1, dklen=32)
    return f"scrypt${n}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, salt_hex, dk_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = __import__("hashlib").scrypt(password.encode(),
                                          salt=bytes.fromhex(salt_hex),
                                          n=int(n), r=8, p=1, dklen=32)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False


class Sessions:
    def __init__(self):
        self.lock = threading.Lock()
        self.tokens: dict[str, float] = {}
        self.failures: dict[str, list[float]] = {}
        self.machine_tokens: dict[str, str] = {}  # name -> hash

    def create(self) -> str:
        tok = secrets.token_urlsafe(32)  # 256 bits
        hours = config()["auth"]["session_hours"]
        with self.lock:
            self.tokens[tok] = time.time() + hours * 3600
        return tok

    def valid(self, tok: str | None) -> bool:
        if not tok:
            return False
        with self.lock:
            exp = self.tokens.get(tok)
            if exp is None:
                return False
            if exp < time.time():
                self.tokens.pop(tok, None)
                return False
            return True

    def revoke(self, tok: str) -> None:
        with self.lock:
            self.tokens.pop(tok, None)

    def revoke_all(self) -> None:
        with self.lock:
            self.tokens.clear()

    def locked_out(self, ip: str) -> bool:
        cfg = config()["auth"]
        with self.lock:
            fails = self.failures.get(ip, [])
            cutoff = time.time() - cfg["lockout_minutes"] * 60
            fails = [t for t in fails if t > cutoff]
            self.failures[ip] = fails
            return len(fails) >= cfg["lockout_attempts"]

    def record_failure(self, ip: str) -> None:
        with self.lock:
            self.failures.setdefault(ip, []).append(time.time())

    def clear_failures(self, ip: str) -> None:
        with self.lock:
            self.failures.pop(ip, None)

    # machine tokens: created/revoked on the settings page, stored hashed
    def add_machine_token(self, name: str) -> str:
        raw = secrets.token_urlsafe(32)
        digest = __import__("hashlib").sha256(raw.encode()).hexdigest()
        with self.lock:
            self.machine_tokens[name] = digest
        _save_machine_tokens()
        return raw

    def check_machine_token(self, raw: str) -> str | None:
        digest = __import__("hashlib").sha256(raw.encode()).hexdigest()
        with self.lock:
            for name, d in self.machine_tokens.items():
                if hmac.compare_digest(d, digest):
                    return name
        return None

    def revoke_machine_token(self, name: str) -> None:
        with self.lock:
            self.machine_tokens.pop(name, None)
        _save_machine_tokens()


SESSIONS = Sessions()


def _save_machine_tokens() -> None:
    SECRETS_PATH.write_text(json.dumps(
        {"machine_tokens": SESSIONS.machine_tokens}, indent=2))
    try:
        os.chmod(SECRETS_PATH, 0o600)
    except OSError:
        pass


def _load_machine_tokens() -> None:
    if SECRETS_PATH.exists():
        try:
            data = json.loads(SECRETS_PATH.read_text())
            SESSIONS.machine_tokens = data.get("machine_tokens", {})
        except (ValueError, OSError):
            pass


def origin_allowed(host: str, origin: str | None) -> bool:
    """Host and Origin must be in the allow-list (blocks DNS rebinding/CSRF)."""
    cfg = config()
    allowed_hosts = {"127.0.0.1:%d" % cfg["port"], "localhost:%d" % cfg["port"]}
    for h in cfg["remote"]["hostnames"]:
        allowed_hosts.add(h)
        allowed_hosts.add(h.split(":")[0])
    host_ok = host in allowed_hosts
    if not host_ok:
        return False
    if origin is None:
        return True  # same-origin XHR may omit Origin; Host still checked
    p = urllib.parse.urlparse(origin)
    return (p.netloc in allowed_hosts) or (p.hostname in
            {h.split(":")[0] for h in allowed_hosts})


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------

_audit_lock = threading.Lock()


def audit(user: str, action: str, pane: str = "", extra: dict | None = None) -> None:
    """Never log the text sent to an agent, or any secret."""
    rec = {"time": time.time(), "user": user, "action": action, "pane": pane}
    if extra:
        for k, v in extra.items():
            if k in ("text", "password", "token", "content", "diff"):
                continue
            rec[k] = v
    line = json.dumps(rec)
    with _audit_lock:
        try:
            with open(AUDIT_PATH, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass


# --------------------------------------------------------------------------
# git (read-only)
# --------------------------------------------------------------------------

def git(repo: str, *args: str, timeout: float = 15.0) -> tuple[int, str]:
    # Measured: always --no-optional-locks so we never take the index lock.
    cmd = ["git", "--no-optional-locks", "-C", repo, *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 1, ""


def repo_root(path: str) -> str | None:
    rc, out = git(path, "rev-parse", "--show-toplevel")
    return out.strip() if rc == 0 and out.strip() else None


def git_status(repo: str) -> dict:
    out: dict = {"repo": repo}
    rc, branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    out["branch"] = branch.strip() if rc == 0 else "?"
    rc, up = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    out["upstream"] = up.strip() if rc == 0 else None
    if out["upstream"]:
        rc, ab = git(repo, "rev-list", "--left-right", "--count",
                     f"{out['upstream']}...HEAD")
        if rc == 0 and ab.strip():
            parts = ab.split()
            out["behind"], out["ahead"] = int(parts[0]), int(parts[1])
    rc, numstat = git(repo, "diff", "HEAD", "--numstat")
    files = []
    for line in numstat.splitlines():
        c = line.split("\t")
        if len(c) == 3:
            files.append({"added": c[0], "removed": c[1], "path": c[2]})
    rc, porcelain = git(repo, "status", "--porcelain=v1", "-z",
                        "--untracked-files=all")
    for entry in porcelain.split("\0"):
        if not entry:
            continue
        code = entry[:2]
        path = entry[3:]
        if not any(f["path"] == path for f in files):
            files.append({"added": "0", "removed": "0", "path": path,
                          "status": code.strip() or "?"})
    out["files"] = files[:300]
    out["files_capped"] = len(files) > 300
    rc, commits = git(repo, "log", "-15", "--pretty=%H|%h|%s|%cI|%d")
    clist = []
    for line in commits.splitlines():
        parts = line.split("|", 4)
        if len(parts) == 5:
            sha, short, subj, date, refs = parts
            pushed = "origin" in refs or "" in refs and False
            # a commit is "pushed" if a remote-tracking ref contains it
            rc2, _ = git(repo, "branch", "-r", "--contains", sha)
            pushed = rc2 == 0
            clist.append({"sha": sha, "short": short, "subject": subj,
                          "date": date, "pushed": pushed})
    out["commits"] = clist
    rc, count = git(repo, "rev-list", "--count", "HEAD", "--not", "--remotes")
    out["unpushed_commits"] = int(count.strip() or 0) if rc == 0 else None
    rc, remotes = git(repo, "remote")
    out["has_remote"] = bool(remotes.strip())
    return out


def git_diff_file(repo: str, path: str) -> str:
    # Only diff files git already lists as changed.
    st = git_status(repo)
    changed = {f["path"] for f in st.get("files", [])}
    if path not in changed:
        return ""
    rc, diff = git(repo, "diff", "HEAD", "--", path)
    return diff if rc == 0 else ""


def git_diff_commit(repo: str, sha: str) -> str:
    if not SHA_RE.match(sha):
        return ""
    rc, diff = git(repo, "show", "--stat", "--patch", sha)
    return diff if rc == 0 else ""


def git_worktrees(repo: str) -> list[dict]:
    rc, out = git(repo, "worktree", "list", "--porcelain")
    if rc != 0:
        return []
    trees, cur = [], {}
    for line in out.splitlines():
        if line.startswith("worktree "):
            if cur:
                trees.append(cur)
            cur = {"path": line[9:]}
        elif line.startswith("branch "):
            cur["branch"] = line[7:]
        elif line.strip() == "detached":
            cur["detached"] = True
    if cur:
        trees.append(cur)
    return trees


# --------------------------------------------------------------------------
# state store
# --------------------------------------------------------------------------

class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.offsets: dict[str, int] = {}
        self.agents: dict[str, dict] = {}       # pane -> agent record
        self.events: dict[str, list[dict]] = {}  # pane -> normalized events
        self.closed: list[dict] = []             # rehirable
        self.alerts: list[dict] = []
        self.dismissed: set[str] = set()
        self.handled: set[str] = set()
        self.notified: dict[str, float] = {}
        self.frozen_roster: list[dict] | None = None
        self.usage_history: dict[str, list[tuple[float, int]]] = {}
        self.last_push = 0.0
        self._subscribers: list = []

    def offset_for(self, key: str) -> int:
        return self.offsets.get(key, 0)

    def set_offset(self, key: str, val: int) -> None:
        self.offsets[key] = val

    def subscribe(self):
        q = _Queue()
        with self.lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q) -> None:
        with self.lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def changed(self) -> None:
        with self.lock:
            subs = list(self._subscribers)
        for q in subs:
            q.put("changed")


class _Queue:
    def __init__(self):
        self.items = []
        self.cv = threading.Condition()

    def put(self, item):
        with self.cv:
            self.items.append(item)
            self.cv.notify_all()

    def get(self, timeout=25):
        with self.cv:
            if not self.items:
                self.cv.wait(timeout)
            return self.items.pop(0) if self.items else None


STATE = State()


def _norm_events(pane: str, events: list[AEvent]) -> list[dict]:
    out = []
    for e in events:
        j = e.to_json()
        j["pane"] = pane
        out.append(j)
    return out


# --------------------------------------------------------------------------
# polling: discover agents and read transcripts incrementally
# --------------------------------------------------------------------------

def reconcile_agents() -> None:
    """Refresh agent records from a herdr snapshot.

    Measured 0.9.2: agent identity lives in snapshot.agents[] (name, agent kind,
    agent_session.value, agent_status, cwd, terminal_title_stripped). The
    panes[] array carries raw panes; index it by pane_id for geometry/focus.
    """
    try:
        snap = snapshot()
    except HerdrError:
        return
    panes = {p.get("pane_id"): p for p in snap.get("panes", [])}
    tabs = {t.get("tab_id"): t for t in snap.get("tabs", [])}
    workspaces = {w.get("workspace_id"): w for w in snap.get("workspaces", [])}
    seen = set()
    now = time.time()
    with STATE.lock:
        for a in snap.get("agents", []):
            pane = a.get("pane_id", "")
            if not valid_pane(pane):
                continue
            seen.add(pane)
            p = panes.get(pane, {})
            sess = a.get("agent_session") or {}
            rec = STATE.agents.get(pane, {})
            rec.update({
                "pane": pane,
                "name": a.get("name") or rec.get("name", ""),
                "kind": a.get("agent") or rec.get("kind", ""),
                "cwd": a.get("cwd") or p.get("cwd") or rec.get("cwd", ""),
                "foreground_cwd": a.get("foreground_cwd", ""),
                "tab_id": a.get("tab_id", ""),
                "workspace_id": a.get("workspace_id", ""),
                "agent_status": a.get("agent_status", "unknown"),
                "title": a.get("terminal_title_stripped", ""),
                "focused": bool(a.get("focused")),
                "interactive_ready": bool(a.get("interactive_ready")),
                "last_seen": now,
                "tab_label": tabs.get(a.get("tab_id"), {}).get("label", ""),
                "ws_label": workspaces.get(a.get("workspace_id"), {}).get("label", ""),
            })
            sid = sess.get("value", "")
            if sid:
                rec["session_id"] = sid
            elif not rec.get("session_id"):
                rec["session_id"] = _session_id_for(pane)
            rec.setdefault("col_since", now)
            STATE.agents[pane] = rec
        # agents that vanished -> closed (rehirable)
        for pane in list(STATE.agents):
            if pane not in seen:
                rec = STATE.agents.pop(pane)
                rec["closed_at"] = now
                STATE.closed.append(rec)
        _prune_closed()


def _detect_kind(p: dict) -> str:
    text = (p.get("terminal_title_stripped") or "") + " " + (p.get("name") or "")
    for kind in ADAPTERS:
        if kind in text.lower():
            return kind
    return ""


def _session_id_for(pane: str) -> str:
    """Ask herdr for the harness session id of the agent in this pane.

    Measured 0.9.2: `herdr agent get <pane>` may expose agent_session.value; the
    exact shape is version-dependent, so we probe several keys.
    """
    try:
        j = herdr("agent", "get", pane, timeout=10)
    except HerdrError:
        return ""
    r = j.get("result", j)
    if not isinstance(r, dict):
        return ""
    for key in ("agent_session", "session"):
        v = r.get(key)
        if isinstance(v, dict) and v.get("value"):
            return str(v["value"])
        if isinstance(v, str) and v:
            return v
    return ""


def poll_transcripts() -> None:
    """Read each agent's transcript incrementally and store normalized events."""
    with STATE.lock:
        agents = list(STATE.agents.values())
    for rec in agents:
        pane = rec["pane"]
        sid = rec.get("session_id", "")
        kind = rec.get("kind", "")
        adapter = get_adapter(kind)
        if not (adapter and sid):
            continue
        try:
            evs = adapter.events(sid, STATE)
        except Exception:
            evs = []
        if evs:
            with STATE.lock:
                STATE.events.setdefault(pane, [])
                STATE.events[pane].extend(_norm_events(pane, evs))
                STATE.events[pane] = STATE.events[pane][-2000:]


def read_screens() -> None:
    """Read screen tails for agents that need questions or status lines."""
    with STATE.lock:
        agents = list(STATE.agents.values())
    for rec in agents:
        pane = rec["pane"]
        kind = rec.get("kind", "")
        adapter = get_adapter(kind)
        try:
            screen = read_screen(pane, 60)
        except HerdrError:
            continue
        with STATE.lock:
            r = STATE.agents.get(pane)
            if not r:
                continue
            r["screen_tail"] = screen[-60:]
            if adapter and adapter.supports.get("status_line"):
                st = adapter.status_line(screen)
                r["status"] = st
                _track_usage(pane, st)
            if adapter and adapter.supports.get("prompts"):
                ask = adapter.parse_prompt(screen)
                r["ask"] = ask.to_json() if ask else None
                if ask and getattr(ask, "kind", "") in ("question", "trust"):
                    r["needs_user"] = True
            else:
                r["ask"] = None


def _track_usage(pane: str, st: dict) -> None:
    pct = st.get("usage_5h_pct")
    if pct is None:
        return
    hist = STATE.usage_history.setdefault(pane, [])
    hist.append((time.time(), pct))
    cutoff = time.time() - 45 * 60
    STATE.usage_history[pane] = [h for h in hist if h[0] > cutoff]


def poll_loop() -> None:
    last = 0.0
    while True:
        try:
            reconcile_agents()
            poll_transcripts()
            read_screens()
            check_roster_restart()
            _detect_alerts()
            cfg = config()
            now = time.time()
            if now - last >= cfg["roster"]["save_seconds"]:
                save_roster()
                last = now
            STATE.changed()
        except Exception as e:  # never let the loop die
            print(f"poll_loop error: {e}", file=sys.stderr)
        time.sleep(3.0)


# --------------------------------------------------------------------------
# roster / rehire
# --------------------------------------------------------------------------

def save_roster() -> None:
    with STATE.lock:
        roster = [{
            "workspace_id": r.get("workspace_id"),
            "tab_id": r.get("tab_id"),
            "cwd": r.get("cwd"),
            "kind": r.get("kind"),
            "name": r.get("name"),
            "session_id": r.get("session_id", ""),
            "pane": r.get("pane"),
        } for r in STATE.agents.values()]
        closed = STATE.closed[-config()["roster"]["max_closed"]:]
    try:
        ROSTER_PATH.write_text(json.dumps(
            {"roster": roster, "closed": closed, "time": time.time()}, indent=2))
    except OSError:
        pass


def check_roster_restart() -> None:
    """If more than half the agents disappear at once, freeze the roster."""
    try:
        data = json.loads(ROSTER_PATH.read_text())
    except (OSError, ValueError):
        return
    prev = data.get("roster", [])
    if not prev:
        return
    with STATE.lock:
        live = len(STATE.agents)
    if live == 0 and len(prev) > 1 and STATE.frozen_roster is None:
        STATE.frozen_roster = prev
        _add_alert("restart", "More than half the agents disappeared at once. "
                   "Rehire them when the harness is back.", pane="")
    elif live >= len(prev) and STATE.frozen_roster is not None:
        STATE.frozen_roster = None


def _prune_closed() -> None:
    cfg = config()["roster"]
    cutoff = time.time() - cfg["closed_ttl_hours"] * 3600
    STATE.closed = [c for c in STATE.closed
                    if c.get("closed_at", 0) > cutoff][-cfg["max_closed"]:]


# --------------------------------------------------------------------------
# workflow rules
# --------------------------------------------------------------------------

CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb", ".java",
            ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".php", ".swift", ".kt",
            ".scala", ".sh", ".sql", ".vue", ".svelte", ".m", ".mm", ".lua",
            ".pl", ".r", ".dart", ".ex", ".exs", ".erl", ".hs", ".clj"}

STATIONS = ["Start check", "Branch", "Code", "Tests", "Review", "Commit",
            "Docs", "Push", "PR"]

GIT_RULES = [
    ("status", re.compile(r"\bgit\s+(-C\s+\S+\s+)?status\b")),
    ("stash", re.compile(r"\bgit\s+(-C\s+\S+\s+)?stash\s+list\b")),
    ("fetch", re.compile(r"\bgit\s+(-C\s+\S+\s+)?fetch\b")),
    ("revlist", re.compile(r"\bgit\s+(-C\s+\S+\s+)?rev-list\s+--count\b")),
    ("stage_all", re.compile(r"\bgit\s+(-C\s+\S+\s+)?add\s+(-A|\.|--all)\b|\bgit\s+(-C\s+\S+\s+)?commit\s+(-[a-z]*a[a-z]*\b|--all)")),
    ("commit", re.compile(r"\bgit\s+(-C\s+\S+\s+)?commit\b")),
    ("show", re.compile(r"\bgit\s+(-C\s+\S+\s+)?show\s+([0-9a-f]{7,40}):")),
    ("push", re.compile(r"\bgit\s+(-C\s+\S+\s+)?push\b")),
    ("pr", re.compile(r"\bgh\s+pr\s+create\b|\bglab\s+mr\s+create\b|/merge_requests\b")),
]


def classify_git(command: str) -> list[str]:
    kinds = []
    for name, rx in GIT_RULES:
        if rx.search(command or ""):
            kinds.append(name)
    # Don't count --amend as commit -a (measured rule).
    if "--amend" in (command or ""):
        kinds = [k for k in kinds if k != "stage_all"]
    return kinds


def edited_code_files(events: list[dict]) -> set[str]:
    out = set()
    for e in events:
        if e.get("kind") == "edit" and e.get("path"):
            if Path(e["path"]).suffix.lower() in CODE_EXT:
                out.add(e["path"])
        if e.get("kind") == "bash":
            for k in classify_git(e.get("command", "")):
                if k == "commit":
                    out.add("__commit__")
    return out


def compute_stations(rec: dict, events: list[dict], repo: str | None) -> dict:
    """Compute the nine stations. The current station is the first not done."""
    done = set()
    skipped = set()
    cmds = [e.get("command", "") for e in events if e.get("kind") == "bash"]
    kinds = []
    for c in cmds:
        kinds.extend(classify_git(c))
    if "status" in kinds or "revlist" in kinds:
        done.add("Start check")
    code = edited_code_files(events)
    if code:
        done.add("Code")
    if "commit" in kinds:
        done.add("Commit")
    if "push" in kinds:
        done.add("Push")
    if "pr" in kinds:
        done.add("PR")
    if repo and "Branch" not in done:
        rc, branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        if rc == 0 and branch.strip() not in ("main", "master", "?"):
            done.add("Branch")
    # Statuses: first not-done station is "in progress".
    order = []
    current_set = False
    for s in STATIONS:
        if s in done:
            order.append((s, "done"))
        elif s in skipped:
            order.append((s, "skipped"))
        elif not current_set:
            order.append((s, "in_progress"))
            current_set = True
        else:
            order.append((s, "todo"))
    first_open = next((s for s, st in order if st in ("in_progress", "todo")), None)
    return {"stations": order, "current": first_open,
            "skipped": sorted(skipped)}


def _detect_alerts() -> None:
    """Workflow alerts from the last 24 hours. Pure heuristics, never guesses."""
    with STATE.lock:
        agents = list(STATE.agents.items())
    cutoff = time.time() - 24 * 3600
    for pane, rec in agents:
        events = STATE.events.get(pane, [])
        repo = repo_root(rec.get("cwd") or "") or None
        commits = [e for e in events if e.get("kind") == "bash"
                   and "commit" in classify_git(e.get("command", ""))]
        if not commits:
            continue
        tests = [e for e in events if e.get("kind") == "subagent_start"
                 and "test" in e.get("subagent_type", "").lower()]
        reviews = [e for e in events if e.get("kind") == "subagent_start"
                   and "review" in e.get("subagent_type", "").lower()]
        code = edited_code_files(events)
        for c in commits:
            key = f"{pane}:{c.get('ts')}:code_no_test"
            if code and not tests and not reviews and key not in STATE.dismissed:
                _add_alert("code_no_test", "A commit carried code but no test "
                           "subagent or test command ran since the previous commit.",
                           pane=pane, key=key)
        # stage-everything
        for e in events:
            if e.get("kind") != "bash":
                continue
            if "stage_all" in classify_git(e.get("command", "")):
                key = f"{pane}:{e.get('ts')}:stage_all"
                if code and key not in STATE.dismissed:
                    _add_alert("stage_all", "Used stage-everything (git add -A / "
                               "commit -a) in a session that changed code.",
                               pane=pane, key=key)


def _add_alert(kind: str, message: str, pane: str = "", key: str = "") -> None:
    key = key or f"{pane}:{kind}:{int(time.time())}"
    with STATE.lock:
        if any(a["key"] == key for a in STATE.alerts):
            return
        STATE.alerts.append({"kind": kind, "message": message, "pane": pane,
                             "key": key, "time": time.time()})
        STATE.alerts = STATE.alerts[-200:]


# --------------------------------------------------------------------------
# notifications: in-page + webhook when no page is open
# --------------------------------------------------------------------------

_last_page_seen = 0.0
_page_lock = threading.Lock()


def note_page_open() -> None:
    global _last_page_seen
    with _page_lock:
        _last_page_seen = time.time()


def page_recently_open(seconds: int = 30) -> bool:
    with _page_lock:
        return (time.time() - _last_page_seen) < seconds


def notify(title: str, body: str, pane: str = "") -> None:
    """Rate-limit to one push per agent per minute. Never include secrets."""
    rate = config()["notifications"].get("rate_limit_seconds", 60)
    key = pane or "global"
    now = time.time()
    with STATE.lock:
        last = STATE.notified.get(key, 0)
        if now - last < rate:
            return
        STATE.notified[key] = now
    if page_recently_open():
        return  # the page raises its own Notification + chime
    _send_webhook(title, body)


def _send_webhook(title: str, body: str) -> None:
    hook = config()["notifications"].get("webhook", {})
    kind = hook.get("kind", "none")
    if kind == "none":
        return
    board_url = _board_url()
    payload = None
    url = None
    if kind == "ntfy":
        url = hook.get("url", "")
        payload = json.dumps({"title": title, "message": body,
                              "click": board_url}).encode()
        headers = {"Content-Type": "application/json"}
    elif kind == "slack":
        url = hook.get("url", "")
        payload = json.dumps({"text": f"*{title}*\n{body}\n{board_url}"}).encode()
        headers = {"Content-Type": "application/json"}
    elif kind == "telegram":
        token = hook.get("bot_token", "")
        chat = hook.get("chat_id", "")
        if not (token and chat):
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat,
                              "text": f"{title}\n{body}\n{board_url}"}).encode()
        headers = {"Content-Type": "application/json"}
    if not url:
        return
    try:
        req = urllib.request.Request(url, data=payload, headers=headers,
                                     method="POST")
        urllib.request.urlopen(req, timeout=10)
    except (urllib.error.URLError, OSError, ValueError):
        pass


def _board_url() -> str:
    cfg = config()
    if cfg["remote"]["hostnames"]:
        return "https://" + cfg["remote"]["hostnames"][0]
    return f"http://127.0.0.1:{cfg['port']}/"


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
        if path in ("/", "/index.html"):
            self._serve_static("index.html")
            return
        if path.startswith("/assets/"):
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
            cookie = (f"board_session={tok}; HttpOnly; Secure; "
                      f"SameSite=Strict; Path=/; Max-Age="
                      f"{cfg['auth']['session_hours'] * 3600}")
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

    def _hire(self):
        body = self._read_json()
        kind = str(body.get("kind", ""))
        folder = str(body.get("folder", ""))
        name = str(body.get("name", ""))
        message = str(body.get("message", ""))[:8000]
        workspace = str(body.get("workspace", "new"))[:64]
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
        try:
            _hire_impl(kind, folder, name, message, workspace)
        except HerdrError as e:
            self._json(409, {"error": e.code, "message": str(e)})
            return
        audit("user", "hire", extra={"kind": kind, "name": name})
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
# board building
# --------------------------------------------------------------------------

def build_board() -> dict:
    from adapters.base import Ask as _Ask
    with STATE.lock:
        agents = list(STATE.agents.values())
        events = {p: list(v) for p, v in STATE.events.items()}
        closed = list(STATE.closed)
        alerts = list(STATE.alerts)
        frozen = STATE.frozen_roster
    tickets: dict[str, list] = {}
    cards = []
    for rec in agents:
        pane = rec["pane"]
        evs = events.get(pane, [])
        repo = repo_root(rec.get("cwd") or "") or None
        stations = compute_stations(rec, evs, repo)
        branch = ""
        if repo:
            rc, b = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
            branch = b.strip() if rc == 0 else ""
        tid = ""
        m = TICKET_RE.search(branch) or TICKET_RE.search(rec.get("title", ""))
        if m:
            tid = m.group(0)
        col = classify_column(rec, stations)
        card = {
            "pane": pane, "name": rec.get("name") or pane, "kind": rec.get("kind"),
            "cwd": rec.get("cwd"), "repo": repo, "branch": branch,
            "ticket": tid or "No ticket",
            "stations": stations["stations"], "current_station": stations["current"],
            "status": rec.get("status", {}),
            "agent_status": rec.get("agent_status"),
            "needs_user": bool(rec.get("needs_user")),
            "ask": rec.get("ask"),
            "last_line": _last_line(rec, evs),
            "column": col,
            "time_in_column": int(time.time() - rec.get("col_since", time.time())),
            "unpushed": None,
            "context_pct": rec.get("status", {}).get("context_pct"),
        }
        if repo:
            rc, count = git(repo, "rev-list", "--count", "HEAD", "--not", "--remotes")
            card["unpushed"] = int(count.strip() or 0) if rc == 0 else None
        cards.append(card)
        tickets.setdefault(card["ticket"], []).append(card)

    wts = list_worktrees()
    return {
        "cards": cards,
        "tickets": {k: v for k, v in tickets.items()},
        "alerts": [a for a in alerts if a["key"] not in STATE.handled][-50:],
        "closed": closed[-30:],
        "frozen_roster": frozen,
        "worktrees": wts,
        "remote_on": config()["remote"]["mode"] != "none",
        "columns": ["Idle", "In progress", "Testing", "Review", "Needs you",
                    "Ready to push", "Shipped", "Parked"],
        "now": int(time.time()),
    }


def classify_column(rec: dict, stations: dict) -> str:
    if rec.get("needs_user") or rec.get("ask"):
        return "Needs you"
    kinds = set()
    for e in STATE.events.get(rec["pane"], []):
        if e.get("kind") == "bash":
            kinds.update(classify_git(e.get("command", "")))
    done = {s for s, st in stations["stations"] if st == "done"}
    if "PR" in done or "Push" in done:
        return "Shipped"
    if "Commit" in done:
        return "Ready to push"
    if "Tests" in done or any(e.get("kind") == "subagent_start" and "test" in
                              e.get("subagent_type", "").lower()
                              for e in STATE.events.get(rec["pane"], [])):
        return "Testing"
    if "Review" in done or any("review" in e.get("subagent_type", "").lower()
                               for e in STATE.events.get(rec["pane"], [])
                               if e.get("kind") == "subagent_start"):
        return "Review"
    if "Code" in done:
        return "In progress"
    return "Idle"


def _last_line(rec: dict, events: list[dict]) -> str:
    for e in reversed(events):
        if e.get("kind") == "reply" and e.get("text"):
            return e["text"].splitlines()[0][:160]
        if e.get("kind") == "prompt" and e.get("text"):
            return e["text"].splitlines()[0][:160]
    tail = rec.get("screen_tail") or []
    for ln in reversed(tail):
        if ln.strip():
            return ln.strip()[:160]
    return ""


def build_agent(pane: str) -> dict:
    with STATE.lock:
        rec = STATE.agents.get(pane)
        events = list(STATE.events.get(pane, []))
        alerts = [a for a in STATE.alerts if a["pane"] == pane]
    if not rec:
        return {"error": "not_found"}
    repo = repo_root(rec.get("cwd") or "")
    status_git = git_status(repo) if repo else None
    adapter = get_adapter(rec.get("kind", ""))
    chat = _build_chat(events)
    return {
        "pane": pane, "name": rec.get("name") or pane, "kind": rec.get("kind"),
        "cwd": rec.get("cwd"), "repo": repo, "git": status_git,
        "chat": chat, "alerts": alerts,
        "supports": adapter.supports if adapter else {},
        "notes": adapter.notes if adapter else [],
        "ask": rec.get("ask"), "screen_tail": rec.get("screen_tail", []),
        "status": rec.get("status", {}),
        "usage_history": STATE.usage_history.get(pane, []),
        "rehire_key": pane,
    }


def _build_chat(events: list[dict]) -> list[dict]:
    """Prompts, replies, tool runs folded into one row, Q&A pairs."""
    out = []
    for e in events:
        k = e.get("kind")
        if k in ("prompt", "reply", "answer", "question"):
            out.append(e)
        elif k in ("edit", "bash", "subagent_start", "subagent_end", "tokens"):
            if out and out[-1].get("_fold") == k:
                out[-1]["_count"] = out[-1].get("_count", 1) + 1
                continue
            row = dict(e)
            row["_fold"] = k
            row["_count"] = 1
            out.append(row)
    return out[-500:]


# --------------------------------------------------------------------------
# worktrees (parked)
# --------------------------------------------------------------------------

def list_worktrees() -> list[dict]:
    roots = set()
    with STATE.lock:
        for rec in STATE.agents.values():
            r = repo_root(rec.get("cwd") or "")
            if r:
                roots.add(r)
    for r in config().get("project_roots", []):
        rr = repo_root(os.path.expanduser(r))
        if rr:
            roots.add(rr)
    out = []
    for repo in roots:
        for wt in git_worktrees(repo):
            path = wt.get("path", "")
            if not path or path == repo:
                continue
            if not os.path.isdir(path):
                continue
            with STATE.lock:
                occupied = any(rec.get("cwd", "").startswith(path)
                               for rec in STATE.agents.values())
            if occupied:
                continue
            rc, dirty = git(path, "status", "--porcelain")
            uncommitted = len([x for x in dirty.splitlines() if x.strip()])
            rc, count = git(path, "rev-list", "--count", "HEAD", "--not",
                            "--remotes", "--not", "main", "--not", "master")
            out.append({"repo": repo, "path": path,
                        "branch": wt.get("branch", ""),
                        "uncommitted": uncommitted,
                        "unmerged": int(count.strip() or 0) if rc == 0 else 0})
    return out


# --------------------------------------------------------------------------
# hire / rehire implementations
# --------------------------------------------------------------------------

def _hire_impl(kind: str, folder: str, name: str, message: str,
               workspace: str) -> None:
    """Hire needs an existing shell pane (measured 0.9.2)."""
    pane = _new_shell_pane(workspace, folder)
    # agent start fails with agent_pane_busy just after tab/pane creation.
    last_err = None
    for _ in range(20):
        try:
            herdr("agent", "start", name, "--kind", kind, "--pane", pane,
                  "--timeout", "60000", timeout=75)
            last_err = None
            break
        except HerdrError as e:
            last_err = e
            if e.code in ("agent_pane_busy", "agent_not_ready"):
                time.sleep(1.5)
                continue
            raise
    if last_err:
        raise last_err
    if message.strip():
        # send once the agent is ready
        for _ in range(20):
            try:
                herdr("agent", "prompt", pane, message, timeout=20)
                break
            except HerdrError as e:
                if e.code in ("agent_not_ready", "agent_blocked"):
                    time.sleep(1.5)
                    continue
                break


def _new_shell_pane(workspace: str, folder: str) -> str:
    if workspace and workspace != "new":
        # create a tab in the named workspace
        j = herdr("tab", "create", "--workspace", workspace, "--cwd", folder,
                  "--label", "agent", timeout=20)
        r = j.get("result", j)
        pane = r.get("root_pane", {}).get("pane_id") or r.get("pane_id", "")
        if valid_pane(pane):
            return pane
    else:
        j = herdr("workspace", "create", "--label", os.path.basename(folder),
                  "--cwd", folder, "--no-focus", timeout=20)
        r = j.get("result", j)
        pane = r.get("root_pane", {}).get("pane_id") or r.get("pane_id", "")
        if valid_pane(pane):
            return pane
    # fallback: split the focused pane
    try:
        j = herdr("pane", "split", "--direction", "right", "--cwd", folder,
                  "--no-focus", timeout=20)
        r = j.get("result", j)
        pane = r.get("pane", {}).get("pane_id") or r.get("pane_id", "")
        if valid_pane(pane):
            return pane
    except HerdrError:
        pass
    raise HerdrError("no_pane", "could not create a shell pane for hire")


def _rehire_impl(entry: dict) -> None:
    name = entry.get("name", "")
    kind = entry.get("kind", "")
    folder = entry.get("cwd") or os.path.expanduser("~")
    sid = entry.get("session_id", "")
    pane = _new_shell_pane("new", folder)
    args = []
    adapter = get_adapter(kind)
    if sid and adapter:
        args = adapter.resume_args(sid)
    last = None
    for _ in range(20):
        try:
            cmd = ["agent", "start", name, "--kind", kind, "--pane", pane,
                   "--timeout", "60000"]
            if args:
                cmd += ["--", *args]
            herdr(*cmd, timeout=75)
            last = None
            break
        except HerdrError as e:
            last = e
            if e.code in ("agent_pane_busy", "agent_not_ready"):
                time.sleep(1.5)
                continue
            raise
    if last:
        raise last


def _find_closed(key: str) -> dict | None:
    with STATE.lock:
        for c in STATE.closed:
            if c.get("pane") == key or c.get("name") == key:
                return c
    return None


def settings_public() -> dict:
    cfg = config()
    return {
        "session": cfg["herdr_session"],
        "remote": {"mode": cfg["remote"]["mode"],
                   "hostnames": cfg["remote"]["hostnames"]},
        "webhook_kind": cfg["notifications"].get("webhook", {}).get("kind", "none"),
        "docs_repo": cfg["docs_repo"],
        "machine_tokens": list(SESSIONS.machine_tokens.keys()),
        "adapters": capability_table(),
    }



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
    assert valid_keys(["enter", "esc", "up", "ctrl+c"])
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
        srv.shutdown()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    srv.serve_forever()


if __name__ == "__main__":
    main()
