import asyncio
import logging
from datetime import datetime, timezone

import pytest

from app.memory.retrieval import (CooldownGate, GroundedAnswer, LoreService,
                                  MockAnswerProvider, NO_MEMORY_RESPONSE,
                                  answer_provider)
from app.twitch import runtime


def chat_notification(chatter_id: str, text: str, message_id: str) -> dict:
    return {
        "metadata": {"subscription_type": "channel.chat.message",
                     "message_timestamp": datetime.now(timezone.utc).isoformat()},
        "payload": {"subscription": {"type": "channel.chat.message"}, "event": {
            "broadcaster_user_id": "channel", "chatter_user_id": chatter_id,
            "chatter_user_name": chatter_id, "message_id": message_id,
            "message": {"text": text},
        }},
        "_connected_broadcaster_id": "channel",
    }


class FakeAnswerProvider:
    def __init__(self, result: GroundedAnswer) -> None:
        self.result = result
        self.calls = []

    async def answer(self, question, memories):
        self.calls.append((question, memories))
        return self.result


@pytest.mark.asyncio
async def test_successful_retrieval_uses_scoped_approved_evidence(monkeypatch) -> None:
    evidence = [{"title": "Kevin", "description": "The rescued chicken became the mascot.",
                 "sources": [{"excerpt": "Kevin is a legend"}]}]
    calls = []
    monkeypatch.setattr("app.memory.retrieval.supabase.search_approved_memories",
                        lambda broadcaster, query, limit: calls.append((broadcaster, query, limit)) or evidence)
    provider = FakeAnswerProvider(GroundedAnswer(has_answer=True, answer="Kevin is the rescued chicken mascot."))
    result = await LoreService(provider).answer("channel-1", "Who is Kevin?")
    assert result == "Kevin is the rescued chicken mascot."
    assert calls == [("channel-1", "Who is Kevin?", 5)]
    assert provider.calls == [("Who is Kevin?", evidence)]


