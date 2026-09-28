from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REASONING_LABELS = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "xhigh": "Xhigh",
    "max": "Max",
    "ultra": "Ultra",
}


def model_display_name(model_id: str, display_name: str | None = None) -> str:
    """Return the short model name used in the Russian UI.

    The Codex catalog currently exposes names such as ``GPT-6-Luna`` while
    the product copy uses ``GPT-6 Luna``.  Keep the catalog's human label when
    it is available and only normalize the separator between a version and a
    model family.
    """

    value = (display_name or model_id).strip()
    if not display_name:
        value = re.sub(r"^gpt(?=-)", "GPT", value, flags=re.IGNORECASE)
    value = re.sub(r"(?<=\d)-(?=[A-Za-z])", " ", value)
    if not display_name:
        value = re.sub(r"(?<=\s)([a-z])", lambda match: match.group(1).upper(), value)
    return value or model_id


def reasoning_display_name(value: str) -> str:
    normalized = value.strip().lower()
    return REASONING_LABELS.get(normalized, normalized.capitalize() or "Не указан")


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip().lower()


def catalog_options(entries: Iterable[Any]) -> list[dict[str, Any]]:
    """Convert SDK model records into a small, JSON-safe API shape."""

    options: list[dict[str, Any]] = []
    for entry in entries:
        model_id = str(getattr(entry, "model", None) or getattr(entry, "id", "")).strip()
        if not model_id:
            continue
        reasoning: list[dict[str, str]] = []
        for item in getattr(entry, "supported_reasoning_efforts", None) or []:
            value = _enum_value(getattr(item, "reasoning_effort", None) or getattr(item, "effort", item))
            if not value or any(option["value"] == value for option in reasoning):
                continue
            description = str(getattr(item, "description", "") or "").strip()
            reasoning.append({
                "value": value,
                "label": reasoning_display_name(value),
                "description": description,
            })
        options.append({
            "id": model_id,
            "label": model_display_name(model_id, getattr(entry, "display_name", None)),
            "description": str(getattr(entry, "description", "") or "").strip(),
            "reasoning_efforts": reasoning,
        })
    return options


def preferences_path(codex_home: str | Path) -> Path:
    return Path(codex_home).expanduser() / "document-checker-preferences.json"


def read_preferences(path: Path, defaults: dict[str, str]) -> dict[str, str]:
    """Read preferences without making a corrupt volume fatal to startup."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return dict(defaults)
    if not isinstance(payload, dict):
        return dict(defaults)
    model = payload.get("model")
    effort = payload.get("reasoning_effort")
    result = dict(defaults)
    if isinstance(model, str) and model.strip():
        result["model"] = model.strip()
    if isinstance(effort, str) and effort.strip():
        result["reasoning_effort"] = effort.strip().lower()
    return result


def write_preferences(path: Path, model: str, reasoning_effort: str) -> None:
    """Atomically persist the selected model and reasoning level."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"model": model, "reasoning_effort": reasoning_effort},
        ensure_ascii=False,
        indent=2,
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
