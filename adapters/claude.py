"""Claude Code adapter. Measured on Claude Code 2.1.284.

Transcripts: ~/.claude/projects/<dir>/<session_id>.jsonl, one JSON object per line.
"""
from __future__ import annotations

import json
import os
import re

from .base import Adapter, Ask, Event, MenuItem, Option, Step

# Measured: transcript lives here, session id is the jsonl file stem.
PROJECTS = os.path.expanduser("~/.claude/projects")

# Measured: <command-name>/x</command-name><command-args>...</command-args>
SLASH_RE = re.compile(r"<command-name>(.+?)</command-name>"
                      r"(?:<command-args>(.*?)</command-args>)?", re.S)
# Measured: background subagent end notification.
TASK_NOTIFY_RE = re.compile(r"<task-notification>.*?<tool-use-id>(.*?)</tool-use-id>"
                            r".*?<status>(.*?)</status>", re.S)
# Measured: answered AskUserQuestion tool_result text.
ANSWERED_RE = re.compile(r'User has answered your questions:\s*(.*)', re.S)
ANS_PAIR_RE = re.compile(r'"([^"]+)"="([^"]*)"')

# Measured: option rows "❯ 1. Label" / "  2. Label".
OPT_RE = re.compile(r"^\s*(❯)?\s*(\d+)\.\s+(.*)$")
# Measured: an unnumbered row (Submit / Other) can carry the caret.
CARET_ROW_RE = re.compile(r"^\s*(❯)\s+(\S.*)$")
# Measured: multi-select rows "[ ] Label" or "[✔] Label".
CHECK_RE = re.compile(r"^\s*\[( |✔|x|X)\]\s+(.*)$")
# Measured: preview box to the right of options; cut before parsing options.
PREVIEW_CUT_RE = re.compile(r"\s{3,}[┌│└].*$")
# Measured: footer markers.
FOOTER_SELECT = "Enter to select"
FOOTER_CONFIRM = "Enter to confirm"
FOOTER_CANCEL = "Esc to cancel"
# Measured: tab header line "☐ Topic  ☑ Topic2  ✔ Submit".
TAB_RE = re.compile(r"[☐☑✔]\s*\S+")
# Measured: trust dialog / "Chat about this" have no digits.
NO_DIGIT_MARKERS = ("Do you trust the files in this folder",
                    "Chat about this")


