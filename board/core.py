"""Agent Board core implementation."""
from __future__ import annotations

from datetime import datetime
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from adapters import ADAPTERS, get as get_adapter
from adapters.base import Event as AEvent

ROOT = Path(__file__).resolve().parent.parent
ROSTER_PATH = ROOT / "roster.json"
from .config import DEFAULT_CONFIG, _config_lock, load_config, config, _deep_merge, save_config
from .gitrepo import git, repo_root, git_status, git_diff_file, git_diff_commit
from .herdr import (
    HerdrError,
    _herdr_env,
    herdr,
    herdr_text,
    snapshot,
    PANE_RE,
    SHA_RE,
    NAME_RE,
    TICKET_RE,
    ALLOWED_KEYS,
    valid_pane,
    valid_keys,
    read_screen,
    _extract_read_text,
)
from .workflow import (
    CODE_EXT,
    STATIONS,
    shell_commands,
    classify_git,
    edited_code_files,
    has_test_action,
    has_review_action,
    compute_stations,
    STAGE_ORDER,
    _STATION_TO_STAGE,
    agent_stage,
    ticket_stage,
    _first_prompt,
    _branch_ticket,
    derive_ticket,
)
from .notify import (
    _last_page_seen,
    _page_lock,
    note_page_open,
    page_recently_open,
    notify,
    _send_webhook,
    _board_url,
)
class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.offsets: dict[str, int] = {}
        self.transcript_versions: dict[str, str] = {}
        self.feedback_revision = 0
        self.agents: dict[str, dict] = {}       # pane -> agent record
        self.events: dict[str, list[dict]] = {}  # pane -> normalized events
        self.closed: list[dict] = []             # rehirable
        self.alerts: list[dict] = []
        self.dismissed: set[str] = set()
        self.handled: set[str] = set()
        self.notified: dict[str, float] = {}
        self.attention_notified: set[str] = set()
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
            # Subscribers only need to know that state changed, not each event.
            self.items[:] = [item]
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
                history = STATE.events[pane]
                indices = {event.get("event_id"): i for i, event in enumerate(history)
                           if event.get("event_id")}
                for event in _norm_events(pane, evs):
                    event_id = event.get("event_id")
                    if event_id and event_id in indices:
                        history[indices[event_id]] = event
                    else:
                        if event_id:
                            indices[event_id] = len(history)
                        history.append(event)
                STATE.events[pane] = history[-2000:]
                STATE.feedback_revision += 1


def read_screens() -> None:
    """Read screen tails for agents that need questions or status lines."""
    pending_notifications = []
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
        st = adapter.session_status(rec.get("session_id", "")) if adapter else {}
        if adapter and adapter.supports.get("status_line"):
            st.update(adapter.status_line(screen))
        with STATE.lock:
            r = STATE.agents.get(pane)
            if not r:
                continue
            r["screen_tail"] = screen[-60:]
            r["status"] = st
            _track_usage(pane, st)
            r["screen_updated_at"] = time.time()
            ask = adapter.parse_prompt(screen) if adapter and adapter.supports.get("prompts") else None
            r["ask"] = ask.to_json() if ask else None
            # Every harness gets the same attention/fallback behavior.
            r["needs_user"] = bool(ask) or r.get("agent_status") == "blocked" or _screen_needs_user(screen, r.get("agent_status"), kind)
            if not r["needs_user"]:
                STATE.attention_notified.discard(pane)
            elif pane not in STATE.attention_notified:
                pending_notifications.append((pane, r.get("name") or pane))
            if r["needs_user"] and not ask:
                r["ask"] = {
                    "question": "\n".join(screen[-6:]).strip(),
                    "options": [], "multi": False, "submit_row": "",
                    "tabs": [], "context": "unparsed", "kind": "raw",
                    "raw": "\n".join(screen[-20:]),
                }

    for pane, name in pending_notifications:
        if notify(f"Needs you: {name}", "An agent is waiting for your input. Open the board to respond.", pane):
            with STATE.lock:
                STATE.attention_notified.add(pane)


# A screen that unmistakably waits for the user, even without parsed options.
_NEEDS_USER_MARKERS = (
    "paste code here", "paste the code", "enter the code", "sign in",
    "log in", "login", "authorize", "device code", "press enter",
    "do you want", "[y/n]", "yes/no", "overwrite?",
)


