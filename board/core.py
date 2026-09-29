"""Core state, polling, workflow rules, tickets and hire/fire.

This holds the live model the board is built from. Python 3 standard library
only. Measured facts about herdr live beside the code that relies on them.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from adapters import ADAPTERS, get as get_adapter
from adapters.base import Event as AEvent

from .config import config
from .gitrepo import git, repo_root, git_status, git_diff_file, git_diff_commit
from .herdr import (
    HerdrError, herdr, snapshot, read_screen, valid_pane,
    NAME_RE, SHA_RE, TICKET_RE,
)
from .notify import note_page_open, notify

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
ROSTER_PATH = ROOT / "roster.json"

class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.offsets: dict[str, int] = {}
        self.agents: dict[str, dict] = {}       # pane -> agent record
        self.events: dict[str, list[dict]] = {}  # pane -> normalized events
        self.closed: list[dict] = []             # rehirable
        self.alerts: list[dict] = []
        self.dismissed: set[str] = set()
        self.handled: set[str] = set()
        self.notified: dict[str, float] = {}
        self.frozen_roster: list[dict] | None = None
        self.usage_history: dict[str, list[tuple[float, int]]] = {}
        self.last_push = 0.0
        self._subscribers: list = []

    def offset_for(self, key: str) -> int:
        return self.offsets.get(key, 0)

    def set_offset(self, key: str, val: int) -> None:
        self.offsets[key] = val

    def subscribe(self):
        q = _Queue()
        with self.lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q) -> None:
        with self.lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def changed(self) -> None:
        with self.lock:
            subs = list(self._subscribers)
        for q in subs:
            q.put("changed")


class _Queue:
    def __init__(self):
        self.items = []
        self.cv = threading.Condition()

    def put(self, item):
        with self.cv:
            self.items.append(item)
            self.cv.notify_all()

    def get(self, timeout=25):
        with self.cv:
            if not self.items:
                self.cv.wait(timeout)
            return self.items.pop(0) if self.items else None


STATE = State()


def _norm_events(pane: str, events: list[AEvent]) -> list[dict]:
    out = []
    for e in events:
        j = e.to_json()
        j["pane"] = pane
        out.append(j)
    return out


# --------------------------------------------------------------------------
# polling: discover agents and read transcripts incrementally
# --------------------------------------------------------------------------

def reconcile_agents() -> None:
    """Refresh agent records from a herdr snapshot.

    Measured 0.9.2: agent identity lives in snapshot.agents[] (name, agent kind,
    agent_session.value, agent_status, cwd, terminal_title_stripped). The
    panes[] array carries raw panes; index it by pane_id for geometry/focus.
    """
    try:
        snap = snapshot()
    except HerdrError:
        return
    panes = {p.get("pane_id"): p for p in snap.get("panes", [])}
    tabs = {t.get("tab_id"): t for t in snap.get("tabs", [])}
    workspaces = {w.get("workspace_id"): w for w in snap.get("workspaces", [])}
    seen = set()
    now = time.time()
    with STATE.lock:
        for a in snap.get("agents", []):
            pane = a.get("pane_id", "")
            if not valid_pane(pane):
                continue
            seen.add(pane)
            p = panes.get(pane, {})
            sess = a.get("agent_session") or {}
            rec = STATE.agents.get(pane, {})
            rec.update({
                "pane": pane,
                "name": a.get("name") or rec.get("name", "") or rec.get("hired_name", ""),
                "kind": a.get("agent") or rec.get("kind", ""),
                "cwd": a.get("cwd") or p.get("cwd") or rec.get("cwd", ""),
                "foreground_cwd": a.get("foreground_cwd", ""),
                "tab_id": a.get("tab_id", ""),
                "workspace_id": a.get("workspace_id", ""),
                "agent_status": a.get("agent_status", "unknown"),
                "title": a.get("terminal_title_stripped", ""),
                "focused": bool(a.get("focused")),
                "interactive_ready": bool(a.get("interactive_ready")),
                "last_seen": now,
                "tab_label": tabs.get(a.get("tab_id"), {}).get("label", ""),
                "ws_label": workspaces.get(a.get("workspace_id"), {}).get("label", ""),
            })
            sid = sess.get("value", "")
            if sid:
                rec["session_id"] = sid
            elif rec.get("kind") == "codex" and not rec.get("session_id"):
                # Measured: herdr reports agent_session None for codex; discover
                # the newest thread for this cwd from codex's own SQLite index.
                # Several codex agents can share a cwd, so never hand the same
                # thread id to two panes.
                ad = get_adapter("codex")
                claimed = {o.get("session_id") for p2, o in STATE.agents.items()
                           if p2 != pane and o.get("kind") == "codex"}
                found = (ad.discover_session(rec.get("cwd", ""), claimed)
                         if ad else "")
                if found:
                    rec["session_id"] = found
            elif not rec.get("session_id"):
                rec["session_id"] = _session_id_for(pane)
            rec.setdefault("col_since", now)
            STATE.agents[pane] = rec
        # agents that vanished -> closed (rehirable)
        for pane in list(STATE.agents):
            if pane not in seen:
                rec = STATE.agents.pop(pane)
                rec["closed_at"] = now
                STATE.closed.append(rec)
        _prune_closed()


def _detect_kind(p: dict) -> str:
    text = (p.get("terminal_title_stripped") or "") + " " + (p.get("name") or "")
    for kind in ADAPTERS:
        if kind in text.lower():
            return kind
    return ""


def _session_id_for(pane: str) -> str:
    """Ask herdr for the harness session id of the agent in this pane.

    Measured 0.9.2: `herdr agent get <pane>` may expose agent_session.value; the
    exact shape is version-dependent, so we probe several keys.
    """
    try:
        j = herdr("agent", "get", pane, timeout=10)
    except HerdrError:
        return ""
    r = j.get("result", j)
    if not isinstance(r, dict):
        return ""
    for key in ("agent_session", "session"):
        v = r.get(key)
        if isinstance(v, dict) and v.get("value"):
            return str(v["value"])
        if isinstance(v, str) and v:
            return v
    return ""


def poll_transcripts() -> None:
    """Read each agent's transcript incrementally and store normalized events."""
    with STATE.lock:
        agents = list(STATE.agents.values())
    for rec in agents:
        pane = rec["pane"]
        sid = rec.get("session_id", "")
        kind = rec.get("kind", "")
        adapter = get_adapter(kind)
        if not (adapter and sid):
            continue
        try:
            evs = adapter.events(sid, STATE)
        except Exception:
            evs = []
        if evs:
            with STATE.lock:
                STATE.events.setdefault(pane, [])
                STATE.events[pane].extend(_norm_events(pane, evs))
                STATE.events[pane] = STATE.events[pane][-2000:]


