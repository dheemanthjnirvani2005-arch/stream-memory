from collections import deque
from datetime import datetime, timedelta, timezone

from app.memory.models import ChatMessage


class RollingChatBuffer:
    def __init__(self, max_age: timedelta = timedelta(minutes=10), max_messages: int = 300) -> None:
        self.max_age = max_age
        self.max_messages = max_messages
        self._channels: dict[str, deque[ChatMessage]] = {}

    def add(self, message: ChatMessage) -> None:
        channel = self._channels.setdefault(message.broadcaster_id, deque())
        channel.append(message)
        self._evict(message.broadcaster_id, message.timestamp)

    def _evict(self, broadcaster_id: str, now: datetime | None = None) -> None:
        channel = self._channels.get(broadcaster_id)
        if channel is None:
            return
        cutoff = (now or datetime.now(timezone.utc)) - self.max_age
        while channel and (channel[0].timestamp < cutoff or len(channel) > self.max_messages):
            channel.popleft()

    def snapshot(self, broadcaster_id: str) -> list[ChatMessage]:
        self._evict(broadcaster_id)
        return list(self._channels.get(broadcaster_id, ()))
