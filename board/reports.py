"""Reports: push review, while-you-were-away digest, restart risk, usage
forecast and the live restart/attention ops.

Ported from the reference workflow-viz implementation, minus its relay.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .config import config
from .gitrepo import git, repo_root, git_status
from .herdr import HerdrError, snapshot
from .suggest import rank  # noqa: F401  (re-exported convenience)

_usage: list[tuple[float, float]] = []
_usage_alerted = {"key": None}


def _secs(txt: str | None) -> float | None:
    """Parse '1h08m' / '36h28m' / '5m' into seconds."""
    if not txt:
        return None
    m = __import__("re").match(r"(?:(\d+)h)?(?:(\d+)m)?", txt.strip())
    if not m or not (m.group(1) or m.group(2)):
        return None
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60


def usage_forecast(screens: dict) -> dict | None:
    """5h/7d usage with a rising trend and an ETA to the limit."""
    rows = [v for v in screens.values() if v.get("usage_5h_pct") is not None]
    if not rows:
        return None
    best = max(rows, key=lambda v: v.get("updated") or 0)
    p5 = float(best["usage_5h_pct"])
    p7 = float(best["usage_7d_pct"]) if best.get("usage_7d_pct") is not None else None
    now = time.time()
    rs = _secs(best.get("usage_5h_reset"))
    reset = (now + rs) if rs is not None else None
    if not _usage or now - _usage[-1][0] > 60 or p5 < _usage[-1][1]:
        if _usage and p5 < _usage[-1][1] - 5:
            _usage.clear()  # the window reset
        _usage.append((now, p5))
        del _usage[:-240]
    old = next((u for u in _usage if now - u[0] <= 45 * 60), None)
    rate = ((p5 - old[1]) / (now - old[0])
            if old and now - old[0] >= 5 * 60 else None)
    eta = now + (100 - p5) / rate if rate and rate > 0 else None
    hits = bool(eta and (reset is None or eta < reset))
    return {"pct5": p5, "pct7": p7, "reset5": round(reset) if reset else None,
            "eta": round(eta) if eta else None,
            "per_hour": round(rate * 3600, 1) if rate else None,
            "hits_limit": hits}


def restart_risk(agents: dict) -> dict:
    """What a restart could lose, per agent: uncommitted files, unpushed
    commits, context, and whether the repo is shared."""
    repos: dict[str, dict] = {}
    rows = []
    for pane, rec in agents.items():
        cwd = rec.get("cwd") or ""
        top = None
        try:
            os.listdir(cwd)
            top = repo_root(cwd)
        except OSError:
            pass
        if top and top not in repos:
            repos[top] = git_status(top)
        g = repos.get(top) or {}
        unpushed = None
        if top:
            rc, remotes = git(top, "remote")
            if rc == 0 and remotes.strip():
                rc, count = git(top, "rev-list", "--count", "HEAD", "--not",
                                "--remotes")
                unpushed = int((count or "0").strip() or 0) if rc == 0 else None
        st = rec.get("status") or {}
        rows.append({
            "pane": pane, "label": rec.get("name") or pane,
            "status": rec.get("agent_status"),
            "repo": top.replace(str(Path.home()), "~", 1) if top else None,
            "branch": g.get("branch"), "files": len(g.get("files", [])),
            "unpushed": unpushed, "upstream": g.get("upstream"),
            "ctx": st.get("context_pct"), "shared_repo": False,
        })
    counts: dict = {}
    for r in rows:
        counts[r["repo"]] = counts.get(r["repo"], 0) + 1
    for r in rows:
        r["shared_repo"] = bool(r["repo"]) and counts[r["repo"]] > 1
    return {"ok": True, "rows": rows}


def push_info(cwd: str) -> dict:
    """What an agent asking to push would send: the commits and the diff."""
    top = repo_root(cwd or "")
    if not top:
        return {"ok": False, "error": "no git repository for this agent"}
    g = git_status(top)
    if g.get("upstream"):
        base = "@{u}"
        rng = "@{u}..HEAD"
    else:
        rc, first = git(top, "rev-list", "--reverse", "HEAD", "--not", "--remotes")
        first = first.split()[0] if first.split() else None
        if not first:
            base, rng = None, None
        else:
            base, rng = f"{first}^", f"{first}^..HEAD"
    if base and rng:
        rc, log = git(top, "log", "--format=%h%x1f%s%x1f%an%x1f%cI", "-50", rng)
        rc2, stat = git(top, "diff", "--stat", f"{base}..HEAD")
        rc3, diff = git(top, "diff", f"{base}..HEAD", timeout=10)
    else:
        log, stat, diff = "", "", ""
    commits = [dict(zip(("short", "subject", "author", "when"),
                        l.split("\x1f")))
               for l in log.splitlines() if l.count("\x1f") == 3]
    return {"ok": True, "repo": top.replace(str(Path.home()), "~", 1),
            "branch": g.get("branch"), "upstream": g.get("upstream"),
            "commits": commits, "stat": stat[-4000:], "diff": diff[:400_000],
            "truncated": len(diff) > 400_000,
            "uncommitted": len(g.get("files", []))}


def _epoch(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        from datetime import datetime, timezone
        s = ts[:19].replace("Z", "")
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0.0


def digest(agents: dict, chat_for, since: float) -> dict:
    """While you were away: what each agent did since a moment."""
    out = []
    for pane, rec in agents.items():
        r = chat_for(pane)
        items = [i for i in (r.get("items") or [])
                 if _epoch(i.get("ts")) >= since]
        said = [i for i in items if i.get("role") == "assistant" and i.get("text")]
        cwd = rec.get("cwd") or ""
        commits: list[str] = []
        top = repo_root(cwd) if cwd else None
        if top:
            rc, log = git(top, "log", f"--since=@{int(since)}",
                          "--format=%h %s", "-8")
            commits = log.splitlines() if rc == 0 else []
        st = rec.get("status") or {}
        out.append({
            "pane": pane, "label": rec.get("name") or pane,
            "status": rec.get("agent_status"),
            "prompts": sum(1 for i in items if i.get("role") in ("user", "command")),
            "replies": len(said),
            "tools": sum(1 for i in items if i.get("role") == "tools"),
            "last": (said[-1]["text"][:320] if said else None),
            "commits": commits, "cost": st.get("cost"),
            "asking": (rec.get("ask") or {}).get("question"),
            "needs_user": bool(rec.get("needs_user")),
        })
    # anything waiting on the user goes first, then busiest.
    out.sort(key=lambda r: (not r["needs_user"],
                            -(r["prompts"] + r["replies"] + len(r["commits"])),
                            r["label"]))
    return {"ok": True, "since": since, "agents": out}
