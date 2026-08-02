"""Bounded asynchronous FIFO buffer for finalized captured audio chunks."""

import asyncio
from collections import deque

from app.application.dto import CapturedAudioChunk
from app.application.exceptions import ApplicationValidationError


class BoundedAudioChunkBuffer:
    """Keep newest captured chunks when asynchronous processing falls behind."""

    def __init__(self, *, max_size: int = 3) -> None:
        """Initialize an empty FIFO buffer with a positive fixed capacity."""

        if max_size <= 0:
            raise ApplicationValidationError(
                "Buffer maximum size must be greater than zero."
            )

        self._max_size = max_size
        self._chunks: deque[CapturedAudioChunk] = deque()
        self._condition = asyncio.Condition()
        self._is_closed = False

    @property
    def size(self) -> int:
        """Return the current number of buffered chunks."""

        return len(self._chunks)

    @property
    def is_closed(self) -> bool:
        """Return whether the buffer no longer accepts chunks."""

        return self._is_closed

    async def put(self, chunk: CapturedAudioChunk) -> CapturedAudioChunk | None:
        """Append a chunk, dropping and returning the oldest item when full."""

        async with self._condition:
            if self._is_closed:
                raise RuntimeError("Audio chunk buffer is closed.")

            dropped_chunk = None
            if len(self._chunks) == self._max_size:
                dropped_chunk = self._chunks.popleft()
            self._chunks.append(chunk)
            self._condition.notify()
            return dropped_chunk

    async def get(self) -> CapturedAudioChunk:
        """Return the next chunk, waiting until one is available or closure ends it."""

        async with self._condition:
            while not self._chunks:
                if self._is_closed:
                    raise RuntimeError("Audio chunk buffer is closed and empty.")
                await self._condition.wait()
            return self._chunks.popleft()

    async def close(self) -> None:
        """Prevent future writes and wake every waiting consumer."""

        async with self._condition:
            if self._is_closed:
                return

            self._is_closed = True
            self._condition.notify_all()