def read_screens() -> None:
    """Read screen tails for agents that need questions or status lines."""
    with STATE.lock:
        agents = list(STATE.agents.values())
    for rec in agents:
        pane = rec["pane"]
        kind = rec.get("kind", "")
        adapter = get_adapter(kind)
        try:
            screen = read_screen(pane, 60)
        except HerdrError:
            continue
        with STATE.lock:
            r = STATE.agents.get(pane)
            if not r:
                continue
            r["screen_tail"] = screen[-60:]
            if adapter and adapter.supports.get("status_line"):
                st = adapter.status_line(screen)
                r["status"] = st
                _track_usage(pane, st)
            if adapter and adapter.supports.get("prompts"):
                ask = adapter.parse_prompt(screen)
                r["ask"] = ask.to_json() if ask else None
                if ask and getattr(ask, "kind", "") in ("question", "trust"):
                    r["needs_user"] = True
                # Fallback: a screen that clearly waits for the user (login URL,
                # device code, a raw question) but does not parse into options
                # still needs the user. Never guess an answer; offer raw keys.
                elif _screen_needs_user(screen, r.get("agent_status")):
                    r["needs_user"] = True
                    r["ask"] = {
                        "question": "\n".join(screen[-6:]).strip(),
                        "options": [], "multi": False, "submit_row": "",
                        "tabs": [], "context": "unparsed", "kind": "raw",
                        "raw": "\n".join(screen[-20:]),
                    }
                else:
                    r["needs_user"] = False
            else:
                r["ask"] = None


# A screen that unmistakably waits for the user, even without parsed options.
_NEEDS_USER_MARKERS = (
    "paste code here", "paste the code", "enter the code", "sign in",
    "log in", "login", "authorize", "device code", "press enter",
    "do you want", "[y/n]", "yes/no", "overwrite?",
)


def _screen_needs_user(screen: list[str], agent_status: str) -> bool:
    text = "\n".join(screen[-40:]).lower()
    if any(m in text for m in _NEEDS_USER_MARKERS):
        return True
    # Measured: herdr can report `done` while the agent is actually waiting.
    # Only trust a plain idle/done screen when nothing looks like a question.
    return False


def _track_usage(pane: str, st: dict) -> None:
    pct = st.get("usage_5h_pct")
    if pct is None:
        return
    hist = STATE.usage_history.setdefault(pane, [])
    hist.append((time.time(), pct))
    cutoff = time.time() - 45 * 60
    STATE.usage_history[pane] = [h for h in hist if h[0] > cutoff]


