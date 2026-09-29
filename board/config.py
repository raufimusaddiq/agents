"""Configuration: load, merge, read, save."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"

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
    "ticket": {"sources": ["worktree", "branch_ticket", "branch", "tab",
                           "prompt", "repo"], "first_prompt_len": 48},
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
