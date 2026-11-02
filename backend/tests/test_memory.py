from datetime import datetime, timedelta, timezone
import logging
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.ai.extraction import MockExtractionProvider, get_extraction_provider
from app.memory.buffer import RollingChatBuffer
from app.memory.models import ChatMessage, ExtractionResult, MemoryProposal
from app.memory.service import MemoryService
from app.twitch import runtime


def message(message_id: str, broadcaster: str = "channel", chatter: str = "viewer",
            timestamp: datetime | None = None, text: str = "hello") -> ChatMessage:
    return ChatMessage(message_id=message_id, broadcaster_id=broadcaster, chatter_id=chatter,
        display_name="Viewer", text=text, timestamp=timestamp or datetime.now(timezone.utc))


def chat_notification(event_id: str, chatter_id: str, text: str) -> dict:
    return {
        "metadata": {"subscription_type": "channel.chat.message",
                     "message_timestamp": datetime.now(timezone.utc).isoformat()},
        "payload": {"subscription": {"type": "channel.chat.message"}, "event": {
            "broadcaster_user_id": "owner", "chatter_user_id": chatter_id,
            "chatter_user_name": chatter_id, "message_id": event_id,
            "message": {"text": text},
        }},
        "_connected_broadcaster_id": "owner",
    }


def test_buffer_keeps_latest_limit_per_channel() -> None:
    buffer = RollingChatBuffer(max_messages=2)
    buffer.add(message("a", broadcaster="one"))
    buffer.add(message("b", broadcaster="one"))
    buffer.add(message("c", broadcaster="one"))
    buffer.add(message("other", broadcaster="two"))
    assert [item.message_id for item in buffer.snapshot("one")] == ["b", "c"]
    assert [item.message_id for item in buffer.snapshot("two")] == ["other"]


def test_buffer_evicts_messages_older_than_ten_minutes() -> None:
    buffer = RollingChatBuffer()
    now = datetime.now(timezone.utc)
    buffer.add(message("old", timestamp=now - timedelta(minutes=11)))
    buffer.add(message("new", timestamp=now))
    assert [item.message_id for item in buffer.snapshot("channel")] == ["new"]


def test_proposal_validates_category_confidence_and_sources() -> None:
    with pytest.raises(ValueError):
        MemoryProposal(title="T", description="D", category="invalid", confidence=.5,
            source_message_ids=["m"])
    with pytest.raises(ValueError):
        MemoryProposal(title="T", description="D", category="other", confidence=2,
            source_message_ids=["m"])


class FakeProvider:
    def __init__(self, result: ExtractionResult) -> None:
        self.result = result
        self.calls = 0

    async def extract(self, messages, topic):
        self.calls += 1
        return self.result


@pytest.mark.asyncio
async def test_empty_buffer_does_not_call_provider_or_persist(monkeypatch) -> None:
    provider = FakeProvider(ExtractionResult(proposal=None))
    monkeypatch.setattr("app.memory.service.supabase.create_memory_with_sources", lambda *args: pytest.fail("persisted"))
    service = MemoryService(RollingChatBuffer(), provider)
    assert await service.capture("channel") == {"created": False, "reason": "empty_chat_buffer"}
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_empty_ai_result_is_safe(monkeypatch) -> None:
    provider = FakeProvider(ExtractionResult(proposal=None))
    monkeypatch.setattr("app.memory.service.supabase.create_memory_with_sources", lambda *args: pytest.fail("persisted"))
    buffer = RollingChatBuffer()
    buffer.add(message("m1"))
    service = MemoryService(buffer, provider)
    assert await service.capture("channel") == {"created": False, "reason": "no_meaningful_memory"}


