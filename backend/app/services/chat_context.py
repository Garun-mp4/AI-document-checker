"""Build a bounded, completed-message-only prompt history for one chat epoch."""

from collections.abc import Iterable
from typing import Any

MAX_HISTORY_MESSAGES = 32
MAX_HISTORY_CHARS = 12_000


def bounded_history(messages: Iterable[Any], *, exclude_id: Any = None,
                    max_messages: int = MAX_HISTORY_MESSAGES,
                    max_chars: int = MAX_HISTORY_CHARS) -> list[dict[str, str]]:
    """Return chronological context, keeping the newest turns within a char budget."""
    eligible = [{"role": item.role, "text": item.content or ""} for item in messages
                if item.id != exclude_id
                and item.role in {"user", "assistant"}
                and (item.role == "user" or getattr(item, "generation_status", "complete") == "complete")]
    eligible = eligible[-max_messages:]
    budget = max(0, max_chars)
    while eligible and sum(len(item["text"]) for item in eligible) > budget:
        overflow = sum(len(item["text"]) for item in eligible) - budget
        oldest = eligible[0]
        content = oldest["text"]
        if len(content) <= overflow:
            eligible.pop(0)
        else:
            oldest["text"] = content[:len(content) - overflow]
            break
    return eligible
