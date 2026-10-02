from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from openai_codex import AsyncCodex
from openai_codex.api import AsyncThread

from app.config import settings
from app.services.codex import (
    CodexModelUnavailable,
    CodexNeedsLogin,
    CodexPreferenceError,
    CodexService,
)


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


def test_validate_choice_keeps_one_off_analysis_preferences_out_of_saved_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "codex_home", str(tmp_path))
    monkeypatch.setattr(settings, "codex_model", "gpt-6-luna")
    monkeypatch.setattr(settings, "codex_reasoning_effort", "medium")
    service = CodexService()

    async def fake_status(*, refresh: bool):
        assert refresh is True
        return {
            "authenticated": True,
            "models": [{
                "id": "gpt-6.1-sol",
                "reasoning_efforts": [{"value": "high"}],
            }],
        }

    monkeypatch.setattr(service, "status", fake_status)

    result = asyncio.run(service.validate_choice("GPT-6.1-Sol", "HIGH"))

    assert result == ("gpt-6.1-sol", "high")
    assert settings.codex_model == "gpt-6-luna"
    assert settings.codex_reasoning_effort == "medium"
    assert not service._preferences_file.exists()


def test_validate_choice_rejects_an_unavailable_model_without_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "codex_home", str(tmp_path))
    service = CodexService()

    async def fake_status(*, refresh: bool):
        return {
            "authenticated": True,
            "models": [{"id": "gpt-6-luna", "reasoning_efforts": [{"value": "medium"}]}],
        }

    monkeypatch.setattr(service, "status", fake_status)

    with pytest.raises(CodexModelUnavailable, match="недоступны"):
        asyncio.run(service.validate_choice("gpt-6.1-sol", "high"))


class _FakeStreamTurn:
    def stream(self):
        async def notifications():
            yield SimpleNamespace(method="item/agentMessage/delta", payload=SimpleNamespace(delta='{"answer":'))
            yield SimpleNamespace(
                method="turn/completed",
                payload=SimpleNamespace(turn=SimpleNamespace(error=None)),
            )

        return notifications()


class _FakeStreamThread:
    id = "thread-structured-test"

    def __init__(self):
        self.turn_input = None
        self.turn_options = None

    async def turn(self, message, **options):
        self.turn_input = message
        self.turn_options = options
        return _FakeStreamTurn()


class _FakeStreamClient:
    def __init__(self):
        self.started_with = None
        self.thread = _FakeStreamThread()

    async def thread_start(self, **options):
        self.started_with = options
        return self.thread


def test_pinned_codex_sdk_supports_structured_stream_and_ephemeral_thread() -> None:
    assert "ephemeral" in inspect.signature(AsyncCodex.thread_start).parameters
    assert "output_schema" in inspect.signature(AsyncThread.turn).parameters
    assert "output_schema" in inspect.signature(AsyncThread.run).parameters


class _FakeCompletionThread:
    async def run(self, message, **options):
        self.input = message
        self.options = options
        return SimpleNamespace(error=None, final_response='{"answer":"ok"}')


class _FakeCompletionClient:
    def __init__(self):
        self.started_with = None
        self.thread = _FakeCompletionThread()

    async def thread_start(self, **options):
        self.started_with = options
        return self.thread


def test_complete_uses_explicit_ephemeral_structured_read_only_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    service = CodexService()
    client = _FakeCompletionClient()
    service.client = client
    schema = {"type": "object", "required": ["answer"], "additionalProperties": False}

    async def fake_status(*, refresh: bool):
        assert refresh is True
        return {
            "authenticated": True,
            "models": [{"id": "gpt-6-luna", "reasoning_efforts": [{"value": "medium"}]}],
        }

    monkeypatch.setattr(service, "status", fake_status)

    result = asyncio.run(service.complete(
        {"user_question": "Synthetic question only"}, schema, model="gpt-6-luna", reasoning_effort="medium",
    ))

    assert result == '{"answer":"ok"}'
    assert client.started_with["ephemeral"] is True
    assert client.started_with["approval_mode"].value == "deny_all"
    assert client.started_with["sandbox"].value == "read-only"
    assert client.thread.options["output_schema"] == schema
    assert client.thread.options["approval_mode"].value == "deny_all"
    assert client.thread.options["sandbox"].value == "read-only"


def test_stream_chat_forwards_structured_schema_in_a_new_ephemeral_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    service = CodexService()
    client = _FakeStreamClient()
    service.client = client

    async def ready():
        return None

    monkeypatch.setattr(service, "require_ready", ready)
    schema = {"type": "object", "required": ["answer"], "additionalProperties": False}
    instructions = "Trusted application help instructions."

    async def collect():
        return [item async for item in service.stream_chat(
            {"user_question": "Как найти модель?"},
            output_schema=schema,
            base_instructions=instructions,
            ephemeral=True,
        )]

    events = asyncio.run(collect())

    assert events[0] == {"kind": "thread", "thread_id": "thread-structured-test"}
    assert events[1] == {"kind": "delta", "text": '{"answer":'}
    assert client.started_with["ephemeral"] is True
    assert client.started_with["base_instructions"] == instructions
    assert client.thread.turn_options["output_schema"] == schema
    assert client.thread.turn_options["approval_mode"].value == "deny_all"


def test_stream_chat_defaults_preserve_existing_persistent_chat_options(monkeypatch: pytest.MonkeyPatch) -> None:
    service = CodexService()
    client = _FakeStreamClient()
    service.client = client

    async def ready():
        return None

    monkeypatch.setattr(service, "require_ready", ready)

    async def collect():
        return [item async for item in service.stream_chat({"question": "Existing chat question"})]

    events = asyncio.run(collect())

    assert events[0] == {"kind": "thread", "thread_id": "thread-structured-test"}
    assert "ephemeral" not in client.started_with
    assert client.started_with["base_instructions"]
    assert "output_schema" not in client.thread.turn_options


def test_stream_chat_does_not_claim_ephemeral_for_a_resumed_thread() -> None:
    service = CodexService()

    async def collect():
        return [item async for item in service.stream_chat(
            {"user_question": "Вопрос"}, "persisted-thread", ephemeral=True,
        )]

    with pytest.raises(ValueError, match="только для нового"):
        asyncio.run(collect())
