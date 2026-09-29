"""Adapter registry. The only place harness names are mapped to classes."""
from __future__ import annotations

from .base import Adapter, Ask, Event, Option, Step
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .opencode import OpencodeAdapter

ADAPTERS: dict[str, Adapter] = {
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "opencode": OpencodeAdapter(),
}


def get(kind: str) -> Adapter | None:
    return ADAPTERS.get(kind)


def capability_table() -> list[dict]:
    rows = []
    for kind, a in ADAPTERS.items():
        rows.append({"kind": kind, "supports": a.supports, "notes": a.notes})
    return rows


__all__ = ["Adapter", "Ask", "Event", "Option", "Step", "ADAPTERS", "get",
           "capability_table"]
