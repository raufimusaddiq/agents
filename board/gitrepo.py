"""Agent Board gitrepo implementation."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
from .herdr import SHA_RE

def git(repo: str, *args: str, timeout: float = 15.0) -> tuple[int, str]:
    # Measured: always --no-optional-locks so we never take the index lock.
    cmd = ["git", "--no-optional-locks", "-C", repo, *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 1, ""


def repo_root(path: str) -> str | None:
    if not path:
        return None
    # Measured: herdr marks a deleted cwd as "<path> (deleted)".
    if path.endswith(" (deleted)"):
        path = path[:-len(" (deleted)")]
    if not os.path.isdir(path):
        return None
    rc, out = git(path, "rev-parse", "--show-toplevel")
    return out.strip() if rc == 0 and out.strip() else None


def git_status(repo: str) -> dict:
    out: dict = {"repo": repo}
    rc, branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    out["branch"] = branch.strip() if rc == 0 else "?"
    rc, up = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    out["upstream"] = up.strip() if rc == 0 else None
    if out["upstream"]:
        rc, ab = git(repo, "rev-list", "--left-right", "--count",
                     f"{out['upstream']}...HEAD")
        if rc == 0 and ab.strip():
            parts = ab.split()
            out["behind"], out["ahead"] = int(parts[0]), int(parts[1])
    rc, numstat = git(repo, "diff", "HEAD", "--numstat", "-z")
    files = []
    entries = iter(numstat.split("\0"))
    for entry in entries:
        columns = entry.split("\t", 2)
        if len(columns) != 3:
            continue
        added, removed, path = columns
        if not path:
            # Rename/copy records have two separate NUL-terminated paths.
            next(entries, "")  # source
            path = next(entries, "")
        if path:
            files.append({"added": added, "removed": removed, "path": path})
    rc, porcelain = git(repo, "status", "--porcelain=v1", "-z",
                        "--untracked-files=all")
    entries = iter(porcelain.split("\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        code = entry[:2]
        path = entry[3:]
        if "R" in code or "C" in code:
            next(entries, "")  # source path is not a second changed file
        existing = next((file for file in files if file["path"] == path), None)
        if existing:
            existing["status"] = code.strip() or "?"
        else:
            files.append({"added": "0", "removed": "0", "path": path,
                          "status": code.strip() or "?"})
    out["files"] = files[:300]
    out["files_capped"] = len(files) > 300
    rc, commits = git(repo, "log", "-15", "--pretty=%H%x00%h%x00%s%x00%cI%x00%d")
    rc_remotes, remotes_out = git(repo, "remote")
    has_remote = bool(remotes_out.strip())
    clist = []
    for line in commits.splitlines():
        parts = line.split("\0", 4)
        if len(parts) == 5:
            sha, short, subj, date, refs = parts
            # Measured: `branch -r --contains` returns rc 0 even with no
            # remotes, so `pushed` must also require a remote to exist.
            if has_remote:
                rc2, out2 = git(repo, "branch", "-r", "--contains", sha)
                pushed = rc2 == 0 and bool(out2.strip())
            else:
                pushed = False
            clist.append({"sha": sha, "short": short, "subject": subj,
                          "date": date, "pushed": pushed})
    out["commits"] = clist
    rc, count = git(repo, "rev-list", "--count", "HEAD", "--not", "--remotes")
    out["unpushed_commits"] = int(count.strip() or 0) if rc == 0 else None
    out["has_remote"] = has_remote
    return out


def git_diff_file(repo: str, path: str) -> str:
    # Only diff files git already lists as changed.
    st = git_status(repo)
    changed = {f["path"] for f in st.get("files", [])}
    if path not in changed:
        return ""
    rc, diff = git(repo, "diff", "HEAD", "--", path)
    return diff if rc == 0 else ""


def git_diff_commit(repo: str, sha: str) -> str:
    if not SHA_RE.match(sha):
        return ""
    rc, diff = git(repo, "show", "--stat", "--patch", sha)
    return diff if rc == 0 else ""
