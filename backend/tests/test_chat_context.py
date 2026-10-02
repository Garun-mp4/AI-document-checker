from types import SimpleNamespace

from app.services.chat_context import bounded_history


def _message(role: str, content: str, *, status: str = "complete", id: int = 1):
    return SimpleNamespace(id=id, role=role, content=content, generation_status=status)


def test_context_excludes_streaming_and_interrupted_assistant_messages():
    messages = [
        _message("user", "Вопрос 1", id=1),
        _message("assistant", "Ответ 1", id=2),
        _message("user", "Вопрос 2", id=3),
        _message("assistant", "Частичный ответ", status="interrupted", id=4),
    ]

    assert bounded_history(messages) == [
        {"role": "user", "text": "Вопрос 1"},
        {"role": "assistant", "text": "Ответ 1"},
        {"role": "user", "text": "Вопрос 2"},
    ]


def test_context_excludes_current_question_and_keeps_newest_within_budget():
    messages = [_message("user", "старый вопрос", id=1),
                _message("assistant", "старый ответ", id=2),
                _message("user", "текущий вопрос", id=3)]

    assert bounded_history(messages, exclude_id=3, max_chars=8) == [
        {"role": "assistant", "text": "старый о"},
    ]


def test_context_caps_message_count_and_preserves_order():
    messages = [_message("user", str(index), id=index) for index in range(6)]

    assert bounded_history(messages, max_messages=3) == [
        {"role": "user", "text": "3"},
        {"role": "user", "text": "4"},
        {"role": "user", "text": "5"},
    ]
