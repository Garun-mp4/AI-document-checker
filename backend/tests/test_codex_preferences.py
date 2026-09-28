from __future__ import annotations

from enum import Enum
from types import SimpleNamespace

from app.services.codex_preferences import (
    catalog_options,
    model_display_name,
    read_preferences,
    reasoning_display_name,
    write_preferences,
)


class FakeReasoning(str, Enum):
    LOW = "low"
    MEDIUM = "medium"


def test_catalog_options_are_json_safe_and_keep_reasoning_metadata() -> None:
    entries = [SimpleNamespace(
        model="gpt-6-luna",
        display_name="GPT-6-Luna",
        description="Fast model",
        supported_reasoning_efforts=[
            SimpleNamespace(reasoning_effort=FakeReasoning.LOW, description="Быстро"),
            SimpleNamespace(reasoning_effort=FakeReasoning.MEDIUM, description="Баланс"),
            SimpleNamespace(reasoning_effort=FakeReasoning.LOW, description="Дубликат"),
        ],
    )]

    assert catalog_options(entries) == [{
        "id": "gpt-6-luna",
        "label": "GPT-6 Luna",
        "description": "Fast model",
        "reasoning_efforts": [
            {"value": "low", "label": "Low", "description": "Быстро"},
            {"value": "medium", "label": "Medium", "description": "Баланс"},
        ],
    }]


def test_display_labels_have_stable_fallbacks() -> None:
    assert model_display_name("gpt-6-astra") == "GPT-6 Astra"
    assert model_display_name("gpt-5.6-sol", "GPT-5.6-Sol") == "GPT-5.6 Sol"
    assert reasoning_display_name("xhigh") == "Xhigh"


def test_preferences_are_written_atomically_and_corrupt_file_falls_back(tmp_path) -> None:
    path = tmp_path / "codex" / "document-checker-preferences.json"
    defaults = {"model": "gpt-6-luna", "reasoning_effort": "medium"}

    write_preferences(path, "gpt-6-sol", "high")
    assert read_preferences(path, defaults) == {"model": "gpt-6-sol", "reasoning_effort": "high"}

    path.write_text("not json", encoding="utf-8")
    assert read_preferences(path, defaults) == defaults