@pytest.mark.asyncio
async def test_unknown_question_returns_fixed_response_without_ai(monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr("app.memory.retrieval.supabase.search_approved_memories", lambda *args: [])
    provider = FakeAnswerProvider(GroundedAnswer(has_answer=True, answer="invented"))
    assert await LoreService(provider).answer("channel-1", "Unknown fact") == NO_MEMORY_RESPONSE
    assert provider.calls == []
    assert "stage=memory_retrieval result=no_memory reason=no_matching_approved_memory" in caplog.text


@pytest.mark.asyncio
async def test_insufficient_ai_evidence_uses_no_memory_response(monkeypatch) -> None:
    monkeypatch.setattr("app.memory.retrieval.supabase.search_approved_memories", lambda *args: [{"title": "Unrelated"}])
    provider = FakeAnswerProvider(GroundedAnswer(has_answer=False, answer=None))
    assert await LoreService(provider).answer("channel-1", "question") == NO_MEMORY_RESPONSE


@pytest.mark.asyncio
async def test_mock_answer_uses_only_retrieved_approved_memory_evidence() -> None:
    provider = MockAnswerProvider()
    memory = {
        "title": "Kevin, the chicken mascot",
        "description": (
            "Kevin is the community's chicken mascot. The community named him Kevin "
            "during a stream, and he won the chicken race yesterday!"
        ),
        "sources": [{"excerpt": "Chat called Kevin the chicken mascot."}],
    }

    who_answer = await provider.answer("who is Kevin", [memory])
    mascot_answer = await provider.answer("what is our community mascot", [memory])
    unknown_answer = await provider.answer("what was our first tournament prize", [memory])

    assert who_answer.has_answer
    assert who_answer.answer == memory["description"]
    assert mascot_answer.has_answer
    assert mascot_answer.answer == "Kevin is the community's chicken mascot."
    assert not unknown_answer.has_answer
    assert unknown_answer.answer is None


def test_mock_mode_selects_deterministic_answer_provider(monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.memory.retrieval.get_settings",
        lambda: SimpleNamespace(ai_provider="mock", app_env="development"),
    )
    assert isinstance(answer_provider(), MockAnswerProvider)


def test_cooldowns_enforce_user_and_channel_limits() -> None:
    user_gate = CooldownGate(channel_seconds=0, user_seconds=60)
    assert user_gate.allow("channel", "viewer-a", now=100)
    assert not user_gate.allow("channel", "viewer-a", now=120)
    assert user_gate.allow("channel", "viewer-b", now=120)
    channel_gate = CooldownGate(channel_seconds=30, user_seconds=0)
    assert channel_gate.allow("channel", "viewer-a", now=100)
    assert not channel_gate.allow("channel", "viewer-b", now=120)
    assert channel_gate.allow("channel", "viewer-b", now=131)


@pytest.mark.asyncio
async def test_lore_chat_response_is_sent_and_self_messages_ignored(monkeypatch) -> None:
    sent = []
    async def send(broadcaster_id, message):
        sent.append((broadcaster_id, message))
    class FakeLore:
        async def answer(self, broadcaster, question):
            return "Kevin is the community mascot."
    monkeypatch.setattr(runtime, "send_message", send)
    monkeypatch.setattr(runtime, "lore_service", FakeLore())
    monkeypatch.setattr(runtime, "lore_cooldowns", CooldownGate(0, 0))
    await runtime.process_lore_question("channel", "viewer", "Who is Kevin?")
    await runtime.on_chat_message(chat_notification("channel", "!lore loops", "own"))
    await asyncio.sleep(0)
    assert sent == [("channel", "Kevin is the community mascot.")]


@pytest.mark.asyncio
async def test_lore_command_ignores_overlong_question(monkeypatch) -> None:
    tasks = []
    monkeypatch.setattr(runtime, "process_lore_question", lambda *args: tasks.append(args))
    monkeypatch.setattr(runtime, "lore_cooldowns", CooldownGate(0, 0))
    await runtime.on_chat_message(chat_notification("viewer", "!lore " + "x" * 201, "long"))
    assert tasks == []


@pytest.mark.asyncio
async def test_viewer_lore_command_dispatches_without_broadcaster_permission(monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO)
    calls = []

    async def process(broadcaster_id, chatter_id, question):
        calls.append((broadcaster_id, chatter_id, question))

    monkeypatch.setattr(runtime, "process_lore_question", process)
    monkeypatch.setattr(runtime, "lore_cooldowns", CooldownGate(0, 0))
    await runtime.on_chat_message(chat_notification("viewer-1", "!lore who is Kevin", "lore-1"))
    await asyncio.sleep(0)
    assert calls == [("channel", "viewer-1", "who is Kevin")]
    assert "stage=command_dispatch result=recognized command=lore viewer=true" in caplog.text
    assert "who is Kevin" not in caplog.text


@pytest.mark.asyncio
async def test_empty_lore_question_sends_safe_no_memory_response(monkeypatch) -> None:
    sent = []

    async def search(*_args):
        raise AssertionError("empty question must not query the database")

    async def send(broadcaster_id, message):
        sent.append((broadcaster_id, message))

    monkeypatch.setattr("app.memory.retrieval.supabase.search_approved_memories", search)
    monkeypatch.setattr(runtime, "send_message", send)
    monkeypatch.setattr(runtime, "lore_cooldowns", CooldownGate(0, 0))
    await runtime.on_chat_message(chat_notification("viewer-1", "!lore", "empty-lore"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert sent == [("channel", NO_MEMORY_RESPONSE)]


@pytest.mark.asyncio
async def test_lore_reply_uses_connected_broadcaster_access_token(monkeypatch) -> None:
    from app.database import supabase

    calls = []
    monkeypatch.setattr(supabase, "load_twitch_connection", lambda broadcaster: {
        "twitch_broadcaster_id": broadcaster,
    })

    async def refresh(_connection):
        return "mock-user-access-token"

    class FakeTwitch:
        async def send_chat_message(self, access_token, broadcaster_id, message):
            calls.append((access_token, broadcaster_id, message))
            return {"data": [{"is_sent": True}]}

    monkeypatch.setattr(runtime, "refresh_connection", refresh)
    monkeypatch.setattr(runtime, "twitch_service", FakeTwitch())
    await runtime.send_message("channel", "Kevin is the community mascot.")
    assert calls == [("mock-user-access-token", "channel", "Kevin is the community mascot.")]


@pytest.mark.asyncio
async def test_lore_chat_api_failure_is_handled_without_crashing(monkeypatch) -> None:
    class FakeLore:
        async def answer(self, _broadcaster, _question):
            return "Kevin is the community mascot."

    async def failed_send(_broadcaster, _message):
        raise RuntimeError("mock Helix failure")

    monkeypatch.setattr(runtime, "lore_service", FakeLore())
    monkeypatch.setattr(runtime, "send_message", failed_send)
    await runtime.process_lore_question("channel", "viewer-1", "who is Kevin")


@pytest.mark.asyncio
async def test_viewer_lore_pipeline_retrieves_answer_and_dispatches_chat_send(monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO)
    from app.memory import retrieval

    evidence = [{
        "title": "Kevin the chicken mascot",
        "description": "Kevin is the community's chicken mascot.",
        "sources": [{"excerpt": "Chat called Kevin the chicken mascot."}],
    }]
    retrieval_calls = []
    sent = []
    send_completed = asyncio.Event()

    def search(broadcaster_id, question, limit):
        retrieval_calls.append((broadcaster_id, question, limit))
        return evidence

    async def send(broadcaster_id, message):
        sent.append((broadcaster_id, message))
        send_completed.set()
        return {"data": [{"is_sent": True}]}

    monkeypatch.setattr(retrieval.supabase, "search_approved_memories", search)
    monkeypatch.setattr(runtime, "lore_service", LoreService(MockAnswerProvider()))
    monkeypatch.setattr(runtime, "send_message", send)
    monkeypatch.setattr(runtime, "lore_cooldowns", CooldownGate(0, 0))

    await runtime.on_chat_message(
        chat_notification("viewer-2", "!lore who is Kevin", "full-lore-pipeline")
    )
    await asyncio.wait_for(send_completed.wait(), timeout=2)

    assert retrieval_calls == [("channel", "who is Kevin", 5)]
    assert sent == [("channel", "Kevin is the community's chicken mascot.")]
    assert "stage=command_dispatch result=recognized" in caplog.text
    assert "stage=approved_memory_rpc" not in caplog.text  # The database function is mocked here.
    assert "stage=mock_answer result=success" in caplog.text
    assert "stage=chat_send result=success" in caplog.text
    assert "!lore who is Kevin" not in caplog.text


@pytest.mark.asyncio
async def test_broadcaster_remember_command_still_triggers_capture(monkeypatch) -> None:
    calls = []

    async def capture(broadcaster_id, topic):
        calls.append((broadcaster_id, topic))
        return {"created": True}

    monkeypatch.setattr(runtime, "capture_memory", capture)
    await runtime.on_chat_message(chat_notification("channel", "!remember Kevin", "remember-1"))
    await asyncio.sleep(0)
    assert calls == [("channel", "Kevin")]


def test_database_retrieval_rpc_passes_broadcaster_scope(monkeypatch) -> None:
    from app.database import supabase
    captured = {}
    class Client:
        def rpc(self, name, params):
            captured.update(name=name, params=params)
            return self
        def execute(self):
            return type("Result", (), {"data": [{"memory_id": "approved-only-by-rpc"}]})()
    monkeypatch.setattr(supabase, "get_supabase_client", lambda: Client())
    assert supabase.search_approved_memories("broadcaster-42", "lore", 5)
    assert captured == {"name": "search_approved_memories", "params": {
        "p_broadcaster_id": "broadcaster-42", "p_query": "lore", "p_limit": 5}}
