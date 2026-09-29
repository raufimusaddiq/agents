"""Agent Board workflow implementation."""
from __future__ import annotations

import os
import re
import shlex
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
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb", ".java",
            ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".php", ".swift", ".kt",
            ".scala", ".sh", ".sql", ".vue", ".svelte", ".m", ".mm", ".lua",
            ".pl", ".r", ".dart", ".ex", ".exs", ".erl", ".hs", ".clj"}

STATIONS = ["Start check", "Branch", "Code", "Tests", "Review", "Commit",
            "Docs", "Push", "PR"]

def shell_commands(command: str) -> list[list[str]]:
    """Find command positions; quoted source and heredoc text are not executions."""
    lines = (command or "").splitlines()
    clean = []
    delimiter = None
    for line in lines:
        if delimiter:
            if line.strip() == delimiter:
                delimiter = None
            continue
        clean.append(line)
        match = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        if match:
            delimiter = match.group(1)
    try:
        lexer = shlex.shlex("\n".join(clean), posix=True, punctuation_chars=";&|()\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        commands = []
        current = []
        for token in lexer:
            if token and all(char in ";&|()\n" for char in token):
                if current:
                    commands.append(current)
                current = []
            else:
                current.append(token)
        if current:
            commands.append(current)
        return commands
    except ValueError:
        return []


def classify_git(command: str) -> list[str]:
    kinds = []
    for tokens in shell_commands(command):
        # Skip environment assignments and explicit command wrappers.
        while tokens and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]) or tokens[0] in {"env", "command", "then", "do"}):
            tokens = tokens[1:]
        if not tokens:
            continue
        executable = os.path.basename(tokens[0])
        if executable in {"gh", "glab"} and tokens[1:3] in (["pr", "create"], ["mr", "create"]):
            kinds.append("pr")
        if executable != "git":
            continue
        args = tokens[1:]
        while args and args[0].startswith("-"):
            option = args.pop(0)
            if option in {"-C", "-c", "--git-dir", "--work-tree"} and args:
                args.pop(0)
        if not args:
            continue
        action, flags = args[0], args[1:]
        if action in {"status", "commit", "push", "fetch"}:
            kinds.append(action)
        if action == "stash" and flags[:1] == ["list"]:
            kinds.append("stash")
        if action == "rev-list" and "--count" in flags:
            kinds.append("revlist")
        if action == "add" and any(flag in {".", "-A", "--all"} for flag in flags):
            kinds.append("stage_all")
        if action == "commit" and any(flag == "--all" or (flag.startswith("-") and not flag.startswith("--") and "a" in flag) for flag in flags):
            kinds.append("stage_all")
        if action == "show" and any(re.match(r"^[0-9a-f]{7,40}:", flag) for flag in flags):
            kinds.append("show")
    return list(dict.fromkeys(kinds))


def edited_code_files(events: list[dict]) -> set[str]:
    out = set()
    for e in events:
        if e.get("kind") == "edit" and e.get("path"):
            if Path(e["path"]).suffix.lower() in CODE_EXT:
                out.add(e["path"])
    return out


def has_test_action(events: list[dict]) -> bool:
    for event in events:
        if event.get("kind") == "subagent_start" and "test" in event.get("subagent_type", "").lower():
            return True
        if event.get("kind") != "bash":
            continue
        for tokens in shell_commands(event.get("command", "")):
            while tokens and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]) or tokens[0] in {"env", "command"}):
                tokens = tokens[1:]
            if not tokens:
                continue
            executable, args = os.path.basename(tokens[0]), tokens[1:]
            if executable == "pytest":
                return True
            if re.fullmatch(r"python[0-9.]*", executable) and "-m" in args:
                index = args.index("-m")
                if args[index + 1:index + 2] in (["pytest"], ["unittest"]):
                    return True
            if executable in {"npm", "pnpm", "yarn", "bun"}:
                if args[:1] == ["run"]:
                    args = args[1:]
                if args and (args[0] == "test" or args[0].startswith("test:")):
                    return True
            if executable in {"go", "cargo", "dotnet", "playwright"} and args[:1] == ["test"]:
                return True
            if executable == "npx" and args[:2] == ["playwright", "test"]:
                return True
            if executable in {"node", "python", "python3"} and args and re.search(r"(?:^|[/\\])tests?[/\\]", args[0]):
                return True
    return False


def has_review_action(events: list[dict]) -> bool:
    return any(event.get("kind") == "subagent_start" and "review" in event.get("subagent_type", "").lower()
               for event in events)


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
    if repo:
        rc, changed = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        entries = iter(changed.split("\0"))
        for entry in entries if rc == 0 else []:
            if len(entry) >= 4:
                if Path(entry[3:]).suffix.lower() in CODE_EXT:
                    code.add(entry[3:])
                if "R" in entry[:2] or "C" in entry[:2]:
                    next(entries, "")
    if code:
        done.add("Code")
    if has_test_action(events):
        done.add("Tests")
    if has_review_action(events):
        done.add("Review")
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