class ClaudeAdapter(Adapter):
    kind = "claude"
    supports = {
        "transcript": True,
        "prompts": True,
        "status_line": True,
        "subagents": True,
        "resume": True,
        "questions": True,
    }
    notes = [
        "measured 2.1.28x: skip isSidechain and isMeta lines",
        "measured: queued prompt appears only as type=queue-operation operation=enqueue",
        "measured: background subagent ends via <task-notification> user text",
        "measured: resume with --resume <session_id>",
    ]

    def transcript_path(self, session_id: str) -> str | None:
        # session_id is the jsonl stem; search project dirs for it.
        for d in _project_dirs():
            p = os.path.join(d, session_id + ".jsonl")
            if os.path.exists(p):
                return p
        return None

    def events(self, session_id: str, state=None) -> list[Event]:
        path = self.transcript_path(session_id) if not session_id.endswith(".jsonl") else session_id
        if not path:
            return []
        offset = 0
        if state is not None and hasattr(state, "offset_for"):
            offset = state.offset_for(path)
        out: list[Event] = []
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                buf = f.read()
        except OSError:
            return []
        # Only parse complete lines; keep remainder for next read.
        lines = buf.split(b"\n")
        remainder = lines.pop() if lines else b""
        consumed = len(buf) - len(remainder)
        for raw in lines:
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            out.extend(_parse_line(obj))
        if state is not None and hasattr(state, "set_offset"):
            state.set_offset(path, offset + consumed)
        return out

    def resume_args(self, session_id: str) -> list[str]:
        # Measured: --resume <session_id>
        return ["--resume", session_id]

    # ---- screen parsing -------------------------------------------------

    def parse_prompt(self, screen_lines: list[str]) -> Ask | None:
        # Rich parser first (ported from the reference implementation): it
        # handles dividers, preview boxes, wrapped labels, Submit rows and tab
        # headers better than the simpler line scan below, which remains as the
        # fallback for menus it declines to claim.
        rich = _try_rich_ask(screen_lines)
        if rich is not None:
            return rich
        return self._parse_prompt_simple(screen_lines)

    def _parse_prompt_simple(self, screen_lines: list[str]) -> Ask | None:
        lines = [PREVIEW_CUT_RE.sub("", ln) for ln in screen_lines]
        text = "\n".join(lines)
        has_footer = (FOOTER_SELECT in text or FOOTER_CONFIRM in text
                      or FOOTER_CANCEL in text)
        has_trust = any(m in text for m in NO_DIGIT_MARKERS)
        # Measured: Claude renders some menus (e.g. login method) with numbered
        # options and a caret but no footer. Treat a caret + numbered option as
        # a prompt too, so startup questions still surface as needs-you.
        has_numbered_caret = any(
            OPT_RE.match(ln) and OPT_RE.match(ln).group(1) for ln in lines)
        if not (has_footer or has_trust or has_numbered_caret):
            return None

        options: list[Option] = []
        multi = False
        # Track which line index each option starts at, so the question text is
        # taken from the lines immediately above the first option (measured:
        # Claude draws banners/logs above the menu; those are not the question).
        first_opt_idx = None
        for idx, ln in enumerate(lines):
            m = OPT_RE.match(ln)
            if m:
                if first_opt_idx is None:
                    first_opt_idx = idx
                caret, num, label = m.group(1), int(m.group(2)), m.group(3).strip()
                options.append(Option(number=num, label=label,
                                      selected=bool(caret)))
                continue
            c = CHECK_RE.match(ln)
            if c:
                multi = True
                options.append(Option(number=None, label=c.group(2).strip(),
                                      checked=c.group(1) in "✔xX"))
                continue
            cr = CARET_ROW_RE.match(ln)
            if cr:
                # Measured: unnumbered caret row (Submit / "Chat about this").
                options.append(Option(number=None, label=cr.group(2).strip(),
                                      selected=True))
                continue
            s = ln.strip()
            if s and options and not _is_footer(s):
                # description on the indented line below a numbered option
                last = options[-1]
                if not PREVIEW_CUT_RE.search(ln) and last.number is not None:
                    last.description = (last.description + " " + s).strip()

        if not options:
            # trust dialog / chat-about-this: arrows + Enter, no digits
            kind = "trust" if any(m in text for m in NO_DIGIT_MARKERS) else "permission"
            ctx = _question_text(lines, len(lines))
            return Ask(question=ctx, options=[], multi=False, kind=kind, raw=text)
        submit = ""
        for o in options:
            if o.number is None and o.label.lower().startswith("submit"):
                submit = o.label
        tabs = TAB_RE.findall(text)
        return Ask(question=_question_text(lines, first_opt_idx or 0),
                   options=options, multi=multi, submit_row=submit,
                   tabs=tabs, kind="question", raw=text)

    def plan_answer(self, ask: Ask, answer) -> list[Step]:
        """Measured keys:
        - single select: digit submits, EXCEPT beside a preview box (digit moves
          caret only), then press Enter.
        - multi select: digit toggles without moving caret; submit by moving
          caret to unnumbered Submit row + Enter, then choose '1. Submit answers'.
        - several questions: a single-select digit advances to the next tab.
        - trust dialog / chat-about-this: arrows + Enter.
        - plan approval option 3 takes typed feedback: digit, text, Enter.
        """
        has_preview = any(o.preview for o in ask.options)
        if ask.kind == "trust":
            return [Step(keys=["right"]), Step(keys=["enter"])]

        # answer may be: int/digit, str text, or dict {option, text, multi:[...]}
        if isinstance(answer, dict):
            opt = answer.get("option")
            text = answer.get("text")
            multi_sel = answer.get("multi") or []
        else:
            opt, text, multi_sel = answer, None, []

        steps: list[Step] = []
        if multi_sel:
            # toggle each selected option by digit (no caret move)
            for o in ask.options:
                if o.number and o.label in multi_sel:
                    steps.append(Step(keys=[str(o.number)]))
            # move caret to unnumbered Submit row with arrows, then Enter
            steps.append(Step(keys=["down"], note="to submit row"))
            steps.append(Step(keys=["enter"]))
            # review screen: choose 1. Submit answers
            steps.append(Step(keys=["1"], wait=0.4, note="submit answers"))
            return steps

        if opt is not None:
            if has_preview:
                steps.append(Step(keys=[str(opt)]))
                steps.append(Step(keys=["enter"]))
            else:
                steps.append(Step(keys=[str(opt)]))
            if text is not None:
                steps.append(Step(text=str(text)))
                steps.append(Step(keys=["enter"]))
            return steps
        if text is not None:
            # typed "Other": caret must already be on its row (caller moves it)
            steps.append(Step(text=str(text)))
            steps.append(Step(keys=["enter"]))
            return steps
        return []

    def parse_menu(self, screen_lines: list[str]) -> list[MenuItem]:
        """Measured: Claude's slash/`@` composer menu uses '❯ /cmd' (or '❯ @file')
        as the selected row and '  /cmd  description' for the rest, with a footer
        like 'Esc to cancel'. Descriptions are separated by two-plus spaces."""
        items: list[MenuItem] = []
        for ln in screen_lines:
            s = PREVIEW_CUT_RE.sub("", ln)
            m = re.match(r"^\s*(❯)?\s*([/@][^\s]+)\s{2,}(.+)$", s)
            if m:
                items.append(MenuItem(
                    trigger=m.group(2)[0], label=m.group(2).strip(),
                    detail=m.group(3).strip(), selected=bool(m.group(1))))
                continue
            m2 = re.match(r"^\s*(❯)?\s*([/@][^\s]+)\s*$", s)
            if m2:
                items.append(MenuItem(
                    trigger=m2.group(2)[0], label=m2.group(2).strip(),
                    selected=bool(m2.group(1))))
        return items

    def status_line(self, screen_lines: list[str]) -> dict:
        """Measured: bottom line has model, ctx NN%, $NN.NN, 5h NN% ↻, 7d NN% ↻."""
        text = "\n".join(screen_lines)
        out: dict = {}
        m = re.search(r"\bctx\s*\.*\s*(\d{1,3})%", text)
        if m:
            out["context_pct"] = int(m.group(1))
        m = re.search(r"\$(\d+(?:\.\d+)?)", text)
        if m:
            out["cost"] = float(m.group(1))
        m = re.search(r"5h\s+(\d{1,3})%\s*↻(\d+h\d+m|\d+m)", text)
        if m:
            out["usage_5h_pct"] = int(m.group(1))
            out["usage_5h_reset"] = m.group(2)
        m = re.search(r"7d\s+(\d{1,3})%\s*↻(\d+h\d+m|\d+m)", text)
        if m:
            out["usage_7d_pct"] = int(m.group(1))
            out["usage_7d_reset"] = m.group(2)
        m = re.search(r"(auto[\w -]*mode|plan mode|accept edits)", text, re.I)
        if m:
            out["mode"] = m.group(1)
        for ln in reversed(screen_lines):
            s = ln.strip()
            if s and not re.search(r"ctx|5h|7d|\$", s):
                out.setdefault("model", s[:60])
                break
        return out


