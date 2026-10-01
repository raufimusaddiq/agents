"""Agent Board worktrees implementation."""
from __future__ import annotations

import os
import sys
from pathlib import Path


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
from .workflow import _branch_ticket
def list_worktrees(include_occupied: bool = False) -> list[dict]:
    """Worktrees across agent repos and configured roots.

    Measured 0.9.2: `herdr worktree list [--cwd <repo>]` returns worktrees with
    branch, path, open_workspace_id and is_prunable. We use it first, because it
    maps a worktree to the workspace (and so the agent) that has it open; a raw
    `git worktree list` cannot. `include_occupied` returns every worktree,
    including ones an agent is inside (used to link an agent to its worktree).
    """
    import board.core as _core
    roots = set()
    with _core.STATE.lock:
        for rec in _core.STATE.agents.values():
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
            with _core.STATE.lock:
                occupied = any(os.path.realpath(rec.get("cwd") or "")
                               == path for rec in _core.STATE.agents.values())
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
