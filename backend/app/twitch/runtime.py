import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database.supabase import load_twitch_connection, update_twitch_tokens
from app.twitch.crypto import decrypt_token, encrypt_token
from app.twitch.service import TwitchAuthenticationError, TwitchError, TwitchService
from app.memory.buffer import RollingChatBuffer
from app.memory.models import ChatMessage
from app.memory.service import MemoryService
from app.memory.retrieval import CooldownGate, LoreService
from app.config import get_settings

logger = logging.getLogger(__name__)
_refresh_locks: dict[str, asyncio.Lock] = {}
twitch_service = TwitchService()
buffer = RollingChatBuffer(max_messages=300)
memory_service = MemoryService(buffer)
lore_service = LoreService()
settings = get_settings()
lore_cooldowns = CooldownGate(settings.lore_channel_cooldown_seconds,
                              settings.lore_user_cooldown_seconds)


class EventSubMessageParseError(ValueError):
    def __init__(self, missing_field: str, available_keys: list[str], reason: str = "missing_required_field") -> None:
        self.missing_field = missing_field
        self.available_keys = available_keys
        self.reason = reason
        super().__init__(reason)


def _safe_key_names(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    return sorted(
        key for key in value
        if isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key)
    )[:30]


def _required_mapping(parent: Any, key: str, path: str) -> dict[str, Any]:
    if not isinstance(parent, dict):
        raise EventSubMessageParseError(path, _safe_key_names(parent), "invalid_object")
    value = parent.get(key)
    if not isinstance(value, dict):
        reason = "missing_required_field" if key not in parent else "invalid_object"
        raise EventSubMessageParseError(path, _safe_key_names(parent), reason)
    return value


