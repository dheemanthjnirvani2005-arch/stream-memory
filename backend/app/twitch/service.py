import asyncio
import json
import logging
import re
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)
TWITCH_ID = "https://id.twitch.tv/oauth2"
HELIX = "https://api.twitch.tv/helix"
REQUIRED_SCOPES = {"user:read:chat", "user:write:chat"}


class TwitchError(RuntimeError):
    pass


class TwitchAuthenticationError(TwitchError):
    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        self.status_code = status_code
        self.error_code = error_code[:80]
        self.provider_message = " ".join(message.split())[:240]
        super().__init__(f"Twitch authentication failed ({status_code}, {self.error_code}): "
                         f"{self.provider_message}")


def _response_error(response: httpx.Response) -> tuple[str, str]:
    """Extract only short, non-header error fields from a Twitch response."""
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        return "unknown_error", "Twitch returned an error response"
    if not isinstance(body, dict):
        return "unknown_error", "Twitch returned an error response"
    code = body.get("error", body.get("status", "unknown_error"))
    message = body.get("message", "Twitch rejected the request")
    return str(code), str(message)


def _sanitized_response_error(response: httpx.Response, *sensitive_values: str) -> tuple[str, str]:
    code, message = _response_error(response)
    for value in sensitive_values:
        if value:
            code = code.replace(value, "[redacted]")
            message = message.replace(value, "[redacted]")
    code = re.sub(r"(?i)\b(bearer|oauth)\s+\S+", r"\1 [redacted]", code)
    safe_code = re.sub(r"[^A-Za-z0-9_-]", "_", code)[:60] or "unknown_error"
    message = re.sub(r"(?i)\b(bearer|oauth)\s+\S+", r"\1 [redacted]", message)
    safe_message = " ".join(message.split())[:160] or "Twitch returned an error response"
    return safe_code, safe_message


