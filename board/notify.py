"""Notifications: in-page (the board raises them) and webhook push when no
page is open. Rate-limited to one push per agent per minute; never includes a
secret or the text sent to an agent.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

from .config import config

_notify_lock = threading.Lock()
_notified: dict[str, float] = {}

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
    with _notify_lock:
        last = _notified.get(key, 0)
        if now - last < rate:
            return
        _notified[key] = now
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


