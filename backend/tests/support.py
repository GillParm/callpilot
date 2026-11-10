"""Test doubles shared by session and API tests."""

from __future__ import annotations

import asyncio
from typing import Any


class RecordingTransport:
    """A CallTransport that keeps everything it is sent."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.audio: list[bytes] = []
        self.clears = 0
        self._changed = asyncio.Condition()

    async def send_event(self, event: dict[str, Any]) -> None:
        async with self._changed:
            self.events.append(event)
            self._changed.notify_all()

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.append(pcm)

    async def clear_audio(self) -> None:
        self.clears += 1

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == event_type]

    async def wait_for(self, event_type: str, count: int = 1, within: float = 2.0) -> None:
        async def _ready() -> None:
            async with self._changed:
                await self._changed.wait_for(lambda: len(self.of_type(event_type)) >= count)

        await asyncio.wait_for(_ready(), within)

    def agent_lines(self) -> list[str]:
        return [e["text"] for e in self.of_type("transcript") if e["role"] == "agent"]