class TwitchService:
    def __init__(self, settings: Settings | None = None,
                 client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client or httpx.AsyncClient(timeout=15)

    def authorization_url(self, state: str) -> str:
        params = {"client_id": self.settings.twitch_client_id,
                  "redirect_uri": self.settings.twitch_redirect_uri,
                  "response_type": "code", "scope": self.settings.twitch_scopes,
                  "state": state}
        return f"{TWITCH_ID}/authorize?{urlencode(params)}"

    async def _post_token(self, data: dict[str, str]) -> dict[str, Any]:
        response = await self.client.post(f"{TWITCH_ID}/token", data=data)
        response.raise_for_status()
        return response.json()

    async def exchange_code(self, code: str) -> dict[str, Any]:
        return await self._post_token({"client_id": self.settings.twitch_client_id,
            "client_secret": self.settings.twitch_client_secret.get_secret_value(),
            "code": code, "grant_type": "authorization_code",
            "redirect_uri": self.settings.twitch_redirect_uri})

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        return await self._post_token({"client_id": self.settings.twitch_client_id,
            "client_secret": self.settings.twitch_client_secret.get_secret_value(),
            "refresh_token": refresh_token, "grant_type": "refresh_token"})

    async def validate_token(self, access_token: str) -> dict[str, Any]:
        response = await self.client.get(f"{TWITCH_ID}/validate",
            headers={"Authorization": f"OAuth {access_token}"})
        if response.status_code == 401:
            code, message = _response_error(response)
            raise TwitchAuthenticationError(401, code, message.replace(access_token, "[redacted]"))
        response.raise_for_status()
        result = response.json()
        if result.get("client_id") != self.settings.twitch_client_id:
            raise TwitchError("Twitch token belongs to a different application")
        if not isinstance(result.get("expires_in"), int) or result["expires_in"] <= 0:
            raise TwitchError("Twitch token validation did not return a valid expiry")
        if not REQUIRED_SCOPES.issubset(set(result.get("scopes", []))):
            raise TwitchError("Twitch authorization is missing required chat scopes")
        return result

    async def broadcaster(self, access_token: str) -> dict[str, str]:
        response = await self.client.get(f"{HELIX}/users", headers=self.headers(access_token))
        response.raise_for_status()
        users = response.json().get("data", [])
        if not users:
            raise TwitchError("Twitch did not return the authorized broadcaster")
        return {"id": users[0]["id"], "login": users[0]["login"],
                "display_name": users[0]["display_name"]}

    def headers(self, access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}",
                "Client-Id": self.settings.twitch_client_id,
                "Content-Type": "application/json"}

    async def subscribe_chat(self, access_token: str, broadcaster_id: str,
                             session_id: str) -> None:
        response = await self.client.post(f"{HELIX}/eventsub/subscriptions",
            headers=self.headers(access_token), json={"type": "channel.chat.message",
                "version": "1", "condition": {"broadcaster_user_id": broadcaster_id,
                "user_id": broadcaster_id}, "transport": {"method": "websocket",
                "session_id": session_id}})
        if response.status_code == 401:
            code, message = _response_error(response)
            raise TwitchAuthenticationError(401, code, message.replace(access_token, "[redacted]"))
        response.raise_for_status()

    async def send_chat_message(self, access_token: str, broadcaster_id: str,
                                message: str) -> dict[str, Any]:
        sender_id = broadcaster_id
        logger.info(
            "lore_trace stage=helix_request result=started endpoint=send_chat_message "
            "user_access_token=true broadcaster_sender_match=%s",
            sender_id == broadcaster_id,
        )
        response = await self.client.post(f"{HELIX}/chat/messages",
            headers=self.headers(access_token), json={"broadcaster_id": broadcaster_id,
                "sender_id": sender_id, "message": message[:500]})
        if not response.is_success:
            error_code, detail = _sanitized_response_error(response, access_token, message)
            logger.warning(
                "lore_trace stage=helix_response result=failed status_code=%d "
                "error_code=%s detail=%s",
                response.status_code, error_code, detail,
            )
            response.raise_for_status()
        try:
            result = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                "lore_trace stage=helix_response result=failed status_code=%d "
                "error_code=invalid_json detail=invalid_response_body",
                response.status_code,
            )
            raise TwitchError("Twitch returned an invalid chat-send response") from exc
        data = result.get("data", []) if isinstance(result, dict) else []
        if not data or not isinstance(data[0], dict) or not isinstance(data[0].get("is_sent"), bool):
            logger.warning(
                "lore_trace stage=helix_response result=failed status_code=%d "
                "error_code=missing_delivery_status detail=is_sent_not_present",
                response.status_code,
            )
            raise TwitchError("Twitch did not confirm that the chat message was sent")
        if data[0]["is_sent"] is not True:
            logger.warning(
                "lore_trace stage=helix_response result=failed status_code=%d "
                "error_code=message_not_sent detail=twitch_reported_is_sent_false",
                response.status_code,
            )
            raise TwitchError("Twitch reported that the chat message was not sent")
        logger.info(
            "lore_trace stage=helix_response result=success status_code=%d "
            "is_sent=true broadcaster_sender_match=%s",
            response.status_code, sender_id == broadcaster_id,
        )
        return result


