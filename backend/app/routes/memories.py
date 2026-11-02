from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Cookie, HTTPException, Query

from app.auth.session import COOKIE_NAME, read_session
from app.database import supabase
from app.memory.models import ManualMemory, MemoryEdit
from app.memory.service import MemoryService
from app.twitch.runtime import capture_memory

router = APIRouter(prefix="/api/memories", tags=["memories"])
logger = logging.getLogger(__name__)


def broadcaster_from_cookie(session: str | None) -> str:
    broadcaster_id = read_session(session)
    if not broadcaster_id:
        raise HTTPException(401, "Streamer session required")
    return broadcaster_id


@router.post("/capture")
async def capture(session: str | None = Cookie(default=None, alias=COOKIE_NAME), topic: str | None = None):
    broadcaster_id = broadcaster_from_cookie(session)
    return await capture_memory(broadcaster_id, topic)


@router.post("")
async def manual_create(data: ManualMemory, session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    broadcaster_id = broadcaster_from_cookie(session)
    try:
        return MemoryService.create_manual(broadcaster_id, data)
    except LookupError as exc:
        raise HTTPException(404, "Connected community not found") from exc


@router.get("")
async def get_memories(status: str | None = None, q: str | None = Query(default=None, max_length=100),
                       session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    try:
        broadcaster_id = broadcaster_from_cookie(session)
    except HTTPException:
        if status == "pending":
            logger.warning("memory_trace stage=dashboard_pending result=early_return reason=unauthenticated")
        raise
    if status not in {None, "pending", "approved", "rejected"}:
        raise HTTPException(422, "Invalid memory status")
    try:
        memories = supabase.list_memories(broadcaster_id, status=status, query=q)
    except Exception as exc:
        if status == "pending":
            logger.error("memory_trace stage=dashboard_pending result=failed error_type=%s",
                         type(exc).__name__)
        raise
    if status == "pending":
        logger.info("memory_trace stage=dashboard_pending result=success count=%d", len(memories))
    return memories


@router.get("/search")
async def viewer_search(q: str = Query(min_length=1, max_length=200),
                        session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    broadcaster_id = broadcaster_from_cookie(session)
    return supabase.list_memories(broadcaster_id, status="approved", query=q)


@router.post("/{memory_id}/approve")
async def approve(memory_id: str, session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    broadcaster_id = broadcaster_from_cookie(session)
    updated = supabase.update_memory(broadcaster_id, memory_id, {"status": "approved",
        "approved_by": broadcaster_id, "approved_at": datetime.now(timezone.utc).isoformat()})
    if not updated:
        raise HTTPException(404, "Memory not found")
    return updated


@router.post("/{memory_id}/reject")
async def reject(memory_id: str, session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    broadcaster_id = broadcaster_from_cookie(session)
    updated = supabase.update_memory(broadcaster_id, memory_id, {"status": "rejected"})
    if not updated:
        raise HTTPException(404, "Memory not found")
    return updated


@router.put("/{memory_id}")
async def edit(memory_id: str, data: MemoryEdit, session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    broadcaster_id = broadcaster_from_cookie(session)
    updated = supabase.update_memory(broadcaster_id, memory_id, data.model_dump())
    if not updated:
        raise HTTPException(404, "Memory not found")
    return updated


@router.delete("/{memory_id}")
async def remove(memory_id: str, session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    broadcaster_id = broadcaster_from_cookie(session)
    if not supabase.delete_memory(broadcaster_id, memory_id):
        raise HTTPException(404, "Memory not found")
    return {"deleted": True}
