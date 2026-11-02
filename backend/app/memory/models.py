from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal["inside_joke", "memorable_moment", "community_achievement", "recurring_character", "stream_incident", "community_event", "other"]


class ChatMessage(BaseModel):
    message_id: str
    broadcaster_id: str
    chatter_id: str
    display_name: str
    text: str = Field(max_length=5000)
    timestamp: datetime


class MemoryProposal(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    category: Category
    confidence: float = Field(ge=0, le=1)
    source_message_ids: list[str] = Field(min_length=1)
    event_time: datetime | None = None


class ExtractionResult(BaseModel):
    proposal: MemoryProposal | None


class ManualMemory(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    category: Category
    event_time: datetime | None = None
    source_note: str | None = Field(default=None, max_length=2000)
    stream_url: str | None = Field(default=None, max_length=500)


class MemoryEdit(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    category: Category
    event_time: datetime | None = None
