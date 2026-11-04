"""Collect photo album updates before recognizing a replacement table."""
import asyncio
from dataclasses import dataclass, field
from time import monotonic

from queue_bot.services.errors import BotError


@dataclass
class _Batch:
    messages: dict = field(default_factory=dict)
    updated: float = field(default_factory=monotonic)


class PhotoAlbums:
    def __init__(self, quiet_seconds=1.2):
        self.quiet_seconds = quiet_seconds
        self.pending = {}
        self.finished = {}

    async def collect(self, message):
        if not message.media_group_id:
            return [message]
        now = monotonic()
        self.finished = {key: value for key, value in self.finished.items() if value[0] > now}
        key = (message.chat.id, message.from_user.id, message.media_group_id)
        if key in self.finished:
            if message.message_id in self.finished[key][1]:
                return None
            raise BotError('Частина альбому надійшла запізно. Надішліть обидва фото разом новим альбомом.')
        batch = self.pending.get(key)
        if batch is not None:
            if message.message_id not in batch.messages:
                batch.messages[message.message_id] = message
                batch.updated = now
            return None
        batch = _Batch(messages={message.message_id: message}, updated=now)
        self.pending[key] = batch
        try:
            while (delay := self.quiet_seconds - (monotonic() - batch.updated)) > 0:
                await asyncio.sleep(delay)
            if len(batch.messages) > 2:
                raise BotError('Надішліть максимум 2 фото таблиці одним альбомом. Цей альбом не оброблено.')
            return [batch.messages[mid] for mid in sorted(batch.messages)]
        finally:
            self.pending.pop(key, None)
            self.finished[key] = (monotonic() + 120, set(batch.messages))
