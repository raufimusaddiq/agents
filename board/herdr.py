"""Agent Board herdr implementation."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
from .config import DEFAULT_CONFIG, _config_lock, load_config, config, _deep_merge, save_config
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
for _d in "0123456789":
    ALLOWED_KEYS.add(_d)
for _c in "abcdefghijklmnopqrstuvwxyz":
    ALLOWED_KEYS.add("ctrl+" + _c)


def valid_pane(pane: str) -> bool:
    return bool(PANE_RE.match(pane or ""))


def valid_keys(keys: list[str]) -> bool:
    return (isinstance(keys, list) and len(keys) <= 100
            and all(isinstance(k, str) and k in ALLOWED_KEYS for k in keys))


def read_screen(pane: str, lines: int = 60) -> list[str]:
    if not valid_pane(pane):
        raise HerdrError("bad_pane", "invalid pane id")
    lines = max(1, min(int(lines), 400))
    # Measured 0.9.2: `agent read` prints plain text, not JSON. When the pane is
    # not idle, `--source recent` can fail (alternate-screen history), so fall
    # back to `--source visible` (measured: visible hides the status line only
    # when scrolled up).
    try:
        text = herdr_text("agent", "read", pane, "--source", "recent",
                          "--lines", str(lines), timeout=15)
    except HerdrError:
        text = herdr_text("agent", "read", pane, "--source", "visible",
                          timeout=15)
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