def _screen_needs_user(screen: list[str], agent_status: str, kind: str = "") -> bool:
    # Codex keeps historical output above its ordinary input box. Markers in
    # that history (or the user draft) are not an active approval prompt. Parsed
    # numbered menus and an explicit blocked runtime state still take priority.
    if kind == "codex" and any(line.lstrip().startswith("›") and not re.match(r"^\d+\.", line.lstrip()[1:].lstrip()) for line in screen[-8:]):
        return False
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


def feedback_signature() -> str:
    """Ignore polling timestamps; publish meaningful changes and transcript revisions."""
    with STATE.lock:
        keys = ("pane", "name", "kind", "cwd", "agent_status", "session_id",
                "tab_label", "ws_label", "needs_user", "ask", "status", "screen_tail")
        state = {
            "agents": [{key: rec.get(key) for key in keys} for rec in STATE.agents.values()],
            "revision": STATE.feedback_revision,
            "alerts": STATE.alerts,
            "closed": STATE.closed,
            "dismissed": sorted(STATE.dismissed),
            "handled": sorted(STATE.handled),
            "frozen": STATE.frozen_roster,
        }
        return __import__("hashlib").sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def poll_loop() -> None:
    last = 0.0
    last_feedback = None
    last_git_refresh = 0.0
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
            current_feedback = feedback_signature()
            if current_feedback != last_feedback or now - last_git_refresh >= cfg["workflow"]["git_reread_seconds"]:
                STATE.changed()
                last_feedback = current_feedback
                last_git_refresh = now
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


def _event_is_recent(event: dict, cutoff: float) -> bool:
    value = event.get("ts")
    if not value:
        return True
    try:
        timestamp = float(value)
        if timestamp > 100_000_000_000:
            timestamp /= 1000
    except (ValueError, TypeError):
        try:
            timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return True
    return timestamp >= cutoff


def _detect_alerts() -> None:
    """Use observed actions between commits, and limit alerts to the last day."""
    with STATE.lock:
        agents = [(pane, list(STATE.events.get(pane, []))) for pane in STATE.agents]
        dismissed = set(STATE.dismissed)
    cutoff = time.time() - 24 * 3600
    for pane, events in agents:
        since_commit = []
        for event in events:
            since_commit.append(event)
            if event.get("kind") != "bash":
                continue
            kinds = classify_git(event.get("command", ""))
            code = edited_code_files(since_commit)
            if "stage_all" in kinds and code and _event_is_recent(event, cutoff):
                key = f"{pane}:{event.get('ts')}:stage_all"
                if key not in dismissed:
                    _add_alert("stage_all", "Used stage-everything (git add -A / "
                               "commit -a) after changing code.", pane=pane, key=key)
            if "commit" in kinds:
                key = f"{pane}:{event.get('ts')}:code_no_test"
                if code and not has_test_action(since_commit) and not has_review_action(since_commit) and key not in dismissed and _event_is_recent(event, cutoff):
                    _add_alert("code_no_test", "Code changed before this commit, but no test "
                               "or review action was observed since the previous commit.", pane=pane, key=key)
                since_commit = []


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
        agents = [dict(rec) for rec in STATE.agents.values()]
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
        "agent_status": rec.get("agent_status", "unknown"),
        "needs_user": bool(rec.get("needs_user")),
        "updated_at": rec.get("screen_updated_at", rec.get("last_seen")),
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
        elif k in ("edit", "bash", "subagent_start", "subagent_end", "tokens", "tool"):
            if out and out[-1].get("_fold") == k:
                out[-1]["_count"] = out[-1].get("_count", 1) + 1
                continue
            row = dict(e)
            row["_fold"] = k
            row["_count"] = 1
            out.append(row)
    return out[-500:]



from .worktrees import (
    list_worktrees,
    _repos_from_herdr,
    _herdr_worktrees,
    _git_worktrees,
    _clean_branch,
    YOLO_ARGS,
    yolo_args,
)
from .hire import (
    _hire_impl,
    _default_wt_branch,
    create_agent_worktree,
    worktree_removal_blocker,
    remove_agent_worktree,
    _remove_worktree_by_path,
    _new_shell_pane,
    _workspace_mode,
    list_workspaces,
    _rehire_impl,
    _find_closed,
    list_folders,
    _has_dir,
    settings_public,
)
