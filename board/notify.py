"""Agent Board notify implementation."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
from .config import DEFAULT_CONFIG, _config_lock, load_config, config, _deep_merge, save_config
import board.core as _core
_last_page_seen = 0.0
_page_lock = threading.Lock()


def note_page_open() -> None:
    global _last_page_seen
    with _page_lock:
        _last_page_seen = time.time()


def page_recently_open(seconds: int = 30) -> bool:
    with _core.STATE.lock:
        if _core.STATE._subscribers:
            return True
    with _page_lock:
        return (time.time() - _last_page_seen) < seconds


def notify(title: str, body: str, pane: str = "") -> bool:
    """Rate-limit to one push per agent per minute. Never include secrets."""
    if page_recently_open():
        return False
    rate = config()["notifications"].get("rate_limit_seconds", 60)
    key = pane or "global"
    now = time.time()
    with _core.STATE.lock:
        last = _core.STATE.notified.get(key, 0)
        if now - last < rate:
            return False
        _core.STATE.notified[key] = now
    return _send_webhook(title, body)


def _send_webhook(title: str, body: str) -> bool:
    hook = config()["notifications"].get("webhook", {})
    kind = hook.get("kind", "none")
    if kind == "none":
        return False
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
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat,
                              "text": f"{title}\n{body}\n{board_url}"}).encode()
        headers = {"Content-Type": "application/json"}
    if not url:
        return False
    try:
        req = urllib.request.Request(url, data=payload, headers=headers,
                                     method="POST")
        with urllib.request.urlopen(req, timeout=10):
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _board_url() -> str:
    cfg = config()
    if cfg["remote"]["hostnames"]:
        return "https://" + cfg["remote"]["hostnames"][0]
    return f"http://127.0.0.1:{cfg['port']}/"
