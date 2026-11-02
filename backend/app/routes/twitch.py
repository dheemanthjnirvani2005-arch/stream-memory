from fastapi import APIRouter, Cookie, HTTPException

from app.auth.session import COOKIE_NAME, read_session
from app.database.supabase import load_twitch_connection
from app.twitch.crypto import decrypt_token
from app.twitch.service import EventSubListener
from app.routes.twitch_auth import listeners, twitch_service

router = APIRouter(prefix="/api/twitch", tags=["twitch"])


@router.get("/status")
async def status(session: str | None = Cookie(default=None, alias=COOKIE_NAME)) -> dict[str, str | bool | None]:
    stream_memory_broadcaster = read_session(session)
    if not stream_memory_broadcaster:
        return {"connected": False, "listener": "disconnected", "broadcaster": None}
    connection = load_twitch_connection(stream_memory_broadcaster)
    if not connection or not connection.get("access_token_encrypted"):
        return {"connected": False, "listener": "disconnected", "broadcaster": None,
                "listener_error": None}
    listener = listeners.get(stream_memory_broadcaster)
    return {"connected": True, "listener": listeners.get(stream_memory_broadcaster, None).status
            if stream_memory_broadcaster in listeners else "stopped",
            "broadcaster": connection.get("display_name"),
            "listener_error": listener.error_message if listener else None}


@router.post("/listener/start")
async def start_listener(session: str | None = Cookie(default=None, alias=COOKIE_NAME)) -> dict[str, str]:
    stream_memory_broadcaster = read_session(session)
    if not stream_memory_broadcaster:
        raise HTTPException(401, "Connect Twitch first")
    if stream_memory_broadcaster in listeners and listeners[stream_memory_broadcaster].status in {"connected", "connecting"}:
        return {"status": listeners[stream_memory_broadcaster].status}
    connection = load_twitch_connection(stream_memory_broadcaster)
    if not connection or not connection.get("access_token_encrypted"):
        raise HTTPException(401, "Connect Twitch first")
    from app.twitch.runtime import eventsub_access_token, on_chat_message
    listener = EventSubListener(
        twitch_service, "", stream_memory_broadcaster, on_chat_message,
        token_provider=lambda force: eventsub_access_token(stream_memory_broadcaster, force),
    )
    listeners[stream_memory_broadcaster] = listener
    import asyncio
    asyncio.create_task(listener.run())
    return {"status": "connecting"}
