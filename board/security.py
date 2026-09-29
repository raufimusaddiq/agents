"""Authentication, sessions, machine tokens, origin allow-list and audit.

The server binds 127.0.0.1 only; remote access is via a tunnel with this login
in front. The audit log never records the text sent to an agent.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.parse
from pathlib import Path

from .config import config

ROOT = Path(__file__).resolve().parent.parent
AUDIT_PATH = ROOT / "audit.jsonl"
SECRETS_PATH = ROOT / "secrets.json"

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


