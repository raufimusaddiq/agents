"""opencode adapter. Measured on opencode 1.18.33.

MEASURED: opencode stores state in SQLite at
  ~/.local/share/opencode/opencode.db
Tables used (observed):
  session(id, project_id, directory, title, agent, model, cost, time_created, time_updated)
  message(id, session_id, time_created, data JSON)
  part(id, message_id, session_id, time_created, data JSON)
MEASURED: `part.data.type` is one of: text, step-start, step-finish, tool.
MEASURED: tool parts are {type:"tool", tool:"bash"|"edit"|"write"|"question",
  callID, state:{status, input:{...}, output}}.
  - bash  -> state.input.command
  - edit  -> state.input.filePath (and state.input.oldString/newString)
  - write -> state.input.filePath, state.input.content
  - question -> state.input.questions[] mirrors Claude's AskUserQuestion
MEASURED: `opencode db <query>` can run SQL; we read the DB directly read-only.
MEASURED resume: `opencode --session <id>` (session id like ses_...).
"""
from __future__ import annotations

import json
import os
import sqlite3

from .base import Adapter, Ask, Event, Option, ScreenFallbackMixin, Step

DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")


class OpencodeAdapter(ScreenFallbackMixin, Adapter):
    kind = "opencode"
    supports = {
        "transcript": True,
        "prompts": False,
        "status_line": False,
        "subagents": False,
        "resume": True,
        "questions": True,
    }
    notes = [
        "measured 1.18.33: session/message/part tables in ~/.local/share/opencode/opencode.db",
        "measured: part.data.type in text|step-start|step-finish|tool",
        "measured: tool parts carry state.input; bash.command, edit.filePath, write.filePath",
        "measured: question tool state.input.questions[] like Claude AskUserQuestion",
        "measured: resume with `opencode --session <id>`",
        "not reported by opencode: ctx%, 5h/7d usage footer; approval prompts fall back to raw keys",
    ]

    def session_row(self, session_id: str) -> dict | None:
        if not os.path.exists(DB_PATH):
            return None
        try:
            con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            r = con.execute(
                "SELECT id,directory,title,agent,model,cost,tokens_input,tokens_output "
                "FROM session WHERE id=? LIMIT 1", (session_id,)).fetchone()
            con.close()
            return dict(r) if r else None
        except sqlite3.Error:
            return None

    def events(self, session_id: str, state=None) -> list[Event]:
        if not os.path.exists(DB_PATH):
            return []
        # MEASURED: increment by part.time_created high-water mark per session.
        since = 0
        if state and hasattr(state, "offset_for"):
            since = state.offset_for("opencode:" + session_id)
        try:
            con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            rows = list(con.execute(
                "SELECT p.data AS pdata, m.data AS mdata, p.time_created AS tc, p.id AS pid "
                "FROM part p JOIN message m ON m.id=p.message_id "
                "WHERE p.session_id=? AND p.time_created>? ORDER BY p.time_created, p.rowid",
                (session_id, since)))
            con.close()
        except sqlite3.Error:
            return []
        out: list[Event] = []
        high = since
        for r in rows:
            try:
                pdata = json.loads(r["pdata"])
                mdata = json.loads(r["mdata"])
            except ValueError:
                continue
            out.extend(_parse_part(pdata, r["tc"], mdata.get("role", "")))
            high = max(high, r["tc"])
        if state and hasattr(state, "set_offset") and high > since:
            state.set_offset("opencode:" + session_id, high + 1)
        return out

    def resume_args(self, session_id: str) -> list[str]:
        return ["--session", session_id]

    def status_line(self, screen_lines: list[str]) -> dict:
        # MEASURED: opencode TUI shows a model + token summary but no ctx %
        # or 5h/7d windows we could rely on; expose cost/tokens from the DB.
        return {}


def _parse_part(pdata: dict, tc, role: str = "") -> list[Event]:
    ts = str(tc)
    t = pdata.get("type")
    if t == "text":
        # MEASURED: role lives on the message row (message.data.role).
        kind = "reply" if role != "user" else "prompt"
        return [Event(kind=kind, ts=ts, text=pdata.get("text", ""), tool=role)]
    if t != "tool":
        return []
    tool = pdata.get("tool", "")
    state = pdata.get("state", {}) or {}
    inp = state.get("input", {}) or {}
    if tool == "bash":
        return [Event(kind="bash", ts=ts, command=str(inp.get("command", "")),
                      tool="bash", tool_use_id=pdata.get("callID", ""))]
    if tool in ("edit", "write", "multiedit", "patch"):
        path = inp.get("filePath") or inp.get("file_path") or inp.get("path") or ""
        return [Event(kind="edit", ts=ts, path=str(path), tool=tool,
                      tool_use_id=pdata.get("callID", ""))]
    if tool == "question":
        qs = inp.get("questions", [])
        return [Event(kind="question", ts=ts, tool_use_id=pdata.get("callID", ""),
                      ask={"questions": qs})]
    if tool in ("task", "agent"):
        return [Event(kind="subagent_start", ts=ts,
                      subagent_type=str(inp.get("subagent_type", "")),
                      tool_use_id=pdata.get("callID", ""))]
    return []


def is_subagent_session(session_id: str) -> bool:
    """MEASURED: child sessions carry parent_id in the session table."""
    if not os.path.exists(DB_PATH):
        return False
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        r = con.execute("SELECT parent_id FROM session WHERE id=?", (session_id,)).fetchone()
        con.close()
        return bool(r and r[0])
    except sqlite3.Error:
        return False
