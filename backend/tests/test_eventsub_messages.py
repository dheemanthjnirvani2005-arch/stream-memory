from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.memory.buffer import RollingChatBuffer
from app.ai.extraction import MockExtractionProvider
from app.memory.service import MemoryService
from app.routes import memories as memories_route
from app.twitch import runtime
from app.twitch.service import EventSubListener


def notification(
    *, chatter_id: str = "viewer-1", text: str = "hello chat", message_id: str = "chat-msg-1",
    delivery_id: str = "eventsub-delivery-1", fragments: Any = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"text": text}
    if fragments is not None:
        message["fragments"] = fragments
    return {
        "metadata": {
            "message_type": "notification",
            "message_id": delivery_id,
            "subscription_type": "channel.chat.message",
            "message_timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "payload": {
            "subscription": {"type": "channel.chat.message"},
            "event": {
                "broadcaster_user_id": "broadcaster-1",
                "broadcaster_user_login": "streamer",
                "broadcaster_user_name": "Streamer",
                "chatter_user_id": chatter_id,
                "chatter_user_login": chatter_id,
                "chatter_user_name": chatter_id,
                "message_id": message_id,
                "message": message,
            },
        },
        "_connected_broadcaster_id": "broadcaster-1",
    }


@pytest.mark.asyncio
async def test_nested_viewer_notification_parses_fields_and_enters_correct_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_buffer = RollingChatBuffer()
    monkeypatch.setattr(runtime, "buffer", chat_buffer)

    frame = notification(text="We found the hidden room!")
    await runtime.on_chat_message(frame)

    captured = chat_buffer.snapshot("broadcaster-1")
    assert len(captured) == 1
    assert captured[0].message_id == "chat-msg-1"
    assert captured[0].broadcaster_id == "broadcaster-1"
    assert captured[0].chatter_id == "viewer-1"
    assert captured[0].text == "We found the hidden room!"
    assert captured[0].timestamp == datetime.fromisoformat(
        frame["metadata"]["message_timestamp"]
    )


@pytest.mark.asyncio
async def test_broadcaster_remember_is_authorized_and_not_added_to_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_buffer = RollingChatBuffer()
    captures: list[tuple[str, str | None]] = []
    async def capture(broadcaster_id: str, topic: str | None = None) -> dict[str, bool]:
        captures.append((broadcaster_id, topic))
        return {"created": False}
    monkeypatch.setattr(runtime, "buffer", chat_buffer)
    monkeypatch.setattr(runtime, "capture_memory", capture)

    await runtime.on_chat_message(notification(
        chatter_id="broadcaster-1", text="!remember Kevin", message_id="command-1"
    ))
    await asyncio.sleep(0)

    assert captures == [("broadcaster-1", "Kevin")]
    assert chat_buffer.snapshot("broadcaster-1") == []


@pytest.mark.asyncio
async def test_viewer_remember_is_denied_but_viewer_message_is_buffered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_buffer = RollingChatBuffer()
    captures: list[tuple[str, str | None]] = []
    async def capture(broadcaster_id: str, topic: str | None = None) -> dict[str, bool]:
        captures.append((broadcaster_id, topic))
        return {"created": False}
    monkeypatch.setattr(runtime, "buffer", chat_buffer)
    monkeypatch.setattr(runtime, "capture_memory", capture)

    await runtime.on_chat_message(notification(text="!remember Kevin"))

    assert captures == []
    assert [item.message_id for item in chat_buffer.snapshot("broadcaster-1")] == ["chat-msg-1"]