@pytest.mark.asyncio
async def test_mock_provider_returns_no_proposal_for_kevin_and_capture_does_not_persist(monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr("app.memory.service.supabase.create_memory_with_sources",
                        lambda *args: pytest.fail("mock no-op must not persist a memory"))
    buffer = RollingChatBuffer()
    buffer.add(message("m1", text="Kevin says hello."))
    result = await MemoryService(buffer, MockExtractionProvider()).capture("channel", "Kevin")
    assert result == {"created": False, "reason": "no_meaningful_memory"}
    assert "stage=mock_provider result=no_proposal" in caplog.text
    assert "Kevin says hello." not in caplog.text


@pytest.mark.asyncio
async def test_mock_provider_empty_buffer_returns_no_proposal() -> None:
    result = await MockExtractionProvider().extract([], "Kevin")
    assert result.proposal is None


@pytest.mark.asyncio
async def test_mock_provider_irrelevant_chat_and_unsupported_topic_return_no_proposal() -> None:
    provider = MockExtractionProvider()
    chat = [message("m1", text="hello everyone"), message("m2", text="what game are we playing?")]
    assert (await provider.extract(chat, "Kevin")).proposal is None
    assert (await provider.extract(chat, None)).proposal is None


@pytest.mark.asyncio
async def test_mock_provider_rejects_negated_claims() -> None:
    messages = [
        message("m1", text="Kevin is not a chicken."),
        message("m2", text="Kevin is not the community mascot."),
    ]
    assert (await MockExtractionProvider().extract(messages, "Kevin")).proposal is None


def kevin_chat() -> list[ChatMessage]:
    return [
        message("m-chicken", text="Kevin is a chicken."),
        message("m-mascot", text="Viewers call him the community mascot."),
        message("m-unrelated", text="What should we play next?"),
    ]


@pytest.mark.asyncio
async def test_mock_provider_builds_grounded_kevin_recurring_character_proposal() -> None:
    messages = kevin_chat()
    result = await MockExtractionProvider().extract(messages, "Kevin")
    proposal = result.proposal
    assert proposal is not None
    assert proposal.category == "recurring_character"
    assert "Kevin" in proposal.title
    assert "chicken" in proposal.description
    assert "community mascot" in proposal.description
    assert set(proposal.source_message_ids) == {"m-chicken", "m-mascot"}
    assert set(proposal.source_message_ids).issubset({item.message_id for item in messages})
    assert "rescue" not in proposal.description.lower()
    assert "winner" not in proposal.description.lower()


@pytest.mark.asyncio
async def test_mock_provider_extracts_only_explicit_event_or_inside_joke_evidence() -> None:
    provider = MockExtractionProvider()
    event = await provider.extract([message("event-1", text="We finally beat the dragon together.")], None)
    assert event.proposal is not None
    assert event.proposal.category == "memorable_moment"
    assert "We finally beat the dragon together." in event.proposal.description
    assert event.proposal.source_message_ids == ["event-1"]

    joke = await provider.extract([message("joke-1", text="Kevin is our running joke in chat.")], "Kevin")
    assert joke.proposal is not None
    assert joke.proposal.category == "inside_joke"
    assert joke.proposal.source_message_ids == ["joke-1"]


def test_mock_provider_is_development_only(monkeypatch) -> None:
    monkeypatch.setattr("app.ai.extraction.get_settings",
                        lambda: SimpleNamespace(ai_provider="mock", app_env="production"))
    with pytest.raises(RuntimeError, match="only in development or test"):
        get_extraction_provider()


@pytest.mark.asyncio
async def test_memory_service_persists_mock_proposal_pending_with_exact_source_excerpts(monkeypatch) -> None:
    messages = kevin_chat()
    buffer = RollingChatBuffer()
    for chat_message in messages:
        buffer.add(chat_message)
    writes: list[tuple[str, object]] = []

    class Query:
        def __init__(self, table: str) -> None:
            self.table = table
            self.operation = "select"
            self.payload = None
        def select(self, _columns):
            return self
        def eq(self, _column, _value):
            return self
        def limit(self, _limit):
            return self
        def insert(self, payload):
            self.operation, self.payload = "insert", payload
            writes.append((self.table, payload))
            return self
        def execute(self):
            if self.table == "communities":
                data = [{"id": "community-1"}]
            elif self.table == "memories":
                data = [{"id": "memory-kevin", **self.payload}]
            else:
                data = self.payload
            return SimpleNamespace(data=data)

    class Supabase:
        def table(self, name):
            return Query(name)

    monkeypatch.setattr("app.database.supabase.get_supabase_client", lambda: Supabase())
    result = await MemoryService(buffer, MockExtractionProvider()).capture("channel", "Kevin")
    assert result["created"] is True
    memory_write = next(payload for table, payload in writes if table == "memories")
    source_write = next(payload for table, payload in writes if table == "memory_sources")
    assert memory_write["community_id"] == "community-1"
    assert memory_write["status"] == "pending"
    assert [source["twitch_message_id"] for source in source_write] == ["m-chicken", "m-mascot"]
    assert [source["source_excerpt"] for source in source_write] == [
        "Kevin is a chicken.", "Viewers call him the community mascot.",
    ]


@pytest.mark.asyncio
async def test_invalid_extraction_result_is_rejected_before_persistence(monkeypatch) -> None:
    class InvalidProvider:
        async def extract(self, messages, topic):
            return SimpleNamespace(model_dump=lambda: {"proposal": {
                "title": "", "description": "unsupported", "category": "other",
                "confidence": 5, "source_message_ids": ["not-in-buffer"], "event_time": None,
            }})

    monkeypatch.setattr("app.memory.service.supabase.create_memory_with_sources",
                        lambda *args: pytest.fail("invalid proposal must not persist"))
    buffer = RollingChatBuffer()
    buffer.add(message("m1", text="ordinary chat"))
    with pytest.raises(ValidationError):
        await MemoryService(buffer, InvalidProvider()).capture("channel")


@pytest.mark.asyncio
async def test_capture_stores_only_verified_evidence_pending(monkeypatch) -> None:
    proposal = MemoryProposal(title="Mascot rescue", description="Chat celebrated the rescue.",
        category="memorable_moment", confidence=.9, source_message_ids=["m1"])
    provider = FakeProvider(ExtractionResult(proposal=proposal))
    saved = {}
    def persist(broadcaster_id, payload, sources):
        saved.update(channel=broadcaster_id, payload=payload, sources=sources)
        return {"id": "memory-1"}
    monkeypatch.setattr("app.memory.service.supabase.create_memory_with_sources", persist)
    buffer = RollingChatBuffer()
    buffer.add(message("m1", text="We rescued Kevin!"))
    result = await MemoryService(buffer, provider).capture("channel", "Kevin")
    assert result["created"] is True
    assert saved["payload"]["status"] == "pending"
    assert saved["sources"][0]["source_excerpt"] == "We rescued Kevin!"


@pytest.mark.asyncio
async def test_broadcaster_only_remember_command(monkeypatch) -> None:
    captures = []
    async def capture(broadcaster_id, topic=None):
        captures.append((broadcaster_id, topic))
        return {"created": False}
    monkeypatch.setattr(runtime, "capture_memory", capture)
    await runtime.on_chat_message(chat_notification("1", "viewer", "!remember Kevin"))
    await runtime.on_chat_message(chat_notification("2", "owner", "!REMEMBER Kevin"))
    await __import__("asyncio").sleep(0)
    assert captures == [("owner", "Kevin")]


def test_database_memory_reads_are_community_scoped_and_approved_for_search(monkeypatch) -> None:
    from app.database import supabase
    calls = []
    class Builder:
        def __init__(self): self.filters = []
        def select(self, value): calls.append(("select", value)); return self
        def eq(self, key, value): calls.append((key, value)); return self
        def limit(self, value): return self
        def execute(self): return type("Result", (), {"data": [{"id": "community"}]})()
        def order(self, *args, **kwargs): calls.append(("order", args, kwargs)); return self
    class Client:
        def table(self, name): calls.append(("table", name)); return Builder()
    monkeypatch.setattr(supabase, "get_supabase_client", lambda: Client())
    result = supabase.list_memories("broadcaster", status="approved")
    assert result == [{"id": "community"}]
    assert ("community_id", "community") in calls
    assert ("status", "approved") in calls
