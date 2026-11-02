from datetime import datetime, timezone
import logging
from typing import Any

from pydantic import ValidationError

from app.ai.extraction import ExtractionProvider, get_extraction_provider
from app.database import supabase
from app.memory.buffer import RollingChatBuffer
from app.memory.models import ChatMessage, ExtractionResult, ManualMemory, MemoryProposal

logger = logging.getLogger(__name__)


class MemoryService:
    def __init__(self, buffer: RollingChatBuffer, provider: ExtractionProvider | None = None) -> None:
        self.buffer = buffer
        self.provider = provider or get_extraction_provider()

    async def capture(self, broadcaster_id: str, topic: str | None = None) -> dict[str, Any]:
        messages = self.buffer.snapshot(broadcaster_id)
        logger.info("memory_trace stage=buffer_snapshot result=%s count=%d",
                    "success" if messages else "early_return", len(messages))
        if not messages:
            logger.info("memory_trace stage=provider_input result=skipped reason=empty_buffer")
            return {"created": False, "reason": "empty_chat_buffer"}
        logger.info("memory_trace stage=provider_input result=sent message_count=%d topic_provided=%s provider=%s",
                    len(messages), topic is not None, type(self.provider).__name__)
        try:
            result = await self.provider.extract(messages, topic)
        except Exception as exc:
            logger.error("memory_trace stage=provider_output result=failed error_type=%s",
                         type(exc).__name__)
            raise
        try:
            result = ExtractionResult.model_validate(result.model_dump())
        except (ValidationError, AttributeError) as exc:
            logger.error("memory_trace stage=pydantic_validation result=failed error_type=%s",
                         type(exc).__name__)
            raise
        proposal = result.proposal
        if proposal is None:
            logger.info("memory_trace stage=provider_output result=no_proposal reason=no_meaningful_memory")
            logger.info(
                "memory_trace stage=pydantic_validation result=success "
                "model=ExtractionResult proposal_present=false"
            )
            logger.info("memory_trace stage=supabase_persistence result=skipped reason=no_proposal")
            return {"created": False, "reason": "no_meaningful_memory"}
        try:
            proposal = MemoryProposal.model_validate(proposal.model_dump())
        except (ValidationError, AttributeError) as exc:
            logger.error("memory_trace stage=pydantic_validation result=failed error_type=%s",
                         type(exc).__name__)
            raise
        logger.info("memory_trace stage=pydantic_validation result=success")
        allowed = {message.message_id: message for message in messages}
        if not set(proposal.source_message_ids).issubset(allowed):
            logger.warning("memory_trace stage=evidence_validation result=failed reason=source_not_in_buffer")
            raise ValueError("Proposal references messages outside the captured buffer")
        sources = [{"source_type": "chat_capture", "twitch_message_id": message_id,
                    "source_excerpt": allowed[message_id].text,
                    "source_time": allowed[message_id].timestamp.isoformat()}
                   for message_id in proposal.source_message_ids]
        logger.info("memory_trace stage=evidence_validation result=success source_count=%d", len(sources))
        logger.info("memory_trace stage=supabase_persistence result=started status=pending")
        try:
            memory = supabase.create_memory_with_sources(
                broadcaster_id, self._proposal_row(proposal, broadcaster_id), sources
            )
        except Exception as exc:
            logger.error("memory_trace stage=supabase_persistence result=failed error_type=%s",
                         type(exc).__name__)
            raise
        logger.info("memory_trace stage=supabase_persistence result=success status=pending source_count=%d",
                    len(sources))
        return {"created": True, "memory": memory}

    @staticmethod
    def _proposal_row(proposal: MemoryProposal, broadcaster_id: str) -> dict[str, object]:
        return {"title": proposal.title, "description": proposal.description,
                "category": proposal.category, "confidence": proposal.confidence,
                "status": "pending", "event_time": proposal.event_time.isoformat() if proposal.event_time else None,
                "created_by": "ai"}

    @staticmethod
    def create_manual(broadcaster_id: str, data: ManualMemory) -> dict[str, Any]:
        payload = {"title": data.title, "description": data.description, "category": data.category,
                   "status": "approved", "event_time": data.event_time.isoformat() if data.event_time else None,
                   "created_by": broadcaster_id, "approved_by": broadcaster_id,
                   "approved_at": datetime.now(timezone.utc).isoformat()}
        source = ({"source_type": "manual_note", "source_excerpt": data.source_note,
                   "stream_url": data.stream_url} if data.source_note or data.stream_url else None)
        return supabase.create_memory(broadcaster_id, payload, source)