@pytest.mark.asyncio
async def test_missing_optional_fragments_does_not_block_message(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_buffer = RollingChatBuffer()
    monkeypatch.setattr(runtime, "buffer", chat_buffer)
    frame = notification(text="plain text")

    await runtime.on_chat_message(frame)

    assert chat_buffer.snapshot("broadcaster-1")[0].text == "plain text"


@pytest.mark.asyncio
async def test_unexpected_subscription_type_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_buffer = RollingChatBuffer()
    monkeypatch.setattr(runtime, "buffer", chat_buffer)
    frame = notification(text="not a chat.message event")
    frame["metadata"]["subscription_type"] = "channel.follow"
    frame["payload"]["subscription"]["type"] = "channel.follow"

    await runtime.on_chat_message(frame)

    assert chat_buffer.snapshot("broadcaster-1") == []


@pytest.mark.asyncio
async def test_fragments_are_used_safely_when_message_text_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_buffer = RollingChatBuffer()
    monkeypatch.setattr(runtime, "buffer", chat_buffer)
    frame = notification(text="placeholder", fragments=[{"text": "Kevin "}, {"text": "is a chicken"}])
    message = frame["payload"]["event"]["message"]
    message.pop("text")

    await runtime.on_chat_message(frame)

    assert chat_buffer.snapshot("broadcaster-1")[0].text == "Kevin is a chicken"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remove_path", "expected_field"),
    [(("payload", "event", "chatter_user_id"), "payload.event.chatter_user_id"),
     (("payload", "event", "message_id"), "payload.event.message_id"),
     (("payload", "event", "broadcaster_user_id"), "payload.event.broadcaster_user_id"),
     (("payload", "event", "message"), "payload.event.message")],
)
async def test_missing_required_fields_are_logged_without_raising_keyerror(
    monkeypatch: pytest.MonkeyPatch, caplog, remove_path: tuple[str, ...], expected_field: str,
) -> None:
    import logging
    caplog.set_level(logging.WARNING)
    chat_buffer = RollingChatBuffer()
    monkeypatch.setattr(runtime, "buffer", chat_buffer)
    frame = notification(text="private chat content should not be logged")
    target = frame
    for key in remove_path[:-1]:
        target = target[key]
    target.pop(remove_path[-1])

    await runtime.on_chat_message(frame)

    assert chat_buffer.snapshot("broadcaster-1") == []
    assert "stage=message_parse result=failed" in caplog.text
    assert f"missing_field={expected_field}" in caplog.text
    assert "available_keys=" in caplog.text
    assert "private chat content should not be logged" not in caplog.text


@pytest.mark.asyncio
async def test_wrong_connected_broadcaster_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_buffer = RollingChatBuffer()
    monkeypatch.setattr(runtime, "buffer", chat_buffer)
    frame = notification()
    frame["_connected_broadcaster_id"] = "different-broadcaster"

    await runtime.on_chat_message(frame)

    assert chat_buffer.snapshot("broadcaster-1") == []
    assert chat_buffer.snapshot("different-broadcaster") == []


