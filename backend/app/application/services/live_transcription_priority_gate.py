"""Cooperative priority coordination between STT and best-effort Assist work."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum

_CADENCE_HISTORY_SIZE = 4


class TranslationAdmissionReason(StrEnum):
    """Closed reasons for a content-free translation scheduling decision."""

    PROBE = "probe"
    PREDICTED_WINDOW = "predicted_window"
    EXTENDED_IDLE = "extended_idle"
    UPSTREAM_BACKLOG = "upstream_backlog"


@dataclass(frozen=True, slots=True)
class TranslationAdmission:
    """One bounded translation admission decision based on observed timing."""

    reason: TranslationAdmissionReason
    idle_window_ms: int
    wait_seconds: float
    was_deferred: bool = False
    deferred_idle_window_ms: int = 0
    deferred_reason: TranslationAdmissionReason | None = None
    extended_idle_margin_ms: int = 0


class LiveTranscriptionPriorityGate:
    """Expose STT activity and observed cadence without retaining media data."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        """Create an idle gate for one container lifecycle."""

        self._clock = clock
        self._active_stt_count = 0
        self._stt_start_generation = 0
        self._last_stt_started_at: float | None = None
        self._stt_start_intervals: deque[float] = deque(maxlen=_CADENCE_HISTORY_SIZE)
        self._upstream_pending_stt_chunks = 0
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self._backlog_cleared_event = asyncio.Event()
        self._backlog_cleared_event.set()
        self._activity_event = asyncio.Event()

    @property
    def active_stt_count(self) -> int:
        """Return the current bounded count of in-flight STT calls."""

        return self._active_stt_count

    @property
    def stt_start_generation(self) -> int:
        """Return a monotonic STT-start generation for preemption observation."""

        return self._stt_start_generation

    @property
    def upstream_pending_stt_chunks(self) -> int:
        """Return the latest bounded source-side audio backlog count."""

        return self._upstream_pending_stt_chunks

    def set_upstream_pending_stt_chunks(self, pending_chunks: int) -> None:
        """Record one validated, content-free source-side backlog snapshot."""

        if type(pending_chunks) is not int or pending_chunks < 0:
            return
        self._upstream_pending_stt_chunks = pending_chunks
        if pending_chunks == 0:
            self._backlog_cleared_event.set()
        else:
            self._backlog_cleared_event.clear()

    def stt_started(self) -> None:
        """Mark one latency-critical STT request active and observe its cadence."""

        started_at = self._clock()
        previous_started_at = self._last_stt_started_at
        if previous_started_at is not None:
            interval = started_at - previous_started_at
            if interval > 0:
                self._stt_start_intervals.append(interval)
        self._last_stt_started_at = started_at
        self._active_stt_count += 1
        self._stt_start_generation += 1
        self._idle_event.clear()
        self._activity_event.set()

    def stt_finished(self) -> None:
        """Release one completed STT request without underflowing the count."""

        if self._active_stt_count <= 0:
            return
        self._active_stt_count -= 1
        if self._active_stt_count == 0:
            self._idle_event.set()
        self._activity_event.set()

    async def wait_until_idle(self) -> None:
        """Wait until no STT request is active without polling or sleeping."""

        while self._active_stt_count > 0:
            await self._idle_event.wait()

    async def wait_for_stt_start_after(self, generation: int) -> None:
        """Wait for the next STT start after a captured generation."""

        while self._stt_start_generation == generation:
            await self._activity_event.wait()
            self._activity_event.clear()

    def translation_admission(
        self,
        *,
        estimated_translation_duration_ms: int | None,
    ) -> TranslationAdmission | None:
        """Return a prediction-based admission or the bounded wait it requires.

        The forecast uses only the shortest interval from the recent, bounded
        observed STT-start history. It never assumes a configured chunk cadence
        and returns ``None`` while STT is currently active.
        """

        if self._active_stt_count > 0:
            return None
        if self._upstream_pending_stt_chunks > 0:
            return TranslationAdmission(
                reason=TranslationAdmissionReason.UPSTREAM_BACKLOG,
                idle_window_ms=0,
                wait_seconds=0.0,
            )
        if (
            estimated_translation_duration_ms is None
            or estimated_translation_duration_ms <= 0
            or self._last_stt_started_at is None
            or not self._stt_start_intervals
        ):
            return TranslationAdmission(
                reason=TranslationAdmissionReason.PROBE,
                idle_window_ms=0,
                wait_seconds=0.0,
            )

        shortest_interval = min(self._stt_start_intervals)
        longest_interval = max(self._stt_start_intervals)
        now = self._clock()
        earliest_expected_start = self._last_stt_started_at + shortest_interval
        extended_idle_boundary = self._last_stt_started_at + longest_interval
        cadence_jitter_margin_ms = int((longest_interval - shortest_interval) * 1_000)
        extended_idle_margin_ms = max(
            cadence_jitter_margin_ms,
            estimated_translation_duration_ms,
        )
        extended_idle_boundary += extended_idle_margin_ms / 1_000
        remaining_seconds = earliest_expected_start - now
        if remaining_seconds <= 0 and now >= extended_idle_boundary:
            return TranslationAdmission(
                reason=TranslationAdmissionReason.EXTENDED_IDLE,
                idle_window_ms=0,
                wait_seconds=0.0,
                extended_idle_margin_ms=extended_idle_margin_ms,
            )

        idle_window_ms = max(0, int(remaining_seconds * 1_000))
        if idle_window_ms >= estimated_translation_duration_ms:
            return TranslationAdmission(
                reason=TranslationAdmissionReason.PREDICTED_WINDOW,
                idle_window_ms=idle_window_ms,
                wait_seconds=0.0,
                extended_idle_margin_ms=extended_idle_margin_ms,
            )
        return TranslationAdmission(
            reason=TranslationAdmissionReason.PREDICTED_WINDOW,
            idle_window_ms=idle_window_ms,
            wait_seconds=max(0.0, extended_idle_boundary - now),
            extended_idle_margin_ms=extended_idle_margin_ms,
        )

    async def wait_for_translation_admission(
        self,
        *,
        estimated_translation_duration_ms: int | None,
    ) -> TranslationAdmission:
        """Admit translation only in a measured idle window or natural pause.

        A positive wait is the observed time to the latest recently observed
        STT-start boundary, not an arbitrary delay. A new STT start wakes this
        method immediately; otherwise reaching that boundary, including its
        cadence-jitter and measured-translation-duration margin, proves an
        extended source-audio pause.
        """

        was_deferred = False
        deferred_idle_window_ms = 0
        deferred_reason: TranslationAdmissionReason | None = None
        while True:
            await self.wait_until_idle()
            admission = self.translation_admission(
                estimated_translation_duration_ms=estimated_translation_duration_ms
            )
            if admission is None:
                continue
            if admission.reason is TranslationAdmissionReason.UPSTREAM_BACKLOG:
                was_deferred = True
                if deferred_reason is None:
                    deferred_reason = admission.reason
                await self._backlog_cleared_event.wait()
                continue
            if admission.wait_seconds <= 0:
                return TranslationAdmission(
                    reason=admission.reason,
                    idle_window_ms=admission.idle_window_ms,
                    wait_seconds=0.0,
                    was_deferred=was_deferred,
                    deferred_idle_window_ms=deferred_idle_window_ms,
                    deferred_reason=deferred_reason,
                    extended_idle_margin_ms=admission.extended_idle_margin_ms,
                )

            was_deferred = True
            deferred_idle_window_ms = admission.idle_window_ms
            deferred_reason = admission.reason
            generation = self._stt_start_generation
            stt_started_task = asyncio.create_task(
                self.wait_for_stt_start_after(generation)
            )
            deadline_task = asyncio.create_task(asyncio.sleep(admission.wait_seconds))
            wait_tasks = {stt_started_task, deadline_task}
            try:
                done, _ = await asyncio.wait(
                    wait_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for task in wait_tasks:
                    if not task.done():
                        task.cancel()
                for task in wait_tasks:
                    with suppress(asyncio.CancelledError):
                        await task
            if stt_started_task in done:
                continue
