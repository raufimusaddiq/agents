"""Composer suggestions: `/` commands and `@` files.

Ported from the reference workflow-viz implementation. `/` lists the harness's
commands (built-ins plus the user's and the project's `.claude/commands` and
skills). `@` lists the repo's files, or the folder's entries outside a repo.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

BUILTIN_COMMANDS = {
    "add-dir": "Add a working directory", "agents": "Manage subagents",
    "clear": "Clear conversation history", "compact": "Compact the conversation",
    "config": "Open settings", "context": "Show context usage",
    "cost": "Show session cost", "doctor": "Check the installation",
    "exit": "Exit Claude Code", "export": "Export the conversation",
    "help": "Show help", "hooks": "Manage hooks", "init": "Create a CLAUDE.md",
    "mcp": "Manage MCP servers", "memory": "Edit memory files",
    "model": "Switch model", "permissions": "Manage permissions",
    "plugin": "Manage plugins", "resume": "Resume a conversation",
    "review": "Review a pull request", "rewind": "Rewind the conversation",
    "status": "Show status", "usage": "Show plan usage",
    "reload-skills": "Reload skills", "reload-plugins": "Reload plugins",
}

_suggest_cache: dict = {}
_lock = threading.Lock()


def frontmatter(path: Path) -> dict:
    meta = {}
    try:
        text = path.read_text(errors="ignore")[:3000]
    except OSError:
        return meta
    if text.startswith("---"):
        for line in text.split("---", 2)[1].splitlines():
            k, _, v = line.partition(":")
            if k.strip() in ("name", "description") and v.strip():
                meta[k.strip()] = v.strip().strip("\"'")
    return meta


def command_list(cwd: str) -> list[dict]:
    key = ("cmd", cwd)
    with _lock:
        hit = _suggest_cache.get(key)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    out = [{"name": k, "description": v, "source": "built-in"}
           for k, v in BUILTIN_COMMANDS.items()]

    def scan(root, prefix, source):
        root = Path(root)
        for f in sorted(root.glob("commands/**/*.md")):
            name = prefix + ":".join(
                f.relative_to(root / "commands").with_suffix("").parts)
            out.append({"name": name,
                        "description": frontmatter(f).get("description", ""),
                        "source": source})
        for f in sorted(list(root.glob("skills/*/SKILL.md"))
                        + list(root.glob(".claude/skills/*/SKILL.md"))):
            m = frontmatter(f)
            out.append({"name": prefix + (m.get("name") or f.parent.name),
                        "description": m.get("description", ""),
                        "source": source})

    home = Path.home() / ".claude"
    scan(home, "", "personal")
    if cwd and Path(cwd, ".claude").is_dir():
        scan(Path(cwd, ".claude"), "", "project")
    try:
        plugins = json.loads(
            (home / "plugins" / "installed_plugins.json").read_text()
        ).get("plugins", {})
        for key_name, installs in plugins.items():
            for inst in installs[:1]:
                if Path(inst.get("installPath", "")).is_dir():
                    scan(inst["installPath"], key_name.split("@")[0] + ":", "plugin")
    except (OSError, ValueError):
        pass
    for c in out:
        c["description"] = re.sub(r"\s+", " ", c["description"])[:160]
    out = list({c["name"]: c for c in out}.values())
    with _lock:
        _suggest_cache[key] = (time.time(), out)
    return out


def file_list(cwd: str) -> list[str]:
    key = ("files", cwd)
    with _lock:
        hit = _suggest_cache.get(key)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    files: list[str] = []
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "ls-files", "--cached", "--others",
             "--exclude-standard"],
            capture_output=True, text=True, timeout=5)
        files = r.stdout.splitlines() if r.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not files:  # not a repo: the folder's entries, then a shallow walk
        skip = {".git", "node_modules", "target", "dist", "build", ".venv"}
        try:
            files += [e.name + ("/" if e.is_dir() else "")
                      for e in sorted(os.scandir(cwd), key=lambda e: e.name.lower())
                      if not e.name.startswith(".") and e.name not in skip]
        except OSError:
            pass
        for root, dnames, fnames in os.walk(cwd):
            dnames[:] = [d for d in dnames
                         if d not in skip and not d.startswith(".")]
            depth = Path(root).relative_to(cwd).parts
            if len(depth) >= 3:
                dnames[:] = []
            files += [str(Path(root, f).relative_to(cwd)) for f in fnames
                      if not f.startswith(".")]
            if len(files) > 5000:
                break
    dirs = sorted({str(Path(f).parent) + "/" for f in files if "/" in f.rstrip("/")})
    out = list(dict.fromkeys(dirs + files))
    with _lock:
        _suggest_cache[key] = (time.time(), out)
    return out


def rank(items: list, q: str, key=lambda x: x, limit: int = 40) -> list:
    """Prefix first, then substring, then in-order subsequence; shorter wins."""
    q = q.lower()
    scored = []
    for it in items:
        k = key(it).lower()
        base = (k.rsplit("/", 2)[-2]
                if k.endswith("/") and "/" in k[:-1] else k.rsplit("/", 1)[-1])
        if not q:
            s = 3
        elif base.startswith(q) or k.startswith(q):
            s = 0
        elif q in k:
            s = 1
        else:
            it_q = iter(k)
            if not all(c in it_q for c in q):
                continue
            s = 2
        scored.append((s, len(k), k, it))
    return [it for *_, it in sorted(scored)[:limit]]


def suggest(pane_cwd: str, kind: str, q: str) -> list[dict]:
    cwd = pane_cwd or str(Path.home())
    if kind == "command":
        return rank(command_list(cwd), q.lstrip("/"), key=lambda c: c["name"])
    q = q.lstrip("@")
    if q.endswith("/"):  # drilling into a folder: direct children, folders first
        kids = [f for f in file_list(cwd)
                if f.startswith(q) and f != q and "/" not in f[len(q):].rstrip("/")]
        return [{"name": f}
                for f in sorted(kids, key=lambda f: (not f.endswith("/"), f.lower()))[:60]]
    return [{"name": f} for f in rank(file_list(cwd), q)]