def _try_rich_ask(screen_lines: list[str]) -> Ask | None:
    """Adapt the ported parse_ask result to our Ask, or None if it declined."""
    from .ask import parse_ask
    d = parse_ask(screen_lines)
    if not d or not d.get("options"):
        return None
    opts: list[Option] = []
    for o in d["options"]:
        # Our Ask.number is the digit the user presses (1-based), not an index.
        n = int(o["n"]) if o.get("n") else None
        opts.append(Option(
            number=n, label=o.get("label", ""),
            description=o.get("description", ""),
            selected=bool(o.get("selected")),
            checked=bool(o.get("checked")),
            kind=o.get("kind", "choice"),
            preview=o.get("preview", "") or "",
        ))
    # The rich parser's Submit row is separate from the numbered options.
    submit = d.get("submit") or None
    return Ask(
        question=d.get("question") or "",
        options=opts, multi=bool(d.get("multi")),
        submit_row="Submit" if submit else "",
        tabs=[t.get("label", "") for t in d.get("tabs", [])],
        context=d.get("context") or "",
        kind="question", raw="\n".join(screen_lines),
    )


def _is_footer(s: str) -> bool:
    return any(k in s for k in (FOOTER_SELECT, FOOTER_CONFIRM, FOOTER_CANCEL))


