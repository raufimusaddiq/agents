"""Workflow rules: git command classification, the nine stations, and ticket
inference.

Pure functions with no server state, so they are easy to test and reuse. Ticket
inference order is configurable via config.json -> ticket.sources.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .config import config
from .gitrepo import git
from .herdr import TICKET_RE

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


STAGE_ORDER = ["To do", "In progress", "Testing", "Review", "Ready to push",
               "Shipped"]
# Which station implies which board stage, furthest last.
_STATION_TO_STAGE = {
    "Start check": "To do",
    "Branch": "To do",
    "Code": "In progress",
    "Tests": "Testing",
    "Review": "Review",
    "Commit": "Ready to push",
    "Docs": "Ready to push",
    "Push": "Shipped",
    "PR": "Shipped",
}


def agent_stage(rec: dict, events: list[dict], repo: str | None) -> str:
    """The furthest work stage this one agent has reached."""
    stations = compute_stations(rec, events, repo)
    best = 0
    for name, state in stations["stations"]:
        if state != "done":
            continue
        idx = STAGE_ORDER.index(_STATION_TO_STAGE.get(name, "To do"))
        best = max(best, idx)
    return STAGE_ORDER[best]


def ticket_stage(agents: list[dict]) -> str:
    """A ticket's stage is the furthest stage reached by any of its agents."""
    best = 0
    for a in agents:
        st = a.get("stage", "To do")
        if st in STAGE_ORDER:
            best = max(best, STAGE_ORDER.index(st))
    return STAGE_ORDER[best]


def _first_prompt(events: list[dict], limit: int = 48) -> str:
    """First real user prompt, skipping slash commands and harness noise."""
    for e in events:
        if e.get("kind") != "prompt":
            continue
        text = (e.get("text") or "").strip()
        if not text or text.startswith("/"):
            continue
        first = text.splitlines()[0].strip()
        if first:
            return first[:limit]
    return ""


def _branch_ticket(b: str) -> str:
    b = (b or "").replace("refs/heads/", "")
    m = TICKET_RE.search(b)
    return m.group(0) if m else b


def derive_ticket(rec: dict, events: list[dict], repo: str | None,
                  branch: str, worktree_branch: str = "") -> dict:
    """Infer the work item an agent is on. First match wins.

    Order is configurable via config.json -> ticket.sources. Every result
    carries `source` so a wrong grouping is visible in the UI rather than
    silent. Never guesses: each source is a concrete observed value.
    """
    cfg = config().get("ticket", {})
    sources = cfg.get("sources", ["worktree", "branch_ticket", "branch",
                                  "tab", "prompt", "repo"])
    prompt_len = int(cfg.get("first_prompt_len", 48))

    for src in sources:
        if src == "worktree" and worktree_branch:
            return {"id": _branch_ticket(worktree_branch),
                    "name": _branch_ticket(worktree_branch),
                    "source": "worktree branch"}
        if src == "branch_ticket" and branch:
            m = TICKET_RE.search(branch)
            if m:
                return {"id": m.group(0), "name": m.group(0),
                        "source": "branch ticket"}
        if src == "branch" and branch and branch not in ("main", "master", "?"):
            return {"id": _branch_ticket(branch), "name": _branch_ticket(branch),
                    "source": "branch"}
        if src == "tab":
            label = (rec.get("tab_label") or "").strip()
            if label and not label.isdigit():
                return {"id": label, "name": label, "source": "tab label"}
        if src == "prompt":
            p = _first_prompt(events, prompt_len)
            if p:
                return {"id": p, "name": p, "source": "first prompt"}
        if src == "repo" and repo:
            b = branch or "?"
            return {"id": f"{os.path.basename(repo)} · {b}",
                    "name": f"{os.path.basename(repo)} · {b}",
                    "source": "repo"}
    return {"id": "Unnamed work", "name": "Unnamed work", "source": ""}

