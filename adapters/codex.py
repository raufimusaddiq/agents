"""Codex adapter. Measured on codex-cli 0.159.0.

MEASURED: sessions are NOT stored under ~/.codex/<flat dirs> directly.
There is a SQLite index at ~/.codex/state_5.sqlite, table `threads`, columns:
  id, rollout_path, cwd, title, preview, git_branch, first_user_message,
  model, updated_at, tokens_used.
The rollout JSONL lives at ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
and each line is {"timestamp","ordinal","type","payload"}.

MEASURED rollouts line types (observed on 0.159.0):
  session_meta, event_msg, response_item, world_state, turn_context,
  token_usage_record.
MEASURED response_item payloads:
  {type:"message", role:"user"|"assistant"|"developer", content:[{type:"input_text"|"output_text", text}]}
  assistant tool calls appear as payload type "function_call"/"local_shell_call"
  (see _tool_call); exec output is echoed by the CLI as an `exec` block.
MEASURED event_msg payloads: task_started, item_completed, token_count,
  task_complete (last_agent_message), agent_message.
MEASURED resume: `codex resume <SESSION_ID>`.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

from .base import Adapter, Ask, Event, Option, ScreenFallbackMixin, Step

CODEX_DIR = os.path.expanduser("~/.codex")
STATE_DB = os.path.join(CODEX_DIR, "state_5.sqlite")

# Measured 0.159.0: Codex menus use "›" as the selected-row caret and render a
# footer like "enter continue · esc back" (not Claude's "Enter to select").
# Measured option row: "› 1. Trust and continue" / "  2. Back to ...".
CX_OPT_RE = re.compile(r"^\s*(›|\*)?\s*(\d+)\.\s+(.*)$")
CX_CARET_RE = re.compile(r"^\s*(›)\s+(\S.*)$")
CX_FOOTER_RE = re.compile(r"(enter\s+\w+.*esc\s+\w+|esc\s+\w+.*enter\s+\w+)", re.I)


class CodexAdapter(ScreenFallbackMixin, Adapter):
    kind = "codex"
    # MEASURED: Codex reports tool calls and token counts, but its on-screen
    # status line is not the Claude-style ctx/5h/7d footer, and approval UIs
    # are TUI-drawn (fall back to raw keys). No subagent concept observed.
    supports = {
        "transcript": True,
        "prompts": True,
        "status_line": True,
        "subagents": False,
        "resume": True,
        "questions": True,
    }
    notes = [
        "measured 0.159.0: sessions indexed in ~/.codex/state_5.sqlite table threads",
        "measured: rollout jsonl at ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl",
        "measured: assistant text in response_item payload type=message role=assistant, content[].output_text",
        "measured: user text in response_item role=user content[].input_text",
        "measured: token_count in event_msg payload type=token_count info.total_token_usage",
        "measured: resume with `codex resume <id>`",
        "not reported by codex: ctx%, 5h/7d usage footer; approval prompts fall back to raw keys",
    ]

    def session_row(self, session_id: str) -> dict | None:
        if not os.path.exists(STATE_DB):
            return None
        try:
            con = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            r = con.execute(
                "SELECT id,rollout_path,cwd,title,model FROM threads WHERE id=? LIMIT 1",
                (session_id,)).fetchone()
            con.close()
            return dict(r) if r else None
        except sqlite3.Error:
            return None

    def transcript_path(self, session_id: str) -> str | None:
        row = self.session_row(session_id)
        if row and row.get("rollout_path") and os.path.exists(row["rollout_path"]):
            return row["rollout_path"]
        # fallback: scan sessions tree for id in filename
        base = os.path.join(CODEX_DIR, "sessions")
        for root, _dirs, files in os.walk(base):
            for fn in files:
                if session_id in fn and fn.endswith(".jsonl"):
                    return os.path.join(root, fn)
        return None

    def events(self, session_id: str, state=None) -> list[Event]:
        path = self.transcript_path(session_id)
        if not path:
            return []
        offset = state.offset_for(path) if (state and hasattr(state, "offset_for")) else 0
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                buf = f.read()
        except OSError:
            return []
        lines = buf.split(b"\n")
        remainder = lines.pop() if lines else b""
        consumed = len(buf) - len(remainder)
        out: list[Event] = []
        for raw in lines:
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            out.extend(_parse_codex(obj))
        if state and hasattr(state, "set_offset"):
            state.set_offset(path, offset + consumed)
        return out

    def resume_args(self, session_id: str) -> list[str]:
        # MEASURED: keep the interface identical across harnesses; the
        # server prefix-selects the harness binary.
        return ["resume", session_id]

    def status_line(self, screen_lines: list[str]) -> dict:
        # MEASURED: Codex prints "tokens used" and a NNNN number, no percentages.
        import re
        text = "\n".join(screen_lines)
        out: dict = {}
        m = re.search(r"tokens used\s*\n?\s*([\d,]+)", text)
        if m:
            out["tokens_used"] = int(m.group(1).replace(",", ""))
        m = re.search(r"model:\s*(\S+)", text)
        if m:
            out["model"] = m.group(1)
        return out

    def parse_prompt(self, screen_lines: list[str]) -> Ask | None:
        """Measured 0.159.0: trust/menu screens use '› N. Label' rows and a
        footer like 'enter continue · esc back'. This is the startup trust
        dialog and similar approval menus."""
        text = "\n".join(screen_lines)
        has_footer = bool(CX_FOOTER_RE.search(text))
        options: list[Option] = []
        first_idx = None
        for i, ln in enumerate(screen_lines):
            m = CX_OPT_RE.match(ln)
            if m:
                if first_idx is None:
                    first_idx = i
                options.append(Option(number=int(m.group(2)),
                                      label=m.group(3).strip(),
                                      selected=bool(m.group(1))))
                continue
            c = CX_CARET_RE.match(ln)
            if c:
                options.append(Option(number=None, label=c.group(2).strip(),
                                      selected=True))
        if not options and not has_footer:
            return None
        kind = "trust" if options and any(
            "trust" in o.label.lower() for o in options) else "question"
        q = _question_text(screen_lines, first_idx or len(screen_lines))
        return Ask(question=q, options=options, multi=False,
                   kind=kind if options else "permission", raw=text)

    def plan_answer(self, ask: Ask, answer) -> list[Step]:
        """Measured: Codex trust dialog accepts Enter on the selected row, or a
        digit to pick then Enter. 'esc back' cancels. No preview-box nuance."""
        if ask.kind in ("trust", "permission") and not ask.options:
            return [Step(keys=["enter"])]
        opt = answer.get("option") if isinstance(answer, dict) else answer
        text = answer.get("text") if isinstance(answer, dict) else None
        if opt is not None:
            steps = [Step(keys=[str(opt)]), Step(keys=["enter"])]
            if text:
                steps += [Step(text=str(text)), Step(keys=["enter"])]
            return steps
        if text is not None:
            return [Step(text=str(text)), Step(keys=["enter"])]
        return [Step(keys=["enter"])]


def _question_text(lines: list[str], first_opt_idx: int) -> str:
    if first_opt_idx <= 0:
        return ""
    picked: list[str] = []
    for ln in reversed(lines[:first_opt_idx]):
        if ln.strip():
            picked.append(ln.strip())
        elif picked:
            break
        if len(picked) >= 4:
            break
    return "\n".join(reversed(picked))


def _parse_codex(obj: dict) -> list[Event]:
    ts = obj.get("timestamp", "")
    t = obj.get("type")
    p = obj.get("payload", {}) or {}
    out: list[Event] = []

    if t == "response_item":
        pt = p.get("type")
        if pt == "message":
            role = p.get("role", "")
            texts = [c.get("text", "") for c in p.get("content", []) or []
                     if isinstance(c, dict) and c.get("type") in ("input_text", "output_text")]
            text = "\n".join(x for x in texts if x)
            if not text:
                return out
            if role == "user":
                # MEASURED: Codex injects <environment_context> as a user
                # message before the real prompt; skip angle-bracket noise.
                if text.lstrip().startswith("<"):
                    return out
                out.append(Event(kind="prompt", ts=ts, text=text))
            elif role == "assistant":
                out.append(Event(kind="reply", ts=ts, text=text))
            # developer/system roles skipped (harness noise)
        elif pt in ("function_call", "local_shell_call", "custom_tool_call"):
            out.extend(_codex_tool(p, ts))
        return out

    if t == "event_msg":
        pt = p.get("type")
        if pt == "token_count":
            info = p.get("info", {}) or {}
            total = info.get("total_token_usage", {}) or {}
            if total:
                out.append(Event(kind="tokens", ts=ts,
                                 text=str(total.get("total_tokens", "")),
                                 tool="token_count"))
        elif pt == "task_complete":
            # MEASURED: last_agent_message duplicates the final reply; use only
            # when no assistant message was captured this turn.
            pass
        elif pt == "agent_message":
            msg = p.get("message", "")
            if msg:
                out.append(Event(kind="reply", ts=ts, text=msg))
        return out

    return out


def _codex_tool(p: dict, ts: str) -> list[Event]:
    name = p.get("name", "")
    args = p.get("arguments") or p.get("action") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    out: list[Event] = []
    # MEASURED: local_shell_call action.type == "exec", action.command is a list.
    if p.get("type") == "local_shell_call":
        action = p.get("action", {}) or {}
        cmd = action.get("command")
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        out.append(Event(kind="bash", ts=ts, command=str(cmd or ""),
                         tool="shell"))
        return out
    lowered = name.lower()
    if lowered in ("shell", "bash", "exec", "local_shell"):
        cmd = args.get("command") or args.get("cmd") or ""
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        out.append(Event(kind="bash", ts=ts, command=str(cmd), tool="shell"))
    elif lowered in ("apply_patch", "edit", "write"):
        path = args.get("file_path") or args.get("path") or ""
        out.append(Event(kind="edit", ts=ts, path=str(path), tool=name))
    return out