def poll_loop() -> None:
    last = 0.0
    while True:
        try:
            reconcile_agents()
            poll_transcripts()
            read_screens()
            check_roster_restart()
            _detect_alerts()
            cfg = config()
            now = time.time()
            if now - last >= cfg["roster"]["save_seconds"]:
                save_roster()
                last = now
            STATE.changed()
        except Exception as e:  # never let the loop die
            print(f"poll_loop error: {e}", file=sys.stderr)
        time.sleep(3.0)


# --------------------------------------------------------------------------
# roster / rehire
# --------------------------------------------------------------------------

def save_roster() -> None:
    with STATE.lock:
        roster = [{
            "workspace_id": r.get("workspace_id"),
            "tab_id": r.get("tab_id"),
            "cwd": r.get("cwd"),
            "kind": r.get("kind"),
            "name": r.get("name"),
            "session_id": r.get("session_id", ""),
            "pane": r.get("pane"),
        } for r in STATE.agents.values()]
        closed = STATE.closed[-config()["roster"]["max_closed"]:]
    try:
        ROSTER_PATH.write_text(json.dumps(
            {"roster": roster, "closed": closed, "time": time.time()}, indent=2))
    except OSError:
        pass


def check_roster_restart() -> None:
    """If more than half the agents disappear at once, freeze the roster."""
    try:
        data = json.loads(ROSTER_PATH.read_text())
    except (OSError, ValueError):
        return
    prev = data.get("roster", [])
    if not prev:
        return
    with STATE.lock:
        live = len(STATE.agents)
    if live == 0 and len(prev) > 1 and STATE.frozen_roster is None:
        STATE.frozen_roster = prev
        _add_alert("restart", "More than half the agents disappeared at once. "
                   "Rehire them when the harness is back.", pane="")
    elif live >= len(prev) and STATE.frozen_roster is not None:
        STATE.frozen_roster = None


def _prune_closed() -> None:
    cfg = config()["roster"]
    cutoff = time.time() - cfg["closed_ttl_hours"] * 3600
    STATE.closed = [c for c in STATE.closed
                    if c.get("closed_at", 0) > cutoff][-cfg["max_closed"]:]


# --------------------------------------------------------------------------
# Workflow rules (git classification, stations, ticket inference) are pure and
# live in board.workflow; re-exported here for the board builder and tests.
from .workflow import (  # noqa: E402
    CODE_EXT, STATIONS, STAGE_ORDER, GIT_RULES, classify_git,
    edited_code_files, compute_stations, agent_stage, ticket_stage,
    _first_prompt, _branch_ticket, derive_ticket,
)


def _detect_alerts() -> None:
    """Workflow alerts from the last 24 hours. Pure heuristics, never guesses."""
    with STATE.lock:
        agents = list(STATE.agents.items())
    cutoff = time.time() - 24 * 3600
    for pane, rec in agents:
        events = STATE.events.get(pane, [])
        repo = repo_root(rec.get("cwd") or "") or None
        commits = [e for e in events if e.get("kind") == "bash"
                   and "commit" in classify_git(e.get("command", ""))]
        if not commits:
            continue
        tests = [e for e in events if e.get("kind") == "subagent_start"
                 and "test" in e.get("subagent_type", "").lower()]
        reviews = [e for e in events if e.get("kind") == "subagent_start"
                   and "review" in e.get("subagent_type", "").lower()]
        code = edited_code_files(events)
        for c in commits:
            key = f"{pane}:{c.get('ts')}:code_no_test"
            if code and not tests and not reviews and key not in STATE.dismissed:
                _add_alert("code_no_test", "A commit carried code but no test "
                           "subagent or test command ran since the previous commit.",
                           pane=pane, key=key)
        # stage-everything
        for e in events:
            if e.get("kind") != "bash":
                continue
            if "stage_all" in classify_git(e.get("command", "")):
                key = f"{pane}:{e.get('ts')}:stage_all"
                if code and key not in STATE.dismissed:
                    _add_alert("stage_all", "Used stage-everything (git add -A / "
                               "commit -a) in a session that changed code.",
                               pane=pane, key=key)


def _add_alert(kind: str, message: str, pane: str = "", key: str = "") -> None:
    key = key or f"{pane}:{kind}:{int(time.time())}"
    with STATE.lock:
        if any(a["key"] == key for a in STATE.alerts):
            return
        STATE.alerts.append({"kind": kind, "message": message, "pane": pane,
                             "key": key, "time": time.time()})
        STATE.alerts = STATE.alerts[-200:]



