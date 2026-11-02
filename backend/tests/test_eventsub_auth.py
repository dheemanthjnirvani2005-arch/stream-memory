from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.twitch import runtime
from app.twitch.service import (
    EventSubListener,
    TwitchAuthenticationError,
    TwitchError,
    TwitchService,
)


SCOPES = ["user:read:chat", "user:write:chat"]


def settings() -> Settings:
    return Settings(twitch_client_id="client-id", twitch_client_secret="client-secret")


def connection(expires_at: datetime) -> dict[str, Any]:
    return {
        "twitch_broadcaster_id": "broadcaster-1",
        "access_token_encrypted": "cipher-access-old",
        "refresh_token_encrypted": "cipher-refresh-old",
        "expires_at": expires_at.isoformat(),
    }


def valid_info(**overrides: Any) -> dict[str, Any]:
    return {
        "client_id": "client-id", "user_id": "broadcaster-1",
        "scopes": SCOPES, "expires_in": 3600, **overrides,
    }


@pytest.mark.asyncio
async def test_unexpired_token_is_validated_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime._refresh_locks.clear()
    monkeypatch.setattr(runtime, "decrypt_token", lambda value: value)
    monkeypatch.setattr(runtime, "settings", SimpleNamespace(twitch_client_id="client-id"))
    validated: list[str] = []

    class Twitch:
        async def validate_token(self, token: str) -> dict[str, Any]:
            validated.append(token)
            return valid_info()

        async def refresh(self, _token: str) -> dict[str, Any]:
            raise AssertionError("valid token should not be refreshed")

    monkeypatch.setattr(runtime, "twitch_service", Twitch())
    access = await runtime.refresh_connection(connection(datetime.now(timezone.utc) + timedelta(minutes=10)))
    assert access == "cipher-access-old"
    assert validated == ["cipher-access-old"]