def _required_text(parent: dict[str, Any], key: str, path: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EventSubMessageParseError(path, _safe_key_names(parent), "missing_or_invalid_text")
    return value


def _parse_eventsub_chat_message(notification: dict[str, Any]) -> tuple[ChatMessage, str] | None:
    metadata = _required_mapping(notification, "metadata", "metadata")
    payload = _required_mapping(notification, "payload", "payload")
    subscription = payload.get("subscription")
    subscription_type = metadata.get("subscription_type")
    if subscription_type is None and isinstance(subscription, dict):
        subscription_type = subscription.get("type")
    if subscription_type != "channel.chat.message":
        return None
    event = _required_mapping(payload, "event", "payload.event")

    broadcaster_id = _required_text(event, "broadcaster_user_id", "payload.event.broadcaster_user_id")
    chatter_id = _required_text(event, "chatter_user_id", "payload.event.chatter_user_id")
    message_id = _required_text(event, "message_id", "payload.event.message_id")
    message_info = _required_mapping(event, "message", "payload.event.message")
    message_text = message_info.get("text")
    if not isinstance(message_text, str):
        fragments = message_info.get("fragments")
        if isinstance(fragments, list):
            message_text = "".join(
                fragment["text"] for fragment in fragments
                if isinstance(fragment, dict) and isinstance(fragment.get("text"), str)
            )
        else:
            message_text = ""
    if not message_text.strip():
        raise EventSubMessageParseError(
            "payload.event.message.text", _safe_key_names(message_info), "missing_or_invalid_text"
        )

    connected_broadcaster_id = notification.get("_connected_broadcaster_id")
    if not isinstance(connected_broadcaster_id, str) or not connected_broadcaster_id:
        connected_broadcaster_id = broadcaster_id

    raw_timestamp = metadata.get("message_timestamp") or event.get("message_timestamp")
    if raw_timestamp is None:
        timestamp = datetime.now(timezone.utc)
    else:
        try:
            timestamp = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
        except ValueError as exc:
            raise EventSubMessageParseError(
                "metadata.message_timestamp", _safe_key_names(metadata), "invalid_timestamp"
            ) from exc

    display_name = event.get("chatter_user_name") or event.get("chatter_user_login") or "viewer"
    try:
        message = ChatMessage(
            message_id=message_id,
            broadcaster_id=broadcaster_id,
            chatter_id=chatter_id,
            display_name=str(display_name),
            text=message_text,
            timestamp=timestamp,
        )
    except (TypeError, ValueError) as exc:
        raise EventSubMessageParseError(
            "payload.event", _safe_key_names(event), "invalid_chat_message"
        ) from exc
    return message, connected_broadcaster_id


def _consume_capture_task(task: asyncio.Task[Any]) -> None:
    """Retrieve the exception so background capture failures stay in sanitized logs."""
    try:
        task.result()
    except asyncio.CancelledError:
        logger.info("memory_trace stage=capture_complete result=cancelled")
    except Exception:
        # capture_memory already logged only the exception class, without its payload.
        pass


def _stored_expiry(connection: dict[str, Any]) -> datetime | None:
    raw_expiry = connection.get("expires_at")
    if not isinstance(raw_expiry, str):
        return None
    try:
        expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
    except ValueError:
        return None
    if expiry.tzinfo is None:
        return expiry.replace(tzinfo=timezone.utc)
    return expiry.astimezone(timezone.utc)


async def refresh_connection(connection: dict[str, Any], *, force_refresh: bool = False) -> str:
    broadcaster_id = str(connection["twitch_broadcaster_id"])
    lock = _refresh_locks.setdefault(broadcaster_id, asyncio.Lock())
    async with lock:
        access_token = decrypt_token(str(connection["access_token_encrypted"]))
        expiry = _stored_expiry(connection)
        should_refresh = force_refresh or (
            expiry is not None and expiry <= datetime.now(timezone.utc) + timedelta(seconds=60)
        )
        token_info: dict[str, Any] | None = None
        if not should_refresh:
            try:
                token_info = await twitch_service.validate_token(access_token)
            except TwitchAuthenticationError:
                should_refresh = True
        if token_info is not None:
            _validate_connection_identity(token_info, broadcaster_id)
            expires_in = token_info.get("expires_in")
            if isinstance(expires_in, (int, float)) and expires_in <= 60:
                should_refresh = True
            else:
                return access_token

        old_refresh_encrypted = str(connection["refresh_token_encrypted"])
        old_refresh = decrypt_token(old_refresh_encrypted)
        tokens = await twitch_service.refresh(old_refresh)
        new_access = str(tokens["access_token"])
        new_refresh = str(tokens.get("refresh_token") or old_refresh)
        validated = await twitch_service.validate_token(new_access)
        _validate_connection_identity(validated, broadcaster_id)
        expires_in = int(tokens.get("expires_in", validated.get("expires_in", 0)))
        if expires_in <= 0:
            raise TwitchError("Twitch returned a refreshed token without a valid expiry")
        new_expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        update_twitch_tokens(
            broadcaster_id,
            old_refresh_encrypted,
            encrypt_token(new_access),
            encrypt_token(new_refresh),
            new_expiry,
        )
        return new_access


def _validate_connection_identity(token_info: dict[str, Any], broadcaster_id: str) -> None:
    if token_info.get("client_id") != settings.twitch_client_id:
        raise TwitchError("Twitch token belongs to a different application")
    if str(token_info.get("user_id", "")) != broadcaster_id:
        raise TwitchError("Twitch token identity does not match the connected broadcaster")
    scopes = set(token_info.get("scopes", []))
    missing_scopes = {"user:read:chat", "user:write:chat"} - scopes
    if missing_scopes:
        raise TwitchError("Twitch token is missing required chat scopes")
    logger.info(
        "lore_trace stage=token_identity result=success broadcaster_match=true "
        "read_scope=true write_scope=true client_id_match=true"
    )


async def eventsub_access_token(broadcaster_id: str, force_refresh: bool = False) -> str:
    """Load the latest persisted credentials before every new EventSub subscription."""
    try:
        connection = load_twitch_connection(broadcaster_id)
        if connection is None:
            raise TwitchError("Twitch connection is missing")
        return await refresh_connection(connection, force_refresh=force_refresh)
    except TwitchAuthenticationError:
        raise
    except Exception:
        # Do not let provider/database exception details (which may include request
        # metadata) escape into logs or the listener's unbounded reconnect handler.
        raise TwitchAuthenticationError(
            401, "token_unavailable",
            "Twitch credentials could not be validated or refreshed; reconnect Twitch",
        ) from None


async def on_chat_message(notification: dict[str, Any]) -> None:
    try:
        parsed = _parse_eventsub_chat_message(notification)
    except EventSubMessageParseError as exc:
        available = ",".join(exc.available_keys) or "none"
        logger.warning(
            "memory_trace stage=message_parse result=failed missing_field=%s reason=%s available_keys=%s",
            exc.missing_field, exc.reason, available,
        )
        return
    except (TypeError, ValueError) as exc:
        logger.warning("memory_trace stage=message_parse result=failed reason=%s",
                       type(exc).__name__)
        return
    if parsed is None:
        logger.info("memory_trace stage=message_parse result=ignored reason=unexpected_subscription_type")
        return
    message, connected_broadcaster_id = parsed
    broadcaster_id = message.broadcaster_id
    chatter_id = message.chatter_id
    if broadcaster_id != connected_broadcaster_id:
        logger.warning("lore_trace stage=broadcaster_scope result=ignored reason=broadcaster_mismatch")
        return
    logger.info("memory_trace stage=message_parse result=success broadcaster_match=true")
    text = message.text.strip()
    parts = text.split(maxsplit=1)
    command = parts[0].casefold() if parts else ""
    if command == "!remember" and chatter_id == connected_broadcaster_id:
        logger.info("memory_trace stage=buffer_add result=skipped reason=capture_command_excluded")
        logger.info("memory_trace stage=broadcaster_check result=authorized")
        logger.info("memory_trace stage=remember_command result=recognized")
        topic = parts[1].strip() if len(parts) > 1 else None
        task = asyncio.create_task(capture_memory(broadcaster_id, topic))
        task.add_done_callback(_consume_capture_task)
        logger.info("memory_trace stage=capture_schedule result=success")
        return
    if chatter_id == connected_broadcaster_id and command == "!lore":
        logger.info("lore_trace stage=command_dispatch result=ignored reason=bot_self_message")
        return
    buffer.add(message)
    buffered_count = len(buffer.snapshot(broadcaster_id))
    logger.info("memory_trace stage=buffer_add result=success count=%d", buffered_count)
    if command == "!remember":
        logger.info("memory_trace stage=broadcaster_check result=rejected reason=sender_not_broadcaster")
        logger.info("memory_trace stage=remember_command result=ignored reason=permission_denied")
        return
    if command == "!lore" and chatter_id != connected_broadcaster_id:
        question = parts[1].strip() if len(parts) > 1 else ""
        if len(question) > 200:
            logger.info("lore_trace stage=command result=ignored reason=question_too_long")
            return
        logger.info(
            "lore_trace stage=command_dispatch result=recognized command=lore viewer=true "
            "broadcaster_match=true question_present=%s question_length=%d",
            bool(question), len(question),
        )
        if not lore_cooldowns.allow(broadcaster_id, chatter_id):
            logger.info("lore_trace stage=command result=ignored reason=cooldown")
            return
        asyncio.create_task(process_lore_question(broadcaster_id, chatter_id, question))
        logger.info("lore_trace stage=question_task result=scheduled")


async def capture_memory(broadcaster_id: str, topic: str | None = None) -> dict[str, Any]:
    try:
        result = await memory_service.capture(broadcaster_id, topic)
        logger.info("memory_trace stage=capture_complete result=%s reason=%s",
                    "created" if result.get("created") else "not_created",
                    result.get("reason", "none"))
        return result
    except Exception as exc:
        logger.error("memory_trace stage=capture_complete result=failed error_type=%s",
                     type(exc).__name__)
        raise


async def process_lore_question(broadcaster_id: str, chatter_id: str, question: str) -> None:
    try:
        logger.info("lore_trace stage=question_processing result=started question_length=%d", len(question))
        answer = await lore_service.answer(broadcaster_id, question)
        if len(answer) > 500:
            answer = answer[:497] + "..."
        await send_message(broadcaster_id, answer)
        logger.info("lore_trace stage=chat_send result=success broadcaster_id=%s", broadcaster_id)
    except Exception as exc:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", "none")
        logger.error(
            "lore_trace stage=chat_send result=failed broadcaster_id=%s error_type=%s status_code=%s",
            broadcaster_id, type(exc).__name__, status_code,
        )


async def send_message(broadcaster_id: str, message: str) -> dict[str, Any]:
    from app.database.supabase import load_twitch_connection
    logger.info("lore_trace stage=twitch_connection result=loading broadcaster_id=%s", broadcaster_id)
    connection = load_twitch_connection(broadcaster_id)
    if connection is None:
        logger.warning("lore_trace stage=twitch_connection result=failed reason=not_connected")
        raise RuntimeError("Twitch connection not found")
    logger.info("lore_trace stage=twitch_connection result=success broadcaster_id_match=true")
    access_token = await refresh_connection(connection)
    logger.info("lore_trace stage=send_token result=validated broadcaster_identity_match=true")
    logger.info("lore_trace stage=helix_dispatch result=started broadcaster_sender_match=true")
    return await twitch_service.send_chat_message(access_token, broadcaster_id, message)