def build_board() -> dict:
    """Ticket-centric board: one row per work item, agents as chips.

    The board's columns are work stages only. Agent runtime state
    (idle/working/blocked/needs-you) is a lamp on the chip and a filter, never
    a column, because an agent and a ticket have different lifecycles.
    """
    with STATE.lock:
        agents = list(STATE.agents.values())
        events = {p: list(v) for p, v in STATE.events.items()}
        closed = list(STATE.closed)
        alerts = list(STATE.alerts)
        frozen = STATE.frozen_roster

    wt_by_path = _worktree_index()
    agent_rows: list[dict] = []
    tickets: dict[str, dict] = {}

    for rec in agents:
        pane = rec["pane"]
        evs = events.get(pane, [])
        cwd = rec.get("cwd") or ""
        repo = repo_root(cwd) or None
        branch = ""
        if repo:
            rc, b = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
            branch = b.strip() if rc == 0 else ""
        wt = wt_by_path.get(os.path.realpath(cwd))
        wt_branch = wt.get("branch", "") if wt else ""
        stage = agent_stage(rec, evs, repo)
        inferred = derive_ticket(rec, evs, repo, branch, wt_branch)
        status = rec.get("status", {})
        chip = {
            "pane": pane,
            "name": rec.get("name") or pane,
            "kind": rec.get("kind"),
            "agent_status": rec.get("agent_status", "unknown"),
            "needs_user": bool(rec.get("needs_user")),
            "stage": stage,
            "last_line": _last_line(rec, evs),
            "context_pct": status.get("context_pct"),
            "tab_label": rec.get("tab_label", ""),
            "ws_label": rec.get("ws_label", ""),
        }
        agent_rows.append(chip)
        tid = inferred["id"]
        t = tickets.setdefault(tid, {
            "id": tid, "name": inferred["name"], "source": inferred["source"],
            "agents": [], "repo": repo, "branch": branch,
            "worktree": wt_branch, "stage": "To do", "unpushed": None,
            "needs_you_count": 0, "context_pct_max": None,
        })
        t["agents"].append(chip)
        if wt_branch:
            t["worktree"] = wt_branch
        if repo and not t["branch"]:
            t["branch"] = branch
        if not t["source"]:
            t["source"] = inferred["source"]
        if chip["needs_user"]:
            t["needs_you_count"] += 1
        if chip["context_pct"] is not None:
            t["context_pct_max"] = max(t["context_pct_max"] or 0,
                                       chip["context_pct"])

    for t in tickets.values():
        t["stage"] = ticket_stage(t["agents"])
        repo = t.get("repo")
        if repo:
            rc, count = git(repo, "rev-list", "--count", "HEAD", "--not",
                            "--remotes")
            t["unpushed"] = int(count.strip() or 0) if rc == 0 else None

    # Parked: worktrees with no agent inside.
    parked = list_worktrees()

    cols = STAGE_ORDER + ["Parked"]
    ticket_list = sorted(tickets.values(),
                         key=lambda x: (-STAGE_ORDER.index(x["stage"])
                                        if x["stage"] in STAGE_ORDER else 0,
                                        x["name"].lower()))
    return {
        "tickets": ticket_list,
        "agents": agent_rows,
        # compat: the old per-agent card list and ticket map, one release.
        "cards": _compat_cards(ticket_list),
        "alerts": [a for a in alerts if a["key"] not in STATE.handled][-50:],
        "closed": closed[-30:],
        "frozen_roster": frozen,
        "worktrees": parked,
        "remote_on": config()["remote"]["mode"] != "none",
        "columns": cols,
        "stages": STAGE_ORDER,
        "now": int(time.time()),
    }


def _compat_cards(tickets: list[dict]) -> list[dict]:
    """Flatten tickets back to the old card shape for older clients/tests."""
    out = []
    for t in tickets:
        for a in t["agents"]:
            out.append({
                "pane": a["pane"], "name": a["name"], "kind": a["kind"],
                "cwd": a.get("cwd", ""), "repo": t.get("repo"),
                "branch": t.get("branch", ""), "ticket": t["name"],
                "stations": [], "current_station": None, "status": {},
                "agent_status": a["agent_status"], "needs_user": a["needs_user"],
                "ask": None, "last_line": a["last_line"],
                "column": t["stage"], "time_in_column": 0, "unpushed": None,
                "context_pct": a.get("context_pct"),
            })
    return out


def _worktree_index() -> dict[str, dict]:
    """Map realpath -> worktree record, for linking an agent's cwd to its
    worktree. Uses native herdr worktree listing when available."""
    idx: dict[str, dict] = {}
    for wt in list_worktrees(include_occupied=True):
        idx[os.path.realpath(wt["path"])] = wt
    return idx


def _last_line(rec: dict, events: list[dict]) -> str:
    for e in reversed(events):
        if e.get("kind") == "reply" and e.get("text"):
            return e["text"].splitlines()[0][:160]
        if e.get("kind") == "prompt" and e.get("text"):
            return e["text"].splitlines()[0][:160]
    tail = rec.get("screen_tail") or []
    for ln in reversed(tail):
        if ln.strip():
            return ln.strip()[:160]
    return ""