@pytest.mark.asyncio
async def test_expired_token_refreshes_rotates_and_persists_before_return(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime._refresh_locks.clear()
    monkeypatch.setattr(runtime, "settings", SimpleNamespace(twitch_client_id="client-id"))
    monkeypatch.setattr(runtime, "decrypt_token", lambda value: {
        "cipher-access-old": "access-old", "cipher-refresh-old": "refresh-old"
    }[value])
    monkeypatch.setattr(runtime, "encrypt_token", lambda value: f"encrypted:{value}")
    calls: list[tuple[Any, ...]] = []

    class Twitch:
        async def refresh(self, token: str) -> dict[str, Any]:
            assert token == "refresh-old"
            return {"access_token": "access-new", "refresh_token": "refresh-new", "expires_in": 3600}

        async def validate_token(self, token: str) -> dict[str, Any]:
            assert token == "access-new"
            return valid_info()

    monkeypatch.setattr(runtime, "twitch_service", Twitch())
    monkeypatch.setattr(runtime, "update_twitch_tokens", lambda *args: calls.append(args))
    access = await runtime.refresh_connection(connection(datetime.now(timezone.utc) - timedelta(seconds=1)))
    assert access == "access-new"
    assert calls[0][:4] == (
        "broadcaster-1", "cipher-refresh-old", "encrypted:access-new", "encrypted:refresh-new"
    )
    assert datetime.fromisoformat(calls[0][4]) > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_invalid_token_401_refreshes_even_if_database_expiry_is_future(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime._refresh_locks.clear()
    monkeypatch.setattr(runtime, "settings", SimpleNamespace(twitch_client_id="client-id"))
    monkeypatch.setattr(runtime, "decrypt_token", lambda value: value.removeprefix("cipher-"))
    monkeypatch.setattr(runtime, "encrypt_token", lambda value: f"cipher-{value}")
    saved: list[tuple[Any, ...]] = []

    class Twitch:
        async def validate_token(self, token: str) -> dict[str, Any]:
            if token == "access-old":
                raise TwitchAuthenticationError(401, "Unauthorized", "Token expired")
            return valid_info()

        async def refresh(self, _token: str) -> dict[str, Any]:
            return {"access_token": "access-new", "refresh_token": "refresh-rotated", "expires_in": 3600}

    monkeypatch.setattr(runtime, "twitch_service", Twitch())
    monkeypatch.setattr(runtime, "update_twitch_tokens", lambda *args: saved.append(args))
    token = await runtime.refresh_connection(connection(datetime.now(timezone.utc) + timedelta(hours=2)))
    assert token == "access-new"
    assert saved and saved[0][3] == "cipher-refresh-rotated"


@pytest.mark.asyncio
async def test_refresh_failure_does_not_persist_or_return_stale_token(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime._refresh_locks.clear()
    monkeypatch.setattr(runtime, "settings", SimpleNamespace(twitch_client_id="client-id"))
    monkeypatch.setattr(runtime, "decrypt_token", lambda value: value)
    saves: list[tuple[Any, ...]] = []

    class Twitch:
        async def validate_token(self, _token: str) -> dict[str, Any]:
            raise TwitchAuthenticationError(401, "Unauthorized", "Invalid token")

        async def refresh(self, _token: str) -> dict[str, Any]:
            raise RuntimeError("refresh rejected")

    monkeypatch.setattr(runtime, "twitch_service", Twitch())
    monkeypatch.setattr(runtime, "update_twitch_tokens", lambda *args: saves.append(args))
    with pytest.raises(RuntimeError, match="refresh rejected"):
        await runtime.refresh_connection(connection(datetime.now(timezone.utc) + timedelta(hours=1)))
    assert saves == []


@pytest.mark.asyncio
async def test_token_identity_must_match_broadcaster(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime._refresh_locks.clear()
    monkeypatch.setattr(runtime, "settings", SimpleNamespace(twitch_client_id="client-id"))
    monkeypatch.setattr(runtime, "decrypt_token", lambda value: value)

    class Twitch:
        async def validate_token(self, _token: str) -> dict[str, Any]:
            return valid_info(user_id="another-user")

    monkeypatch.setattr(runtime, "twitch_service", Twitch())
    with pytest.raises(TwitchError, match="does not match"):
        await runtime.refresh_connection(connection(datetime.now(timezone.utc) + timedelta(hours=1)))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_id", "scopes", "message"),
    [("different-client", SCOPES, "different application"),
     ("client-id", ["user:write:chat"], "missing required")],
)
async def test_validation_rejects_client_mismatch_and_missing_read_scope(
    client_id: str, scopes: list[str], message: str
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "client_id": client_id, "user_id": "broadcaster-1", "scopes": scopes, "expires_in": 3600,
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TwitchService(settings(), client)
    with pytest.raises(TwitchError, match=message):
        await service.validate_token("not-printed")
    await client.aclose()


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
        import json
        return json.dumps(frame)


def envelope(message_type: str, session_id: str = "session-1") -> dict[str, Any]:
    return {"metadata": {"message_type": message_type}, "payload": {"session": {
        "id": session_id, "reconnect_url": "wss://eventsub.twitch.tv/reconnect"}}}


async def run_listener(monkeypatch: pytest.MonkeyPatch, listener: EventSubListener,
                       sockets: list[FakeSocket], urls: list[str] | None = None) -> None:
    socket_iter = iter(sockets)

    def connect(url: str, **_kwargs: Any) -> FakeSocket:
        if urls is not None:
            urls.append(url)
        return next(socket_iter)

    monkeypatch.setitem(sys.modules, "websockets", SimpleNamespace(connect=connect))
    await listener.run()


@pytest.mark.asyncio
async def test_401_subscription_refreshes_once_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    tokens: list[bool] = []
    provider_values = iter(["latest-valid", "refreshed-valid"])
    class Twitch:
        def __init__(self) -> None:
            self.attempts: list[str] = []
            self.listener: EventSubListener | None = None

        async def subscribe_chat(self, token: str, _broadcaster: str, _session: str) -> None:
            self.attempts.append(token)
            if len(self.attempts) == 1:
                raise TwitchAuthenticationError(401, "Unauthorized", "Token rejected")
            self.listener._stopping = True

    twitch = Twitch()
    async def provider(force: bool) -> str:
        tokens.append(force)
        return next(provider_values)
    listener = EventSubListener(twitch, "stale", "broadcaster-1", lambda _event: asyncio.sleep(0),
                                token_provider=provider)
    twitch.listener = listener
    await run_listener(monkeypatch, listener, [FakeSocket([envelope("session_welcome")])])
    assert twitch.attempts == ["latest-valid", "refreshed-valid"]
    assert tokens == [False, True]
    assert listener.status == "disconnected"


@pytest.mark.asyncio
async def test_401_after_refresh_stops_without_reconnect_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    providers: list[bool] = []
    connections: list[str] = []
    class Twitch:
        async def subscribe_chat(self, _token: str, _broadcaster: str, _session: str) -> None:
            raise TwitchAuthenticationError(401, "Unauthorized", "Token rejected")
    async def provider(force: bool) -> str:
        providers.append(force)
        return "current-token"
    listener = EventSubListener(Twitch(), "stale", "broadcaster-1", lambda _event: asyncio.sleep(0),
                                token_provider=provider)
    await run_listener(monkeypatch, listener,
                       [FakeSocket([envelope("session_welcome")])], connections)
    assert providers == [False, True]
    assert connections == ["wss://eventsub.wss.twitch.tv/ws"]
    assert listener.status == "authentication_error"


@pytest.mark.asyncio
async def test_eventsub_auth_logs_redact_tokens(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    issued = iter(["secret-access-token", "secret-rotated-token"])
    async def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        return httpx.Response(401, json={
            "error": "Unauthorized", "status": 401, "message": f"Rejected token {token}"
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    twitch = TwitchService(settings(), client)
    async def provider(_force: bool) -> str:
        return next(issued)
    listener = EventSubListener(twitch, "", "broadcaster-1", lambda _event: asyncio.sleep(0),
                                token_provider=provider)
    await run_listener(monkeypatch, listener, [FakeSocket([envelope("session_welcome")])])
    assert listener.status == "authentication_error"
    assert "secret-access-token" not in caplog.text
    assert "secret-rotated-token" not in caplog.text
    assert "Rejected token [redacted]" in caplog.text
    await client.aclose()


@pytest.mark.asyncio
async def test_reconnect_handoff_does_not_create_duplicate_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    class Twitch:
        subscriptions = 0
        listener: EventSubListener | None = None
        async def subscribe_chat(self, *_args: Any) -> None:
            self.subscriptions += 1

    twitch = Twitch()
    listener = EventSubListener(twitch, "token", "broadcaster-1", lambda _event: asyncio.sleep(0))
    twitch.listener = listener
    second_socket = FakeSocket([envelope("session_welcome")], lambda _frame: setattr(listener, "_stopping", True))
    urls: list[str] = []
    await run_listener(monkeypatch, listener, [
        FakeSocket([envelope("session_welcome"), envelope("session_reconnect")]), second_socket,
    ], urls)
    assert twitch.subscriptions == 1
    assert urls == ["wss://eventsub.wss.twitch.tv/ws", "wss://eventsub.twitch.tv/reconnect"]