class EventSubListener:
    def __init__(self, twitch: TwitchService, access_token: str,
                 broadcaster_id: str, on_message: Callable[[dict[str, Any]], Awaitable[None]],
                 status_callback: Callable[[str], None] | None = None,
                 token_provider: Callable[[bool], Awaitable[str]] | None = None) -> None:
        self.twitch, self.access_token, self.broadcaster_id = twitch, access_token, broadcaster_id
        self.on_message = on_message
        self.status_callback = status_callback or (lambda _: None)
        self.token_provider = token_provider
        self.status = "disconnected"
        self.error_message: str | None = None
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._stopping = False
        self._socket: Any = None

    def _set_status(self, value: str) -> None:
        self.status = value
        self.status_callback(value)

    @staticmethod
    def _consume_notification_task(task: asyncio.Task[Any]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            logger.info("memory_trace stage=eventsub_dispatch result=cancelled")
        except Exception as exc:
            logger.error("memory_trace stage=eventsub_dispatch result=failed error_type=%s",
                         type(exc).__name__)

    async def run(self) -> None:
        import websockets

        base_url = "wss://eventsub.wss.twitch.tv/ws"
        url = base_url
        reconnect_handoff = False
        while not self._stopping:
            try:
                async with websockets.connect(url, ping_interval=None, close_timeout=5) as socket:
                    self._socket = socket
                    self._set_status("connecting")
                    async for raw in socket:
                        import json
                        envelope = json.loads(raw)
                        metadata = envelope.get("metadata", {})
                        message_type = metadata.get("message_type")
                        payload = envelope.get("payload", {})
                        if message_type == "session_welcome":
                            session = payload["session"]
                            if reconnect_handoff:
                                reconnect_handoff = False
                            else:
                                self._set_status("authenticating")
                                try:
                                    token = (await self.token_provider(False)
                                             if self.token_provider else self.access_token)
                                except TwitchAuthenticationError:
                                    raise
                                except Exception:
                                    raise TwitchAuthenticationError(
                                        401, "token_unavailable",
                                        "Twitch credentials could not be validated or refreshed; reconnect Twitch",
                                    ) from None
                                try:
                                    await self.twitch.subscribe_chat(
                                        token, self.broadcaster_id, session["id"]
                                    )
                                except TwitchAuthenticationError as exc:
                                    if self.token_provider is None:
                                        raise
                                    logger.warning(
                                        "EventSub subscription rejected authentication "
                                        "(status=%d, code=%s, message=%s); refreshing once",
                                        exc.status_code, exc.error_code, exc.provider_message,
                                    )
                                    try:
                                        token = await self.token_provider(True)
                                    except TwitchAuthenticationError:
                                        raise
                                    except Exception:
                                        raise TwitchAuthenticationError(
                                            401, "token_refresh_failed",
                                            "Twitch token refresh failed; reconnect Twitch",
                                        ) from None
                                    await self.twitch.subscribe_chat(
                                        token, self.broadcaster_id, session["id"]
                                    )
                            self._set_status("connected")
                        elif message_type == "session_keepalive":
                            continue
                        elif message_type == "session_reconnect":
                            url = payload["session"]["reconnect_url"]
                            reconnect_handoff = True
                            break
                        elif message_type == "revocation":
                            self._set_status("revoked")
                            self._stopping = True
                            break
                        elif message_type == "notification":
                            subscription = payload.get("subscription")
                            if not isinstance(subscription, dict):
                                subscription = {}
                            subscription_type = metadata.get("subscription_type") or subscription.get("type")
                            if subscription_type == "channel.chat.message":
                                logger.info(
                                    "memory_trace stage=eventsub_receive result=success type=channel.chat.message"
                                )
                            else:
                                logger.info(
                                    "memory_trace stage=eventsub_receive result=ignored reason=unexpected_type"
                                )
                                continue
                            message_id = metadata.get("message_id")
                            if not isinstance(message_id, str) or not message_id:
                                logger.warning(
                                    "memory_trace stage=eventsub_dispatch result=skipped "
                                    "reason=missing_delivery_message_id"
                                )
                                continue
                            now = time.monotonic()
                            self._seen = OrderedDict((key, seen) for key, seen in self._seen.items()
                                if now - seen < 600)
                            if message_id and message_id not in self._seen:
                                self._seen[message_id] = now
                                notification = {
                                    **envelope,
                                    "_connected_broadcaster_id": self.broadcaster_id,
                                }
                                task = asyncio.create_task(self.on_message(notification))
                                task.add_done_callback(self._consume_notification_task)
                                logger.info(
                                    "memory_trace stage=eventsub_dispatch result=scheduled "
                                    "broadcaster_match=true"
                                )
                            elif message_id:
                                logger.info(
                                    "memory_trace stage=eventsub_dedup result=ignored reason=duplicate"
                                )
            except asyncio.CancelledError:
                raise
            except TwitchAuthenticationError as exc:
                self._set_status("authentication_error")
                self.error_message = (
                    "Twitch rejected EventSub authentication after a token refresh. "
                    "Reconnect Twitch to restore live chat."
                )
                logger.error(
                    "EventSub authentication failed after one attempt to refresh "
                    "(status=%d, code=%s, message=%s)",
                    exc.status_code, exc.error_code, exc.provider_message,
                )
                self._stopping = True
            except Exception:
                logger.exception("EventSub connection failed")
                self._set_status("reconnecting")
                await asyncio.sleep(2)
                url = base_url
                reconnect_handoff = False
        if self.status not in {"revoked", "authentication_error"}:
            self._set_status("disconnected")
        self._socket = None

    def stop(self) -> None:
        self._stopping = True
        if self._socket is not None:
            asyncio.create_task(self._socket.close())