def build_agent(pane: str) -> dict:
    with STATE.lock:
        rec = STATE.agents.get(pane)
        events = list(STATE.events.get(pane, []))
        alerts = [a for a in STATE.alerts if a["pane"] == pane]
    if not rec:
        return {"error": "not_found"}
    repo = repo_root(rec.get("cwd") or "")
    status_git = git_status(repo) if repo else None
    adapter = get_adapter(rec.get("kind", ""))
    chat = _build_chat(events)
    return {
        "pane": pane, "name": rec.get("name") or pane, "kind": rec.get("kind"),
        "cwd": rec.get("cwd"), "repo": repo, "git": status_git,
        "chat": chat, "alerts": alerts,
        "supports": adapter.supports if adapter else {},
        "notes": adapter.notes if adapter else [],
        "ask": rec.get("ask"), "screen_tail": rec.get("screen_tail", []),
        "status": rec.get("status", {}),
        "usage_history": STATE.usage_history.get(pane, []),
        "rehire_key": pane,
    }


def _build_chat(events: list[dict]) -> list[dict]:
    """Prompts, replies, tool runs folded into one row, Q&A pairs."""
    out = []
    for e in events:
        k = e.get("kind")
        if k in ("prompt", "reply", "answer", "question"):
            out.append(e)
        elif k in ("edit", "bash", "subagent_start", "subagent_end", "tokens"):
            if out and out[-1].get("_fold") == k:
                out[-1]["_count"] = out[-1].get("_count", 1) + 1
                continue
            row = dict(e)
            row["_fold"] = k
            row["_count"] = 1
            out.append(row)
    return out[-500:]


# --------------------------------------------------------------------------
# worktrees (parked)
# --------------------------------------------------------------------------

def list_worktrees(include_occupied: bool = False) -> list[dict]:
    """Worktrees across agent repos and configured roots.

    Measured 0.9.2: `herdr worktree list [--cwd <repo>]` returns worktrees with
    branch, path, open_workspace_id and is_prunable. We use it first, because it
    maps a worktree to the workspace (and so the agent) that has it open; a raw
    `git worktree list` cannot. `include_occupied` returns every worktree,
    including ones an agent is inside (used to link an agent to its worktree).
    """
    roots = set()
    with STATE.lock:
        for rec in STATE.agents.values():
            r = repo_root(rec.get("cwd") or "")
            if r:
                roots.add(r)
    for r in config().get("project_roots", []):
        rr = repo_root(os.path.expanduser(r))
        if rr:
            roots.add(rr)
    # A worktree created from the UI has no agent inside yet, so its parent repo
    # is otherwise never scanned. Recover it from the worktree path
    # (~/.herdr/worktrees/<repo>/<branch>) and from any pane cwd in the snapshot.
    roots.update(_repos_from_herdr())

    out: list[dict] = []
    seen: set[str] = set()
    for repo in roots:
        trees = _herdr_worktrees(repo)
        if trees is None:
            trees = _git_worktrees(repo)
        for wt in trees:
            path = os.path.realpath(wt.get("path", ""))
            if not path or path in seen or not os.path.isdir(path):
                continue
            if path == os.path.realpath(repo):
                continue
            seen.add(path)
            with STATE.lock:
                occupied = any(os.path.realpath(rec.get("cwd") or "")
                               == path for rec in STATE.agents.values())
            if occupied and not include_occupied:
                continue
            rc, dirty = git(path, "status", "--porcelain")
            uncommitted = len([x for x in dirty.splitlines() if x.strip()])
            rc, count = git(path, "rev-list", "--count", "HEAD", "--not",
                            "--remotes", "--not", "main", "--not", "master")
            branch = wt.get("branch", "")
            out.append({
                "repo": repo, "path": path, "branch": branch,
                "ticket": _branch_ticket(branch) if branch else "Unnamed work",
                "uncommitted": uncommitted,
                "unmerged": int(count.strip() or 0) if rc == 0 else 0,
                "open_workspace_id": wt.get("open_workspace_id", ""),
                "occupied": occupied,
                "prunable": bool(wt.get("is_prunable")),
            })
    return out


def _repos_from_herdr() -> set[str]:
    """Main repos herdr knows about, so parked worktrees are always visible.

    Sources: the worktree base (~/.herdr/worktrees/<repo>/<branch>) and every
    pane cwd in the snapshot (a worktree-backed workspace's root pane cwd is its
    checkout). Both are best-effort; failures just mean fewer roots.
    """
    roots: set[str] = set()
    base = os.path.expanduser("~/.herdr/worktrees")
    if os.path.isdir(base):
        for repo_dir in os.listdir(base):
            p = os.path.join(base, repo_dir)
            rr = repo_root(p) if os.path.isdir(p) else None
            if rr:
                roots.add(rr)
            else:
                # the base may hold the bare repo itself
                if os.path.isdir(os.path.join(p, ".git")) or \
                        os.path.isfile(os.path.join(p, ".git")):
                    roots.add(p)
    try:
        snap = snapshot()
    except HerdrError:
        snap = {}
    pane_cwds = {p.get("cwd", "") for p in snap.get("panes", [])}
    for cwd in pane_cwds:
        rr = repo_root(cwd) if cwd else None
        if rr:
            roots.add(rr)
    return roots


