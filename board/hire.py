"""Agent Board hire implementation."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from adapters import capability_table, get as get_adapter

ROOT = Path(__file__).resolve().parent.parent
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
from .security import (
    hash_password,
    verify_password,
    Sessions,
    SESSIONS,
    _secrets_lock,
    _save_machine_tokens,
    _load_machine_tokens,
    origin_allowed,
    _audit_lock,
    audit,
)
from .worktrees import (
    list_worktrees,
    _repos_from_herdr,
    _herdr_worktrees,
    _git_worktrees,
    _clean_branch,
    YOLO_ARGS,
    yolo_args,
)
import board.core as _core
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
    with _core.STATE.lock:
        rec = _core.STATE.agents.setdefault(pane, {"pane": pane})
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


def worktree_removal_blocker(path: str) -> str:
    """Require successful Git checks before removing a checkout."""
    rc, dirty = git(path, "status", "--porcelain")
    if rc != 0:
        return "cannot verify worktree status"
    if dirty.strip():
        return "uncommitted changes"
    rc, remotes = git(path, "remote")
    if rc != 0:
        return "cannot verify remotes"
    if remotes.strip():
        rc, count = git(path, "rev-list", "--count", "HEAD", "--not", "--remotes")
        if rc != 0:
            return "cannot verify unpushed commits"
        try:
            if int(count.strip()) > 0:
                return "unpushed commits"
        except ValueError:
            return "cannot verify unpushed commits"
    return ""


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
    reason = worktree_removal_blocker(path)
    if reason:
        return reason
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
    try:
        p = subprocess.run(["git", "-C", main_repo, "worktree", "remove", path],
                           capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise HerdrError("remove_failed", "worktree removal failed") from e
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
    pane = _new_shell_pane("new", "", folder, name)
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
    with _core.STATE.lock:
        for c in _core.STATE.closed:
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
    if not Path(cur).is_relative_to(Path(home)) or not os.path.isdir(cur):
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
