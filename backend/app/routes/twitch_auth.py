import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
import asyncio

from app.config import get_settings
from app.auth.session import COOKIE_NAME, make_session, read_session
from app.database.supabase import (
    disconnect_twitch_connection,
    save_twitch_credentials,
    upsert_community,
)
from app.twitch.crypto import encrypt_token
from app.twitch.service import EventSubListener, TwitchError, TwitchService

router = APIRouter(prefix="/auth/twitch", tags=["twitch-auth"])
twitch_service = TwitchService()
listeners: dict[str, EventSubListener] = {}


@router.get("/login")
async def login() -> Response:
    state = secrets.token_urlsafe(32)
    response = RedirectResponse(twitch_service.authorization_url(state), status_code=302)
    settings = get_settings()
    response.set_cookie("twitch_oauth_state", state, httponly=True, secure=settings.session_cookie_secure,
                        samesite="lax", max_age=600)
    return response


@router.get("/callback")
async def callback(request: Request, response: Response, code: str | None = None,
                   state: str | None = None, error: str | None = None,
                   twitch_oauth_state: str | None = Cookie(default=None)) -> Response:
    settings = get_settings()
    if error:
        raise HTTPException(400, "Twitch authorization was not granted")
    if not code or not state or not twitch_oauth_state or not secrets.compare_digest(state, twitch_oauth_state):
        raise HTTPException(400, "Invalid or missing OAuth state")
    try:
        token_data = await twitch_service.exchange_code(code)
        access_token = str(token_data["access_token"])
        token_info = await twitch_service.validate_token(access_token)
        if token_info.get("user_id") is None:
            raise TwitchError("A broadcaster User Access Token is required")
        broadcaster = await twitch_service.broadcaster(access_token)
        if token_info["user_id"] != broadcaster["id"]:
            raise TwitchError("Authorized identity does not match broadcaster profile")
        community = upsert_community({
            "twitch_broadcaster_id": broadcaster["id"],
            "twitch_login": broadcaster["login"],
            "display_name": broadcaster["display_name"],
        })
        save_twitch_credentials({
            "community_id": str(community["id"]),
            "twitch_user_id": broadcaster["id"],
            "access_token_encrypted": encrypt_token(access_token),
            "refresh_token_encrypted": encrypt_token(str(token_data["refresh_token"])),
            "scopes": token_info.get("scopes", []),
            "expires_at": (datetime.now(timezone.utc) + timedelta(
                seconds=int(token_data["expires_in"])
            )).isoformat(),
        })
    except (TwitchError, KeyError, ValueError) as exc:
        raise HTTPException(502, "Twitch authorization could not be completed") from exc
    except Exception as exc:
        raise HTTPException(502, "Twitch connection could not be stored") from exc
    redirect = RedirectResponse(settings.twitch_success_redirect, status_code=303)
    redirect.delete_cookie("twitch_oauth_state", httponly=True, secure=settings.session_cookie_secure, samesite="lax")
    redirect.set_cookie(COOKIE_NAME, make_session(broadcaster["id"]), httponly=True,
        secure=settings.session_cookie_secure, samesite="lax", max_age=60 * 60 * 24 * 30)
    from app.twitch.runtime import eventsub_access_token, on_chat_message
    from app.database.supabase import load_twitch_connection
    from app.twitch.service import EventSubListener
    connection = load_twitch_connection(broadcaster["id"])
    if connection:
        listener = EventSubListener(
            twitch_service, "", broadcaster["id"], on_chat_message,
            token_provider=lambda force: eventsub_access_token(broadcaster["id"], force),
        )
        listeners[broadcaster["id"]] = listener
        asyncio.create_task(listener.run())
    return redirect


@router.post("/disconnect", status_code=204)
async def disconnect(session: str | None = Cookie(default=None, alias=COOKIE_NAME)) -> Response:
    broadcaster_id = read_session(session)
    if not broadcaster_id:
        raise HTTPException(401, "Streamer session required")
    listener = listeners.pop(broadcaster_id, None)
    if listener:
        listener.stop()
    disconnect_twitch_connection(broadcaster_id)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="lax")
    return response