def _herdr_worktrees(repo: str) -> list[dict] | None:
    """Native herdr worktree list, or None if unsupported/failed."""
    try:
        j = herdr("worktree", "list", "--cwd", repo, timeout=15)
    except HerdrError:
        return None
    r = j.get("result", j)
    if not isinstance(r, dict):
        return None
    wts = r.get("worktrees")
    if not isinstance(wts, list):
        return None
    out = []
    for w in wts:
        if not isinstance(w, dict):
            continue
        out.append({
            "path": w.get("path", ""),
            "branch": _clean_branch(w.get("branch", "")),
            "open_workspace_id": w.get("open_workspace_id", ""),
            "is_prunable": w.get("is_prunable", False),
            "is_linked_worktree": w.get("is_linked_worktree", False),
        })
    return out


def _git_worktrees(repo: str) -> list[dict]:
    rc, out = git(repo, "worktree", "list", "--porcelain")
    if rc != 0:
        return []
    trees, cur = [], {}
    for line in out.splitlines():
        if line.startswith("worktree "):
            if cur:
                trees.append(cur)
            cur = {"path": line[9:]}
        elif line.startswith("branch "):
            cur["branch"] = _clean_branch(line[7:])
        elif line.strip() == "detached":
            cur["detached"] = True
    if cur:
        trees.append(cur)
    return trees


def _clean_branch(b: str) -> str:
    return (b or "").replace("refs/heads/", "").strip()


# Measured 0.9.2 / harness CLIs: the "yolo" bypass flag per harness.
YOLO_ARGS = {
    "claude": ["--dangerously-skip-permissions"],
    "codex": ["--dangerously-bypass-approvals-and-sandbox"],
    "opencode": ["--auto"],
}


def yolo_args(kind: str) -> list[str]:
    return YOLO_ARGS.get(kind, [])



# --------------------------------------------------------------------------
# hire / rehire implementations
# --------------------------------------------------------------------------

def _hire_impl(kind: str, folder: str, name: str, message: str,
               workspace: str, workspace_label: str = "",
               use_worktree: bool = False, worktree_branch: str = "",
               worktree_base: str = "", yolo: bool = False) -> None:
    """Hire an agent, optionally in its own git worktree.

    A herdr workspace holds one or more tabs; each tab is one shell pane and so
    one agent. Hiring either adds a tab to an existing workspace or creates a
    new workspace named by the user.

    When `use_worktree` is set, the agent's pane is opened inside a fresh git
    worktree (branch `worktree_branch`), and the worktree's path and workspace
    are recorded on the agent so they can be cleared when it is fired.
    """
    wt = None
    if use_worktree:
        wt = create_agent_worktree(folder, worktree_branch, worktree_base,
                                   workspace_label or name)
        cwd = wt["path"]
        pane = wt["pane"]
        workspace = wt.get("workspace_id") or workspace
    else:
        cwd = folder
        pane = _new_shell_pane(workspace, workspace_label, folder, name)

    start_args = yolo_args(kind) if yolo else []
    # agent start fails with agent_pane_busy just after tab/pane creation.
    last_err = None
    for _ in range(20):
        try:
            cmd = ["agent", "start", name, "--kind", kind, "--pane", pane,
                   "--timeout", "60000"]
            if start_args:
                cmd += ["--", *start_args]
            herdr(*cmd, timeout=75)
            last_err = None
            break
        except HerdrError as e:
            last_err = e
            if e.code in ("agent_pane_busy", "agent_not_ready"):
                time.sleep(1.5)
                continue
            raise
    if last_err:
        raise last_err
    # Remember the requested name on the pane record: herdr may not report the
    # name in its snapshot (measured: codex start that was slow to register).
    with STATE.lock:
        rec = STATE.agents.setdefault(pane, {"pane": pane})
        rec["hired_name"] = name
        rec["name"] = rec.get("name") or name
        rec["kind"] = kind
        rec["cwd"] = cwd
        rec["yolo"] = bool(yolo)
        if wt:
            rec["worktree_path"] = wt["path"]
            rec["worktree_branch"] = wt["branch"]
            rec["worktree_workspace"] = wt.get("workspace_id", "")
        rec.setdefault("col_since", time.time())
    if message.strip():
        # send once the agent is ready
        for _ in range(20):
            try:
                herdr("agent", "prompt", pane, message, timeout=20)
                break
            except HerdrError as e:
                if e.code in ("agent_not_ready", "agent_blocked"):
                    time.sleep(1.5)
                    continue
                break


