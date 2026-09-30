from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services.codex import CodexNeedsLogin, CodexPreferenceError, CodexService


class _FakeClient:
    def __init__(self, models):
        self._models = models

    async def account(self):
        return SimpleNamespace(account=SimpleNamespace(type="ChatGPT"))

    async def models(self):
        return SimpleNamespace(data=self._models)


def _catalog_model(model: str = "gpt-6-luna") -> SimpleNamespace:
    return SimpleNamespace(
        model=model,
        display_name="GPT-6 Luna",
        description="Fast model",
        supported_reasoning_efforts=[
            SimpleNamespace(reasoning_effort="low", description="Быстро"),
            SimpleNamespace(reasoning_effort="medium", description="Баланс"),
        ],
    )


def test_status_reports_authenticated_catalog_and_selected_capabilities(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "codex_home", str(tmp_path))
    monkeypatch.setattr(settings, "codex_model", "gpt-6-luna")
    monkeypatch.setattr(settings, "codex_reasoning_effort", "medium")
    service = CodexService()
    service.client = _FakeClient([_catalog_model()])

    state = asyncio.run(service.status(refresh=True))

    assert state["authenticated"] is True
    assert state["model_available"] is True
    assert state["reasoning_available"] is True
    assert state["models"][0]["id"] == "gpt-6-luna"
    assert state["models"][0]["reasoning_efforts"][1]["value"] == "medium"


def test_status_explains_when_saved_model_is_not_in_account_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "codex_home", str(tmp_path))
    monkeypatch.setattr(settings, "codex_model", "gpt-6-luna")
    monkeypatch.setattr(settings, "codex_reasoning_effort", "medium")
    service = CodexService()
    service.client = _FakeClient([_catalog_model("gpt-6-astra")])

    state = asyncio.run(service.status(refresh=True))

    assert state["authenticated"] is True
    assert state["model_available"] is False
    assert state["reasoning_available"] is False
    assert "недоступна" in state["error"]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("unauthorized", "Сессия Codex не авторизована"),
        ("model_not_found", "недоступна для этого аккаунта"),
        ("rate limit exceeded", "Достигнут лимит Codex"),
        ("connection timeout", "Не удалось связаться с Codex"),
        ("", "Не удалось получить ответ от Codex"),
    ],
)
def test_friendly_error_maps_provider_failures(message: str, expected: str) -> None:
    assert expected in CodexService._friendly_error(message)


def test_set_preferences_validates_catalog_and_persists_selected_values(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "codex_home", str(tmp_path))
    monkeypatch.setattr(settings, "codex_model", "gpt-6-luna")
    monkeypatch.setattr(settings, "codex_reasoning_effort", "medium")
    service = CodexService()
    state = {
        "authenticated": True,
        "models": [{"id": "gpt-6.1-sol", "reasoning_efforts": [{"value": "high"}]}],
    }

    async def fake_status(*, refresh: bool):
        assert refresh is True
        return state

    monkeypatch.setattr(service, "status", fake_status)

    result = asyncio.run(service.set_preferences("GPT-6.1-Sol", "HIGH"))

    assert result is state
    assert settings.codex_model == "gpt-6.1-sol"
    assert settings.codex_reasoning_effort == "high"
    assert service._preferences_file.read_text(encoding="utf-8")


def test_set_preferences_requires_login_before_catalog_validation(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "codex_home", str(tmp_path))
    service = CodexService()

    async def fake_status(*, refresh: bool):
        return {"authenticated": False, "models": []}

    monkeypatch.setattr(service, "status", fake_status)

    with pytest.raises(CodexNeedsLogin):
        asyncio.run(service.set_preferences("gpt-6-luna", "medium"))


def test_set_preferences_rejects_unsupported_reasoning(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "codex_home", str(tmp_path))
    service = CodexService()

    async def fake_status(*, refresh: bool):
        return {
            "authenticated": True,
            "models": [{"id": "gpt-6-luna", "reasoning_efforts": [{"value": "low"}]}],
        }

    monkeypatch.setattr(service, "status", fake_status)

    with pytest.raises(CodexPreferenceError, match="не поддерживается"):
        asyncio.run(service.set_preferences("gpt-6-luna", "medium"))


def test_set_preferences_rejects_models_outside_product_allowlist(tmp_path) -> None:
    service = CodexService()
    service._preferences_file = tmp_path / "preferences.json"

    with pytest.raises(CodexPreferenceError, match="поддерживаемый список"):
        asyncio.run(service.set_preferences("gpt-6-astra", "medium"))