@pytest.mark.asyncio
async def test_nested_event_to_pending_dashboard_pipeline_with_mocked_supabase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_buffer = RollingChatBuffer()
    runtime_buffer = chat_buffer
    monkeypatch.setattr(runtime, "buffer", runtime_buffer)
    monkeypatch.setattr(runtime, "memory_service", MemoryService(runtime_buffer, MockExtractionProvider()))
    monkeypatch.setattr(memories_route, "broadcaster_from_cookie", lambda _session: "broadcaster-1")

    class FakeDatabase:
        community_id = "community-1"
        memories: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        def table(self, name: str) -> "FakeQuery":
            return FakeQuery(self, name)

    class FakeQuery:
        def __init__(self, database: FakeDatabase, table: str) -> None:
            self.database, self.table_name = database, table
            self.operation, self.payload, self.filters = "select", None, []
            self.with_sources = False
        def select(self, columns: str) -> "FakeQuery":
            self.with_sources = "memory_sources" in columns
            return self
        def eq(self, column: str, value: Any) -> "FakeQuery":
            self.filters.append((column, value))
            return self
        def limit(self, _limit: int) -> "FakeQuery":
            return self
        def order(self, *_args: Any, **_kwargs: Any) -> "FakeQuery":
            return self
        def insert(self, payload: Any) -> "FakeQuery":
            self.operation, self.payload = "insert", payload
            return self
        def execute(self) -> SimpleNamespace:
            if self.table_name == "communities":
                return SimpleNamespace(data=[{"id": self.database.community_id}])
            if self.table_name == "memories" and self.operation == "insert":
                row = {"id": "memory-kevin", **self.payload}
                self.database.memories.append(row)
                return SimpleNamespace(data=[row])
            if self.table_name == "memory_sources" and self.operation == "insert":
                rows = self.payload if isinstance(self.payload, list) else [self.payload]
                self.database.sources.extend(rows)
                return SimpleNamespace(data=rows)
            rows = [row for row in self.database.memories if all(
                row.get(column) == value for column, value in self.filters
            )]
            if self.with_sources:
                rows = [{**row, "memory_sources": [source for source in self.database.sources
                         if source["memory_id"] == row["id"]]} for row in rows]
            return SimpleNamespace(data=rows)

    database = FakeDatabase()
    monkeypatch.setattr("app.database.supabase.get_supabase_client", lambda: database)
    await runtime.on_chat_message(notification(text="Kevin is a chicken.", message_id="evidence-1"))
    mascot = notification(
        text="Viewers call him the community mascot.", message_id="evidence-2",
        chatter_id="viewer-2",
    )
    await runtime.on_chat_message(mascot)
    await runtime.on_chat_message(notification(
        chatter_id="broadcaster-1", text="!remember Kevin", message_id="capture-command"
    ))
    for _ in range(5):
        await asyncio.sleep(0)
        if database.memories:
            break

    pending = await memories_route.get_memories(status="pending", session="test-session", q=None)

    assert len(pending) == 1
    assert pending[0]["status"] == "pending"
    assert pending[0]["community_id"] == "community-1"
    assert len(database.sources) == 2
    assert {source["twitch_message_id"] for source in database.sources} == {"evidence-1", "evidence-2"}
    assert {source["source_excerpt"] for source in database.sources} == {
        "Kevin is a chicken.", "Viewers call him the community mascot.",
    }


class FakeSocket:
    def __init__(self, frames: list[dict[str, Any]], after_frame=None) -> None:
        self.frames = iter(frames)
        self.after_frame = after_frame
    async def __aenter__(self) -> "FakeSocket":
        return self
    async def __aexit__(self, *_args: Any) -> None:
        return None
    def __aiter__(self) -> "FakeSocket":
        return self
    async def __anext__(self) -> str:
        try:
            frame = next(self.frames)
        except StopIteration:
            raise StopAsyncIteration
        if self.after_frame:
            self.after_frame(frame)
        return json.dumps(frame)


@pytest.mark.asyncio
async def test_duplicate_eventsub_delivery_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_buffer = RollingChatBuffer()
    monkeypatch.setattr(runtime, "buffer", chat_buffer)
    frames = [
        {"metadata": {"message_type": "session_welcome"},
         "payload": {"session": {"id": "session-1"}}},
        notification(delivery_id="same-delivery"),
        notification(delivery_id="same-delivery"),
    ]
    listener: EventSubListener
    processed: list[str] = []
    async def handle(frame: dict[str, Any]) -> None:
        processed.append(frame["payload"]["event"]["message_id"])
        await runtime.on_chat_message(frame)
    notification_count = 0
    def stop_after_second_notification(frame: dict[str, Any]) -> None:
        nonlocal notification_count
        if frame.get("metadata", {}).get("message_type") == "notification":
            notification_count += 1
            if notification_count == 2:
                listener._stopping = True
    socket = FakeSocket(frames, stop_after_second_notification)
    monkeypatch.setitem(sys.modules, "websockets", SimpleNamespace(connect=lambda *_args, **_kwargs: socket))
    class Twitch:
        async def subscribe_chat(self, *_args: Any) -> None:
            return None
    listener = EventSubListener(Twitch(), "test-access-token", "broadcaster-1", handle)

    await listener.run()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert processed == ["chat-msg-1"]
    assert [item.message_id for item in chat_buffer.snapshot("broadcaster-1")] == ["chat-msg-1"]