def _default_wt_branch(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "agent").lower()).strip("-")
    return f"agent/{slug or 'agent'}"


def create_agent_worktree(repo: str, branch: str, base: str,
                          label: str) -> dict:
    """Create a worktree via native herdr and return its pane, path, workspace.

    Measured 0.9.2: `herdr worktree create --cwd <repo> --branch <name>
    [--base <ref>] --label <text> --no-focus` returns result.root_pane with the
    new pane id, whose cwd is the worktree path. Ownership is recorded so the
    worktree can be removed when the agent is fired.
    """
    if not repo_root(repo):
        raise HerdrError("bad_repo", "not a git repository")
    branch = branch or _default_wt_branch(label)
    if not re.match(r"^[A-Za-z0-9._/-]+$", branch):
        raise HerdrError("bad_branch", "invalid branch name")
    args = ["worktree", "create", "--cwd", repo, "--branch", branch,
            "--no-focus"]
    if base:
        args += ["--base", base]
    if label:
        args += ["--label", label]
    j = herdr(*args, timeout=90)
    r = j.get("result", j)
    root = r.get("root_pane", {}) if isinstance(r, dict) else {}
    pane = root.get("pane_id", "")
    if not valid_pane(pane):
        raise HerdrError("worktree_failed", "no pane returned")
    return {
        "pane": pane,
        "path": root.get("cwd", "") or root.get("foreground_cwd", ""),
        "branch": branch,
        "workspace_id": root.get("workspace_id", ""),
    }


def remove_agent_worktree(rec: dict) -> str:
    """Remove the worktree an agent owns, refusing while it is dirty.

    Measured 0.9.2: `herdr worktree remove --workspace <ID>` removes a linked
    worktree checkout by its workspace. Never uses --force. Returns "" on
    success, or a reason string when it was skipped.
    """
    path = rec.get("worktree_path") or ""
    ws = rec.get("worktree_workspace") or ""
    if not path:
        return "no worktree"
    if not os.path.isdir(path):
        return "already gone"
    rc, dirty = git(path, "status", "--porcelain")
    if rc == 0 and dirty.strip():
        return "uncommitted changes"
    # A worktree with commits that are not on any remote is not safe to delete
    # silently — but only when the repo actually has a remote. With no remote,
    # every commit is "unpushed" and that would block every removal.
    rc, remotes = git(path, "remote")
    if rc == 0 and remotes.strip():
        rc, count = git(path, "rev-list", "--count", "HEAD", "--not",
                        "--remotes")
        if rc == 0 and int(count.strip() or 0) > 0:
            return "unpushed commits"
    try:
        if ws:
            try:
                herdr("worktree", "remove", "--workspace", ws, timeout=30)
                return ""
            except HerdrError as e:
                # Measured: closing an agent's sole tab already removes its
                # workspace, so by fire time it may be gone. Fall back to
                # removing the worktree directly by path.
                if e.code != "workspace_not_found":
                    raise
        _remove_worktree_by_path(path)
    except HerdrError as e:
        return e.code or "remove failed"
    return ""