def _question_text(lines: list[str], first_opt_idx: int) -> str:
    """Question text = up to 4 non-empty lines immediately above the options."""
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


def _project_dirs():
    base = PROJECTS
    if not os.path.isdir(base):
        return []
    out = []
    for name in os.listdir(base):
        p = os.path.join(base, name)
        if os.path.isdir(p):
            out.append(p)
    return out


def _parse_line(obj: dict) -> list[Event]:
    """Measured rules for one transcript JSON object."""
    out: list[Event] = []
    # Measured: skip sidechain (subagent-internal) and meta lines.
    if obj.get("isSidechain") or obj.get("isMeta"):
        return out
    t = obj.get("type")
    ts = obj.get("timestamp", "")

    if t == "queue-operation" and obj.get("operation") == "enqueue":
        # Measured: a prompt typed while busy appears only here.
        out.append(Event(kind="prompt", ts=ts, text=str(obj.get("content", ""))))
        return out

    if t == "user":
        msg = obj.get("message", {})
        content = msg.get("content")
        if isinstance(content, str):
            ev = _user_text(content, ts)
            if ev:
                out.append(ev)
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    ev = _user_text(item.get("text", ""), ts)
                    if ev:
                        out.append(ev)
                elif item.get("type") == "tool_result":
                    out.extend(_tool_result(item, ts, obj))
        return out

    if t == "assistant":
        msg = obj.get("message", {})
        model = msg.get("model", "")
        for item in msg.get("content", []) or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and item.get("text"):
                out.append(Event(kind="reply", ts=ts, text=item["text"], model=model))
            elif item.get("type") == "tool_use":
                out.extend(_tool_use(item, ts))
        return out
    return out


def _user_text(text: str, ts: str) -> Event | None:
    # Measured: text starting with "<" is harness noise, EXCEPT slash commands.
    if text.startswith("<"):
        m = SLASH_RE.search(text)
        if m:
            cmd = m.group(1)
            args = (m.group(2) or "").strip()
            return Event(kind="prompt", ts=ts, text=(cmd + " " + args).strip())
        n = TASK_NOTIFY_RE.search(text)
        if n:
            return Event(kind="subagent_end", ts=ts,
                         tool_use_id=n.group(1), status=n.group(2))
        return None
    return Event(kind="prompt", ts=ts, text=text)


def _tool_use(item: dict, ts: str) -> list[Event]:
    name = item.get("name", "")
    inp = item.get("input", {}) or {}
    uid = item.get("id", "")
    if name in ("Edit", "Write", "MultiEdit"):
        # Measured: file_path
        return [Event(kind="edit", ts=ts, path=inp.get("file_path", ""),
                      tool=name, tool_use_id=uid)]
    if name == "NotebookEdit":
        # Measured: notebook_path
        return [Event(kind="edit", ts=ts, path=inp.get("notebook_path", ""),
                      tool=name, tool_use_id=uid)]
    if name == "Bash":
        # Measured: command
        return [Event(kind="bash", ts=ts, command=inp.get("command", ""),
                      tool=name, tool_use_id=uid)]
    if name in ("Agent", "Task"):
        # Measured: subagent_type, run_in_background, isolation
        bg = bool(inp.get("run_in_background"))
        return [Event(kind="subagent_start", ts=ts,
                      subagent_type=inp.get("subagent_type", ""),
                      background=bg, isolation=inp.get("isolation", ""),
                      tool_use_id=uid)]
    if name == "AskUserQuestion":
        # Measured: input.questions[] with question/header/multiSelect/options[]
        qs = inp.get("questions", [])
        return [Event(kind="question", ts=ts, tool_use_id=uid,
                      ask={"questions": qs})]
    return []


def _tool_result(item: dict, ts: str, obj: dict) -> list[Event]:
    # Measured: answered AskUserQuestion returns here as tool_result text.
    content = item.get("content")
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                texts.append(c.get("text", ""))
    joined = "\n".join(texts)
    m = ANSWERED_RE.search(joined)
    if m:
        pairs = ANS_PAIR_RE.findall(m.group(1))
        answer = "; ".join(f"{q}={a}" for q, a in pairs)
        return [Event(kind="answer", ts=ts, answer=answer,
                      tool_use_id=item.get("tool_use_id", ""))]
    return []
