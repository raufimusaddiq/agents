"""Rich chat from a harness transcript.

Ported from the reference workflow-viz implementation: it renders what the
harness itself draws — prompts, slash and `!` shell commands, replies,
questions the agent asked you, tool calls with their input and result, subagent
runs, and the live task list. Claude Code is measured; other harnesses fall back
to the screen tail.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SESSION_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Bytes of transcript read per request; plenty for the last ~80 turns.
CHAT_TAIL = 1_500_000

TASK_TOOLS = {"TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TodoWrite"}


def transcript(session_id: str) -> Path | None:
    """The newest Claude Code transcript for a session id."""
    if not SESSION_ID.match(session_id or ""):
        return None
    hits = list((Path.home() / ".claude" / "projects").glob(
        f"*/{session_id}.jsonl"))
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def tool_detail(inp: dict) -> str:
    inp = inp or {}
    v = (inp.get("description") or inp.get("file_path") or inp.get("pattern")
         or inp.get("command") or inp.get("url") or "")
    return str(v).splitlines()[0][:90] if v else ""


def _cap(t: str, n: int) -> str:
    return t if len(t) <= n else t[:n] + f"\n… {len(t) - n} more characters"


def _lines(t: str, n: int) -> str:
    ls = t.splitlines()
    return "\n".join(ls[:n]) + (f"\n… {len(ls) - n} more lines"
                                if len(ls) > n else "")


def tool_view(name: str, inp: dict) -> dict:
    """What the harness draws for a tool call: its name, a one-line argument,
    and the input worth showing (an edit's before/after)."""
    inp, v = inp or {}, {}
    if name in ("Edit", "MultiEdit"):
        edits = inp.get("edits") or [{
            "old_string": inp.get("old_string", ""),
            "new_string": inp.get("new_string", "")}]
        v["edits"] = [{"old": _lines(str(e.get("old_string", "")), 80),
                       "new": _lines(str(e.get("new_string", "")), 80)}
                      for e in edits[:6]]
    elif name == "Write":
        c = str(inp.get("content", ""))
        v["preview"], v["lines"] = _lines(c, 40), len(c.splitlines())
    elif name == "Bash":
        v["command"] = _cap(str(inp.get("command", "")), 800)
    elif name in ("Agent", "Task"):
        v.update(agent=inp.get("subagent_type") or "agent",
                 task=str(inp.get("description", ""))[:120],
                 prompt=_cap(str(inp.get("prompt", "")), 600),
                 background=bool(inp.get("run_in_background")), status="running")
    arg = (inp.get("file_path") or inp.get("notebook_path") or inp.get("pattern")
           or inp.get("url") or inp.get("query") or inp.get("skill") or "")
    if name == "Bash":
        src = inp.get("description") or inp.get("command")
        arg = str(src).splitlines()[0] if src else ""
    if name in ("Agent", "Task"):
        arg = v["task"]
    return {"name": name, "detail": str(arg)[:160], **v}


def result_text(c) -> str:
    return c if isinstance(c, str) else "\n".join(
        x.get("text", "") for x in c or []
        if isinstance(x, dict) and x.get("type") == "text")


def chat_items(lines, limit: int = 80):
    """Transcript JSONL lines -> chat items plus the live task list."""
    items, asked, tools, creates, todos = [], {}, {}, {}, {}
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("isSidechain") or d.get("isMeta"):
            continue
        t, c, ts = d.get("type"), (d.get("message") or {}).get("content"), d.get("timestamp")
        if t == "queue-operation" and d.get("operation") == "enqueue":
            # A prompt sent while the agent was busy is logged only as a queue
            # entry, never as a user line.
            q = str(d.get("content") or "")
            if q.strip() and not q.lstrip().startswith("<"):
                items.append({"role": "user", "text": q, "ts": ts, "queued": True})
            continue
        if t == "user" and isinstance(c, list):
            for x in c:
                if not isinstance(x, dict) or x.get("type") != "tool_result":
                    continue
                tid, body = x.get("tool_use_id"), result_text(x.get("content"))
                q = asked.get(tid)
                if q is not None:
                    q["answers"] = dict(re.findall(r'"([^"]+)"="([^"]*)"', body))
                    q["cancelled"] = not q["answers"]
                if tid in creates and (m := re.search(r"Task #(\S+?) created", body)):
                    todos[m.group(1)] = {"id": m.group(1), **creates.pop(tid),
                                         "status": "pending"}
                tool = tools.get(tid)
                if tool is not None:
                    tool["error"] = bool(x.get("is_error"))
                    if tool["name"] in ("Agent", "Task"):
                        if "Async agent launched" in body:
                            # still running; its end arrives as a task-notification
                            continue
                        tool["status"] = "failed" if tool["error"] else "done"
                    tool["result"] = _cap(body, 3000)
                    tool["result_lines"] = len(body.splitlines())
        if t == "user":
            text = c if isinstance(c, str) else "\n".join(
                x.get("text", "") for x in c or [] if x.get("type") == "text")
            if not text.strip():
                continue  # tool results
            for tid, status in re.findall(
                    r"<tool-use-id>(\S+?)</tool-use-id>.*?<status>(\w+)</status>",
                    text, re.S):
                if tid in tools:  # a background subagent finished
                    res = (re.search(r"<result>(.*?)</result>", text, re.S)
                           or re.search(r"<summary>(.*?)</summary>", text, re.S))
                    tools[tid].update(
                        status="done" if status == "completed" else status,
                        result=_cap((res.group(1) if res else "").strip(), 3000))
            cmd = re.search(r"<command-name>(.*?)</command-name>", text)
            sh = re.match(r"\s*<bash-input>(.*?)</bash-input>", text, re.S)
            out = re.match(
                r"\s*<bash-stdout>(.*?)</bash-stdout>\s*(?:<bash-stderr>(.*?)</bash-stderr>)?",
                text, re.S)
            if sh:
                items.append({"role": "shell", "text": sh.group(1), "ts": ts,
                              "output": None})
            elif out and items and items[-1]["role"] == "shell" and items[-1]["output"] is None:
                items[-1]["output"] = ((out.group(1) or "") + (out.group(2) or ""))[-4000:]
            elif cmd:
                args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
                items.append({"role": "command", "ts": ts,
                              "text": f"{cmd.group(1)} {args.group(1).strip() if args else ''}".strip()})
            elif not text.lstrip().startswith("<"):
                if any(i.get("queued") and i["text"].strip() == text.strip()
                       for i in items[-6:]):
                    continue  # the queued copy is already shown
                items.append({"role": "user", "text": text, "ts": ts})
        elif t == "assistant" and isinstance(c, list):
            for x in c:
                if x.get("type") == "text" and x.get("text", "").strip():
                    items.append({"role": "assistant", "text": x["text"], "ts": ts})
                elif x.get("type") == "tool_use" and x.get("name") == "AskUserQuestion":
                    q = {"role": "question", "id": x.get("id"), "ts": ts,
                         "answers": None,
                         "questions": [{
                             "question": qq.get("question"),
                             "header": qq.get("header"),
                             "multiSelect": bool(qq.get("multiSelect")),
                             "options": [{"label": o.get("label"),
                                          "description": o.get("description", "")}
                                         for o in qq.get("options") or []]}
                             for qq in (x.get("input") or {}).get("questions") or []]}
                    asked[x.get("id")] = q
                    items.append(q)
                elif x.get("type") == "tool_use" and x.get("name") in TASK_TOOLS:
                    inp = x.get("input") or {}
                    if x["name"] == "TaskCreate":
                        creates[x.get("id")] = {
                            "subject": str(inp.get("subject", ""))[:200],
                            "active": str(inp.get("activeForm", ""))[:200]}
                    elif x["name"] == "TaskUpdate" and str(inp.get("taskId")) in todos:
                        td = todos[str(inp["taskId"])]
                        if inp.get("status") == "deleted":
                            todos.pop(str(inp["taskId"]))
                        else:
                            td.update({k: str(inp[f])[:200]
                                       for k, f in (("status", "status"),
                                                    ("subject", "subject"),
                                                    ("active", "activeForm"))
                                       if inp.get(f)})
                    elif x["name"] == "TodoWrite":
                        todos = {str(i): {
                            "id": str(i),
                            "subject": str(td.get("content", ""))[:200],
                            "active": str(td.get("activeForm", ""))[:200],
                            "status": td.get("status", "pending")}
                            for i, td in enumerate(inp.get("todos") or [], 1)}
                elif x.get("type") == "tool_use":
                    tool = {"id": x.get("id"), **tool_view(x.get("name"), x.get("input")),
                            "result": None, "error": False, "ts": ts}
                    tools[x.get("id")] = tool
                    if items and items[-1]["role"] == "tools":
                        items[-1]["tools"].append(tool)
                    else:
                        items.append({"role": "tools", "tools": [tool], "ts": ts})
    return items[-limit:], list(todos.values())


def chat_for(session_id: str, screen_tail: list[str]) -> dict:
    """Chat for an agent: the transcript when readable, else the screen tail."""
    path = transcript(session_id)
    if not path:
        return {"source": "screen",
                "items": [{"role": "screen", "text": "\n".join(screen_tail)}],
                "todos": []}
    size = path.stat().st_size
    with open(path, "rb") as f:
        f.seek(max(0, size - CHAT_TAIL))
        raw = f.read().split(b"\n")
    items, todos = chat_items(raw[1:] if size > CHAT_TAIL else raw)
    return {"source": "transcript", "items": items, "todos": todos}