def _remove_worktree_by_path(path: str) -> None:
    """Remove a worktree checkout by path, via its main repository."""
    # Find the main repo so `git worktree remove` runs from a stable checkout.
    rc, common = git(path, "rev-parse", "--path-format=absolute",
                     "--git-common-dir")
    main_repo = os.path.dirname(common.strip()) if rc == 0 and common.strip() \
        else path
    p = subprocess.run(["git", "-C", main_repo, "worktree", "remove", path],
                       capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise HerdrError("remove_failed", p.stderr.strip()[:200])


def _new_shell_pane(workspace: str, workspace_label: str, folder: str,
                    agent_name: str) -> str:
    """Return a fresh shell pane: a new tab in an existing workspace, or the
    root tab of a newly created workspace named by the user."""
    label = workspace_label.strip() or agent_name or os.path.basename(folder)
    if workspace and workspace != "new":
        # Measured: tab create adds a tab (and its root pane) to a workspace,
        # so several agents can live in one workspace.
        j = herdr("tab", "create", "--workspace", workspace, "--cwd", folder,
                  "--label", label, "--no-focus", timeout=20)
        r = j.get("result", j)
        pane = r.get("root_pane", {}).get("pane_id") or r.get("pane_id", "")
        if valid_pane(pane):
            return pane
    else:
        j = herdr("workspace", "create", "--label", label,
                  "--cwd", folder, "--no-focus", timeout=20)
        r = j.get("result", j)
        pane = r.get("root_pane", {}).get("pane_id") or r.get("pane_id", "")
        if valid_pane(pane):
            return pane
    # fallback: split the focused pane
    try:
        j = herdr("pane", "split", "--direction", "right", "--cwd", folder,
                  "--no-focus", timeout=20)
        r = j.get("result", j)
        pane = r.get("pane", {}).get("pane_id") or r.get("pane_id", "")
        if valid_pane(pane):
            return pane
    except HerdrError:
        pass
    raise HerdrError("no_pane", "could not create a shell pane for hire")


def _workspace_mode(workspace: str, label: str) -> str:
    """'tab' to add a tab to an existing workspace, else 'create' a new one.

    Measured 0.9.2: a workspace holds many tabs; each tab is one agent. Hiring
    into an existing workspace adds a tab so several agents share it."""
    if workspace and workspace not in ("new", "existing"):
        return "tab"
    return "create"


def list_workspaces() -> list[dict]:
    """Workspaces with their tabs and agent names, for the Hire picker."""
    try:
        snap = snapshot()
    except HerdrError:
        return []
    agents_by_ws: dict[str, list[str]] = {}
    for a in snap.get("agents", []):
        agents_by_ws.setdefault(a.get("workspace_id", ""), []).append(
            a.get("name") or a.get("pane_id", ""))
    tabs_by_ws: dict[str, int] = {}
    for t in snap.get("tabs", []):
        tabs_by_ws[t.get("workspace_id", "")] = \
            tabs_by_ws.get(t.get("workspace_id", ""), 0) + 1
    out = []
    for w in snap.get("workspaces", []):
        wid = w.get("workspace_id", "")
        out.append({
            "id": wid,
            "label": w.get("label", ""),
            "tabs": tabs_by_ws.get(wid, 0),
            "agents": agents_by_ws.get(wid, []),
        })
    return out



def _rehire_impl(entry: dict) -> None:
    name = entry.get("name", "")
    kind = entry.get("kind", "")
    folder = entry.get("cwd") or os.path.expanduser("~")
    sid = entry.get("session_id", "")
    pane = _new_shell_pane("new", folder)
    args = []
    adapter = get_adapter(kind)
    if sid and adapter:
        args = adapter.resume_args(sid)
    last = None
    for _ in range(20):
        try:
            cmd = ["agent", "start", name, "--kind", kind, "--pane", pane,
                   "--timeout", "60000"]
            if args:
                cmd += ["--", *args]
            herdr(*cmd, timeout=75)
            last = None
            break
        except HerdrError as e:
            last = e
            if e.code in ("agent_pane_busy", "agent_not_ready"):
                time.sleep(1.5)
                continue
            raise
    if last:
        raise last


def _find_closed(key: str) -> dict | None:
    with STATE.lock:
        for c in STATE.closed:
            if c.get("pane") == key or c.get("name") == key:
                return c
    return None


def list_folders(path: str) -> dict:
    """Directories under $HOME for the Hire folder picker.

    Skips dot-directories and anything deeper than 4 levels below $HOME, so the
    picker stays fast and never exposes unrelated system trees.
    """
    home = os.path.realpath(os.path.expanduser("~"))
    cur = os.path.expanduser(path or "~")
    if not os.path.isabs(cur):
        cur = os.path.join(home, cur)
    cur = os.path.realpath(cur)
    if not cur.startswith(home) or not os.path.isdir(cur):
        cur = home
    rel = os.path.relpath(cur, home)
    depth = 0 if rel == "." else rel.count(os.sep) + 1
    dirs: list[dict] = []
    try:
        for name in sorted(os.listdir(cur), key=str.lower):
            if name.startswith("."):
                continue
            full = os.path.join(cur, name)
            if not os.path.isdir(full) or os.path.islink(full):
                continue
            child_depth = depth + 1
            dirs.append({
                "name": name,
                "path": full,
                "has_children": child_depth < 4 and _has_dir(full),
            })
    except OSError:
        pass
    parent = None
    if cur != home:
        parent = os.path.dirname(cur)
    return {"path": cur, "home": home, "parent": parent,
            "can_descend": depth < 4, "dirs": dirs}


def _has_dir(path: str) -> bool:
    try:
        for name in os.listdir(path):
            if name.startswith("."):
                continue
            if os.path.isdir(os.path.join(path, name)):
                return True
    except OSError:
        return False
    return False


def settings_public() -> dict:
    cfg = config()
    return {
        "session": cfg["herdr_session"],
        "remote": {"mode": cfg["remote"]["mode"],
                   "hostnames": cfg["remote"]["hostnames"]},
        "webhook_kind": cfg["notifications"].get("webhook", {}).get("kind", "none"),
        "docs_repo": cfg["docs_repo"],
        "machine_tokens": list(SESSIONS.machine_tokens.keys()),
        "adapters": capability_table(),
    }



