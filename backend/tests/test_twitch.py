from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.twitch.service import TwitchError, TwitchService


def twitch_settings() -> Settings:
    return Settings(twitch_client_id="client", twitch_client_secret="secret",
                    twitch_redirect_uri="http://localhost:8000/auth/twitch/callback")


def test_login_redirect_sets_state_cookie() -> None:
    response = TestClient(app).get("/auth/twitch/login", follow_redirects=False)
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["scope"] == ["user:read:chat user:write:chat"]
    assert query["state"][0] == response.cookies["twitch_oauth_state"]


def test_callback_rejects_invalid_state() -> None:
    response = TestClient(app).get("/auth/twitch/callback?code=abc&state=wrong")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_validate_requires_user_chat_scopes() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"client_id": "client", "user_id": "123",
            "scopes": ["user:read:chat"], "expires_in": 3600})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TwitchService(twitch_settings(), client)
    with pytest.raises(TwitchError, match="missing required"):
        await service.validate_token("access")
    await client.aclose()


@pytest.mark.asyncio
async def test_eventsub_subscription_uses_broadcaster_user_token_and_identity() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(202, json={"data": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TwitchService(twitch_settings(), client)
    await service.subscribe_chat("USER_ACCESS", "123", "session")
    request = calls[0]
    assert request.headers["Authorization"] == "Bearer USER_ACCESS"
    assert request.headers["Client-Id"] == "client"
    assert request.read() is not None
    await client.aclose()


@pytest.mark.asyncio
async def test_send_chat_uses_connected_broadcaster_as_sender() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"data": [{"is_sent": True}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TwitchService(twitch_settings(), client)
    await service.send_chat_message("USER_ACCESS", "123", "hello")
    payload = calls[0].read().decode()
    assert '"broadcaster_id":"123"' in payload
    assert '"sender_id":"123"' in payload
    assert calls[0].headers["Authorization"] == "Bearer USER_ACCESS"
    await client.aclose()


@pytest.mark.asyncio
async def test_send_chat_rejects_twitch_response_that_did_not_send() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"is_sent": False}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TwitchService(twitch_settings(), client)
    with pytest.raises(TwitchError, match="not sent"):
        await service.send_chat_message("USER_ACCESS", "123", "hello")
    await client.aclose()


@pytest.mark.asyncio
async def test_send_chat_logs_sanitized_helix_error(caplog) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={
            "error": "Unauthorized", "status": 401,
            "message": "Token SECRET_ACCESS rejected message SECRET_CHAT_TEXT",
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TwitchService(twitch_settings(), client)
    with pytest.raises(httpx.HTTPStatusError):
        await service.send_chat_message("SECRET_ACCESS", "123", "SECRET_CHAT_TEXT")
    assert "status_code=401" in caplog.text
    assert "error_code=Unauthorized" in caplog.text
    assert "SECRET_ACCESS" not in caplog.text
    assert "SECRET_CHAT_TEXT" not in caplog.text
    await client.aclose()
