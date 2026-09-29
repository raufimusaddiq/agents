"""Harness-agnostic adapter interface.

Nothing outside adapters/ may branch on the harness name. Each harness is one
module exposing a subclass of Adapter. The server talks only to this interface.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    """One normalized transcript event."""

    kind: str  # prompt|reply|tool|edit|bash|subagent_start|subagent_end|question|answer
    ts: str = ""
    text: str = ""
    path: str = ""          # edit
    command: str = ""       # bash
    tool: str = ""          # tool name
    subagent_type: str = ""  # subagent
    background: bool = False
    isolation: str = ""
    status: str = ""        # subagent_end status
    tool_use_id: str = ""
    ask: dict | None = None
    answer: str = ""
    model: str = ""

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class Option:
    number: int | None
    label: str
    description: str = ""
    selected: bool = False
    checked: bool = False
    kind: str = "choice"  # choice|text
    preview: str = ""


@dataclass
class Ask:
    """A question or permission prompt currently on screen."""

    question: str = ""
    options: list[Option] = field(default_factory=list)
    multi: bool = False
    submit_row: str = ""     # e.g. "Submit"
    tabs: list[str] = field(default_factory=list)
    context: str = ""
    kind: str = ""           # question|permission|trust|plan
    raw: str = ""

    def to_json(self) -> dict:
        d = dataclasses.asdict(self)
        return d


@dataclass
class Step:
    """One answering action: key(s) or text, then short wait."""

    keys: list[str] = field(default_factory=list)
    text: str = ""
    wait: float = 0.15
    note: str = ""

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class MenuItem:
    """One row of a composer completion menu (`/`, `@`, `$`)."""

    trigger: str        # "/" | "@" | "$"
    label: str          # e.g. "/model" or "GitHub"
    detail: str = ""    # description shown to the right
    kind: str = ""      # "command" | "file" | "skill" | "app" | "agent" | ""
    selected: bool = False

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


class Adapter:
    kind = "base"
    # What this harness reports. UI shows "not reported by <harness>" for gaps.
    supports: dict[str, bool] = {
        "transcript": False,
        "prompts": False,
        "status_line": False,
        "subagents": False,
        "resume": False,
        "questions": False,
    }
    # One-line measured notes, filled in by subclasses.
    notes: list[str] = []

    def events(self, session_id: str, state: Any = None) -> list[Event]:
        """Incremental transcript events since the last read."""
        raise NotImplementedError

    def parse_prompt(self, screen_lines: list[str]) -> Ask | None:
        """Parse a question/permission prompt from screen lines, or None."""
        raise NotImplementedError

    def plan_answer(self, ask: Ask, answer: Any) -> list[Step]:
        """Keys/text that give that answer. Never guess."""
        raise NotImplementedError

    def resume_args(self, session_id: str) -> list[str]:
        raise NotImplementedError

    def status_line(self, screen_lines: list[str]) -> dict:
        return {}

    def parse_menu(self, screen_lines: list[str]) -> list[MenuItem]:
        """Parse an open composer completion menu (`/`, `@`, `$`), or []."""
        return []


class ScreenFallbackMixin:
    """Always-available fallback: raw keys, digits, Enter, Esc, arrows."""

    RAW_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9",
                "enter", "esc", "up", "down", "left", "right", "tab", "space"]

    def plan_answer(self, ask: Ask, answer: Any) -> list[Step]:
        # Never guess: when the prompt did not parse, echo raw keys only.
        if isinstance(answer, list):
            return [Step(keys=[str(k) for k in answer], note="raw") for _ in [0]]
        return [Step(keys=[str(answer)], note="raw")]
