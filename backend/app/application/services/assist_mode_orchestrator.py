"""Session-scoped orchestration for transient Assist Mode enrichment."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

from app.application.dto.ai import GermanLevel
from app.application.dto.assist_mode import (
    AssistCapability,
    AssistState,
    AssistUpdate,
    TranscriptSegment,
)
from app.application.exceptions import ApplicationValidationError
from app.application.services.recent_transcript_context import RecentTranscriptContext

if TYPE_CHECKING:
    from app.application.use_cases import (
        GenerateReplySuggestionsUseCase,
        SimplifyTranscriptSegmentUseCase,
        TranslateTranscriptSegmentUseCase,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class AssistModeConfiguration:
    """Immutable feature selection and bounded-queue settings for one session."""

    translation_enabled: bool = True
    simplification_enabled: bool = False
    simplification_level: GermanLevel | None = None
    reply_coaching_enabled: bool = True
    max_queue_size: int = 8

    def __post_init__(self) -> None:
        """Validate V1 capability configuration without provider-specific rules."""

        if self.simplification_enabled:
            if self.simplification_level is None:
                raise ApplicationValidationError(
                    "Simplification level is required when simplification is enabled."
                )
            if self.simplification_level not in {GermanLevel.B1, GermanLevel.B2}:
                raise ApplicationValidationError(
                    "Simplification level must be B1 or B2 for Assist Mode."
                )
        if (
            not isinstance(self.max_queue_size, int)
            or isinstance(self.max_queue_size, bool)
            or self.max_queue_size <= 0
        ):
            raise ApplicationValidationError("Assist Mode queue size must be positive.")


class AssistModeLifecycleState(StrEnum):
    """Privacy-safe session lifecycle states."""

    STOPPED = "stopped"
    RUNNING = "running"
    FAILED = "failed"
    STOPPING = "stopping"


@runtime_checkable
class AssistUpdateSink(Protocol):
    """Receives transient Assist Mode updates without transport dependencies."""

    async def publish(self, update: AssistUpdate) -> None:
        """Publish one privacy-safe update."""


class AssistModeOrchestrator:
    """Process one session's finalized transcript enrichment work sequentially."""

    def __init__(
        self,
        *,
        configuration: AssistModeConfiguration,
        translation_use_case: "TranslateTranscriptSegmentUseCase",
        simplification_use_case: "SimplifyTranscriptSegmentUseCase",
        reply_suggestions_use_case: "GenerateReplySuggestionsUseCase",
        update_sink: AssistUpdateSink,
    ) -> None:
        """Initialize session-local dependencies without persistence ownership."""

        self._configuration = configuration
        self._translation_use_case = translation_use_case
        self._simplification_use_case = simplification_use_case
        self._reply_suggestions_use_case = reply_suggestions_use_case
        self._update_sink = update_sink
        self._context = RecentTranscriptContext()
        self._queue: asyncio.Queue[TranscriptSegment] = asyncio.Queue(
            maxsize=configuration.max_queue_size
        )
        self._worker: asyncio.Task[None] | None = None
        self._state = AssistModeLifecycleState.STOPPED
        self._terminated = False

    @property
    def state(self) -> AssistModeLifecycleState:
        """Return a read-only lifecycle state without session content."""

        return self._state

    async def start(self) -> None:
        """Start the single session worker once; repeated active starts are safe."""

        if self._state is AssistModeLifecycleState.RUNNING:
            return
        if self._terminated or self._state is AssistModeLifecycleState.FAILED:
            raise RuntimeError("Assist Mode session cannot be started again.")
        self._state = AssistModeLifecycleState.RUNNING
        self._worker = asyncio.create_task(self._run_worker())

    async def enqueue(self, segment: TranscriptSegment) -> bool:
        """Queue one persisted segment without waiting for capacity to become free."""

        if self._state is not AssistModeLifecycleState.RUNNING:
            raise RuntimeError("Assist Mode session is not running.")
        if not isinstance(segment, TranscriptSegment):
            raise ApplicationValidationError("Transcript segment is invalid.")

        dropped: TranscriptSegment | None = None
        try:
            self._queue.put_nowait(segment)
        except asyncio.QueueFull:
            dropped = self._queue.get_nowait()
            self._queue.put_nowait(segment)

        if dropped is not None and not await self._publish_dropped(dropped):
            return False
        return self._state is AssistModeLifecycleState.RUNNING

    async def stop(self) -> None:
        """Cancel queued/current work, clear session context, and join the worker."""

        if self._terminated:
            return
        self._terminated = True
        if self._state is AssistModeLifecycleState.RUNNING:
            self._state = AssistModeLifecycleState.STOPPING
        self._clear_pending_queue()
        worker = self._worker
        self._worker = None
        if worker is not None and not worker.done():
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        self._context.clear()
        if self._state is not AssistModeLifecycleState.FAILED:
            self._state = AssistModeLifecycleState.STOPPED

    async def _run_worker(self) -> None:
        """Consume queued segments sequentially until cancellation or sink failure."""

        try:
            while self._state is AssistModeLifecycleState.RUNNING:
                segment = await self._queue.get()
                try:
                    await self._process_segment(segment)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            raise

    async def _process_segment(self, segment: TranscriptSegment) -> None:
        """Enrich one persisted segment in fixed V1 priority order."""

        try:
            self._context.add(segment)
        except Exception:
            await self._publish_generic_failure(
                segment.transcript_id,
                AssistCapability.REPLY_COACHING,
            )
            return

        if self._configuration.translation_enabled:
            await self._run_capability(
                segment,
                AssistCapability.TRANSLATION,
                lambda: self._translation_use_case.execute(segment),
            )
        if self._configuration.reply_coaching_enabled:
            reply_context = self._context.build_reply_context()
            if reply_context is not None:
                await self._run_capability(
                    segment,
                    AssistCapability.REPLY_COACHING,
                    lambda: self._reply_suggestions_use_case.execute(reply_context),
                )
        if self._configuration.simplification_enabled:
            level = self._configuration.simplification_level
            if level is not None:
                await self._run_capability(
                    segment,
                    AssistCapability.SIMPLIFICATION,
                    lambda: self._simplification_use_case.execute(
                        segment,
                        target_level=level,
                    ),
                )

    async def _run_capability(
        self,
        segment: TranscriptSegment,
        capability: AssistCapability,
        operation: Callable[[], Awaitable[AssistUpdate]],
    ) -> None:
        """Publish processing and then isolate unexpected use-case failures."""

        if not await self._publish(
            AssistUpdate(
                transcript_id=segment.transcript_id,
                capability=capability,
                state=AssistState.PROCESSING,
            )
        ):
            return
        try:
            update = await operation()
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._publish_generic_failure(segment.transcript_id, capability)
            return
        if self._state is AssistModeLifecycleState.RUNNING:
            await self._publish(update)

    async def _publish_dropped(self, segment: TranscriptSegment) -> bool:
        """Notify enabled capabilities that a queued segment was unavailable."""

        for capability in self._enabled_capabilities():
            if not await self._publish(
                AssistUpdate(
                    transcript_id=segment.transcript_id,
                    capability=capability,
                    state=AssistState.UNAVAILABLE,
                )
            ):
                return False
        return True

    async def _publish_generic_failure(
        self,
        transcript_id: UUID,
        capability: AssistCapability,
    ) -> None:
        """Publish a provider-detail-free failed update when the session is active."""

        if self._state is AssistModeLifecycleState.RUNNING:
            await self._publish(
                AssistUpdate(
                    transcript_id=transcript_id,
                    capability=capability,
                    state=AssistState.FAILED,
                )
            )

    async def _publish(self, update: AssistUpdate) -> bool:
        """Publish only while active; sink failures terminate this session safely."""

        if self._state is not AssistModeLifecycleState.RUNNING:
            return False
        try:
            await self._update_sink.publish(update)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._state = AssistModeLifecycleState.FAILED
            self._clear_pending_queue()
            return False
        return self._state is AssistModeLifecycleState.RUNNING

    def _enabled_capabilities(self) -> tuple[AssistCapability, ...]:
        """Return enabled capabilities in their fixed execution priority."""

        capabilities: list[AssistCapability] = []
        if self._configuration.translation_enabled:
            capabilities.append(AssistCapability.TRANSLATION)
        if self._configuration.reply_coaching_enabled:
            capabilities.append(AssistCapability.REPLY_COACHING)
        if self._configuration.simplification_enabled:
            capabilities.append(AssistCapability.SIMPLIFICATION)
        return tuple(capabilities)

    def _clear_pending_queue(self) -> None:
        """Discard only not-yet-started transient enrichment items."""

        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()
