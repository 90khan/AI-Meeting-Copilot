"""Session-scoped orchestration for transient Assist Mode enrichment."""

import asyncio
import time
from collections import deque
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
from app.application.services.live_transcription_priority_gate import (
    LiveTranscriptionPriorityGate,
    TranslationAdmission,
    TranslationAdmissionReason,
)
from app.application.services.recent_transcript_context import RecentTranscriptContext
from app.core.logging import get_logger
from app.core.throughput_diagnostics import emit_throughput

if TYPE_CHECKING:
    from app.application.use_cases import (
        GenerateReplySuggestionsUseCase,
        SimplifyTranscriptSegmentUseCase,
        TranslateTranscriptSegmentUseCase,
    )


_LOGGER = get_logger(__name__)
_TRANSLATION_DURATION_HISTORY_SIZE = 4


@dataclass(frozen=True, slots=True, kw_only=True)
class _QueuedAssistSegment:
    """Keep internal queue timing separate from public transcript DTOs."""

    ordinal: int
    queued_at: float
    segment: TranscriptSegment


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
        translation_use_case: "TranslateTranscriptSegmentUseCase | None",
        simplification_use_case: "SimplifyTranscriptSegmentUseCase | None",
        reply_suggestions_use_case: "GenerateReplySuggestionsUseCase | None",
        update_sink: AssistUpdateSink,
        priority_gate: LiveTranscriptionPriorityGate | None = None,
    ) -> None:
        """Initialize session-local dependencies without persistence ownership."""

        self._configuration = configuration
        self._translation_use_case = translation_use_case
        self._simplification_use_case = simplification_use_case
        self._reply_suggestions_use_case = reply_suggestions_use_case
        self._update_sink = update_sink
        self._context = RecentTranscriptContext()
        self._queue: asyncio.Queue[_QueuedAssistSegment] = asyncio.Queue(
            maxsize=configuration.max_queue_size
        )
        self._worker: asyncio.Task[None] | None = None
        self._state = AssistModeLifecycleState.STOPPED
        self._terminated = False
        self._received_segment_count = 0
        self._priority_gate = priority_gate or LiveTranscriptionPriorityGate()
        self._latest_translation_ordinal: int | None = None
        self._active_translation_ordinal: int | None = None
        self._completed_translation_durations_ms: deque[int] = deque(
            maxlen=_TRANSLATION_DURATION_HISTORY_SIZE
        )
        self._translation_only_session = (
            configuration.translation_enabled
            and not configuration.simplification_enabled
            and not configuration.reply_coaching_enabled
        )
        if configuration.translation_enabled and translation_use_case is None:
            raise ApplicationValidationError("Translation capability is unavailable.")
        if configuration.simplification_enabled and simplification_use_case is None:
            raise ApplicationValidationError(
                "Simplification capability is unavailable."
            )
        if configuration.reply_coaching_enabled and reply_suggestions_use_case is None:
            raise ApplicationValidationError(
                "Reply coaching capability is unavailable."
            )

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

        self._received_segment_count += 1
        _LOGGER.debug(
            "assist transcript received count=%d", self._received_segment_count
        )

        queued_segment = _QueuedAssistSegment(
            ordinal=self._received_segment_count,
            queued_at=time.monotonic(),
            segment=segment,
        )
        if self._configuration.translation_enabled:
            self._record_translation_pending(queued_segment.ordinal)
        dropped: _QueuedAssistSegment | None = None
        coalesced_translation_only = False
        if self._translation_only_session:
            try:
                dropped = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            else:
                self._queue.task_done()
                coalesced_translation_only = True
            self._queue.put_nowait(queued_segment)
        else:
            try:
                self._queue.put_nowait(queued_segment)
            except asyncio.QueueFull:
                dropped = self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait(queued_segment)

        _LOGGER.debug(
            "assist queue enqueued ordinal=%d depth=%d",
            queued_segment.ordinal,
            self._queue.qsize(),
        )
        emit_throughput(
            "assist_enqueued",
            ordinal=queued_segment.ordinal,
            queue_depth=self._queue.qsize(),
        )

        if dropped is not None:
            if coalesced_translation_only:
                _LOGGER.debug(
                    "assist outer queue coalesced capability=translation "
                    "replaced_count=1 depth=%d",
                    self._queue.qsize(),
                )
                emit_throughput(
                    "assist_outer_queue_coalesced",
                    capability=AssistCapability.TRANSLATION.value,
                    replaced_count=1,
                    queue_depth=self._queue.qsize(),
                )
            elif not await self._publish_dropped(dropped.segment):
                return False
        return self._state is AssistModeLifecycleState.RUNNING

    def set_upstream_pending_stt_chunks(self, pending_chunks: int) -> None:
        """Provide one bounded source-side backlog snapshot to the scheduler."""

        self._priority_gate.set_upstream_pending_stt_chunks(pending_chunks)

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
                queued_segment = await self._queue.get()
                try:
                    _LOGGER.debug(
                        "assist queue dequeued ordinal=%d queue_wait_ms=%d depth=%d",
                        queued_segment.ordinal,
                        _elapsed_milliseconds(queued_segment.queued_at),
                        self._queue.qsize(),
                    )
                    emit_throughput(
                        "assist_dequeued",
                        ordinal=queued_segment.ordinal,
                        queue_wait_ms=_elapsed_milliseconds(queued_segment.queued_at),
                        queue_depth=self._queue.qsize(),
                    )
                    await self._process_segment(queued_segment)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            raise

    async def _process_segment(self, queued_segment: _QueuedAssistSegment) -> None:
        """Enrich one persisted segment in fixed V1 priority order."""

        segment = queued_segment.segment
        processing_started_at = time.monotonic()

        try:
            self._context.add(segment)
        except Exception:
            await self._publish_generic_failure(
                segment.transcript_id,
                AssistCapability.REPLY_COACHING,
            )
            return

        if self._configuration.translation_enabled:
            translation_use_case = self._translation_use_case
            assert translation_use_case is not None
            await self._run_translation_capability(
                segment,
                ordinal=queued_segment.ordinal,
                operation=lambda: translation_use_case.execute(segment),
            )
        if self._configuration.reply_coaching_enabled:
            reply_context = self._context.build_reply_context()
            if reply_context is not None:
                reply_suggestions_use_case = self._reply_suggestions_use_case
                assert reply_suggestions_use_case is not None
                await self._run_capability(
                    segment,
                    AssistCapability.REPLY_COACHING,
                    ordinal=queued_segment.ordinal,
                    operation=lambda: reply_suggestions_use_case.execute(reply_context),
                )
        if self._configuration.simplification_enabled:
            level = self._configuration.simplification_level
            if level is not None:
                simplification_use_case = self._simplification_use_case
                assert simplification_use_case is not None
                await self._run_capability(
                    segment,
                    AssistCapability.SIMPLIFICATION,
                    ordinal=queued_segment.ordinal,
                    operation=lambda: simplification_use_case.execute(
                        segment,
                        target_level=level,
                    ),
                )

        _LOGGER.debug(
            "assist segment processing completed ordinal=%d elapsed_ms=%d",
            queued_segment.ordinal,
            _elapsed_milliseconds(processing_started_at),
        )
        emit_throughput(
            "assist_segment_completed",
            ordinal=queued_segment.ordinal,
            elapsed_ms=_elapsed_milliseconds(processing_started_at),
        )

    async def _run_capability(
        self,
        segment: TranscriptSegment,
        capability: AssistCapability,
        *,
        ordinal: int,
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
        capability_started_at = time.monotonic()
        _LOGGER.debug(
            "assist capability started capability=%s ordinal=%d",
            capability.value,
            ordinal,
        )
        emit_throughput(
            "assist_capability_started",
            ordinal=ordinal,
            capability=capability.value,
        )
        try:
            if capability is AssistCapability.TRANSLATION:
                _LOGGER.debug("assist translation started ordinal=%d", ordinal)
            update = await operation()
        except asyncio.CancelledError:
            raise
        except Exception:
            if capability is AssistCapability.TRANSLATION:
                _LOGGER.debug("assist translation failed ordinal=%d", ordinal)
                emit_throughput(
                    "assist_translation_update_failed",
                    ordinal=ordinal,
                )
            _LOGGER.debug(
                "assist capability completed capability=%s ordinal=%d "
                "outcome=failed elapsed_ms=%d",
                capability.value,
                ordinal,
                _elapsed_milliseconds(capability_started_at),
            )
            emit_throughput(
                "assist_capability_completed",
                ordinal=ordinal,
                capability=capability.value,
                outcome="failed",
                elapsed_ms=_elapsed_milliseconds(capability_started_at),
            )
            await self._publish_generic_failure(segment.transcript_id, capability)
            return
        if capability is AssistCapability.TRANSLATION:
            if update.state is AssistState.READY:
                _LOGGER.debug("assist translation completed ordinal=%d", ordinal)
            elif update.state is AssistState.UNAVAILABLE:
                _LOGGER.debug("assist translation unavailable ordinal=%d", ordinal)
            else:
                _LOGGER.debug("assist translation failed ordinal=%d", ordinal)
        _LOGGER.debug(
            "assist capability completed capability=%s ordinal=%d outcome=%s "
            "elapsed_ms=%d",
            capability.value,
            ordinal,
            update.state.value,
            _elapsed_milliseconds(capability_started_at),
        )
        emit_throughput(
            "assist_capability_completed",
            ordinal=ordinal,
            capability=capability.value,
            outcome=update.state.value,
            elapsed_ms=_elapsed_milliseconds(capability_started_at),
        )
        if self._state is AssistModeLifecycleState.RUNNING:
            published = await self._publish(update)
            if published and capability is AssistCapability.TRANSLATION:
                if update.state is AssistState.READY:
                    emit_throughput(
                        "assist_translation_update_ready",
                        ordinal=ordinal,
                    )
                elif update.state is AssistState.FAILED:
                    emit_throughput(
                        "assist_translation_update_failed",
                        ordinal=ordinal,
                    )

    async def _run_translation_capability(
        self,
        segment: TranscriptSegment,
        *,
        ordinal: int,
        operation: Callable[[], Awaitable[AssistUpdate]],
    ) -> None:
        """Run only the newest translation when it cannot delay active STT."""

        if ordinal != self._latest_translation_ordinal:
            await self._publish_translation_unavailable(segment.transcript_id, ordinal)
            self._emit_translation_scheduler("coalesced")
            return

        if self._priority_gate.active_stt_count > 0:
            self._emit_translation_scheduler("busy")
        estimated_translation_duration_ms = self._estimated_translation_duration_ms()
        backlog_deferred_reported = False
        if self._priority_gate.upstream_pending_stt_chunks > 0:
            self._emit_translation_scheduler(
                "deferred",
                admission=TranslationAdmission(
                    reason=TranslationAdmissionReason.UPSTREAM_BACKLOG,
                    idle_window_ms=0,
                    wait_seconds=0.0,
                ),
                estimated_translation_duration_ms=estimated_translation_duration_ms,
            )
            backlog_deferred_reported = True
        admission = await self._priority_gate.wait_for_translation_admission(
            estimated_translation_duration_ms=estimated_translation_duration_ms
        )
        if admission.was_deferred and not (
            backlog_deferred_reported
            and admission.deferred_reason is TranslationAdmissionReason.UPSTREAM_BACKLOG
        ):
            self._emit_translation_scheduler(
                "deferred",
                admission=admission,
                estimated_translation_duration_ms=estimated_translation_duration_ms,
            )
        if self._state is not AssistModeLifecycleState.RUNNING:
            return
        if ordinal != self._latest_translation_ordinal:
            await self._publish_translation_unavailable(segment.transcript_id, ordinal)
            self._emit_translation_scheduler("coalesced")
            return

        stt_start_generation = self._priority_gate.stt_start_generation
        if not await self._publish(
            AssistUpdate(
                transcript_id=segment.transcript_id,
                capability=AssistCapability.TRANSLATION,
                state=AssistState.PROCESSING,
            )
        ):
            return

        # Publishing is an await point.  Recheck after it so an STT call that
        # begins while the processing update is in flight always wins before
        # local Ollama work is allowed to start.
        if (
            self._priority_gate.active_stt_count > 0
            or self._priority_gate.stt_start_generation != stt_start_generation
        ):
            await self._publish_translation_unavailable(segment.transcript_id, ordinal)
            if self._latest_translation_ordinal == ordinal:
                self._latest_translation_ordinal = None
            self._emit_translation_scheduler("cancelled")
            return
        if ordinal != self._latest_translation_ordinal:
            await self._publish_translation_unavailable(segment.transcript_id, ordinal)
            self._emit_translation_scheduler("coalesced")
            return

        self._active_translation_ordinal = ordinal
        self._emit_translation_scheduler(
            "started",
            admission=admission,
            estimated_translation_duration_ms=estimated_translation_duration_ms,
        )
        capability_started_at = time.monotonic()
        _LOGGER.debug("assist translation started ordinal=%d", ordinal)
        emit_throughput(
            "assist_capability_started",
            ordinal=ordinal,
            capability=AssistCapability.TRANSLATION.value,
        )
        operation_task: asyncio.Task[object] = asyncio.create_task(
            _await_assist_operation(operation)
        )
        stt_started_task: asyncio.Task[object] = asyncio.create_task(
            self._priority_gate.wait_for_stt_start_after(stt_start_generation)
        )
        scheduler_terminal_state: str | None = None
        try:
            completed, _ = await asyncio.wait(
                {operation_task, stt_started_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if operation_task in completed:
                stt_started_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stt_started_task
                try:
                    update = operation_task.result()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await self._publish_translation_failure(
                        segment.transcript_id,
                        ordinal,
                        capability_started_at,
                    )
                    scheduler_terminal_state = "completed"
                    return
                if not isinstance(update, AssistUpdate):
                    await self._publish_translation_failure(
                        segment.transcript_id,
                        ordinal,
                        capability_started_at,
                    )
                    scheduler_terminal_state = "completed"
                    return
                await self._publish_translation_result(
                    update,
                    ordinal=ordinal,
                    started_at=capability_started_at,
                )
                scheduler_terminal_state = "completed"
                return

            operation_task.cancel()
            with suppress(asyncio.CancelledError):
                await operation_task
            await self._publish_translation_unavailable(segment.transcript_id, ordinal)
            scheduler_terminal_state = "cancelled"
        except asyncio.CancelledError:
            operation_task.cancel()
            stt_started_task.cancel()
            with suppress(asyncio.CancelledError):
                await operation_task
            with suppress(asyncio.CancelledError):
                await stt_started_task
            scheduler_terminal_state = "cancelled"
            raise
        finally:
            self._active_translation_ordinal = None
            if self._latest_translation_ordinal == ordinal:
                self._latest_translation_ordinal = None
            if scheduler_terminal_state is not None:
                self._emit_translation_scheduler(scheduler_terminal_state)

    async def _publish_translation_result(
        self,
        update: AssistUpdate,
        *,
        ordinal: int,
        started_at: float,
    ) -> None:
        """Publish one terminal translation state after the task completes."""

        elapsed_ms = _elapsed_milliseconds(started_at)
        if update.state is AssistState.READY:
            self._completed_translation_durations_ms.append(elapsed_ms)
        _LOGGER.debug(
            "assist translation completed ordinal=%d outcome=%s elapsed_ms=%d",
            ordinal,
            update.state.value,
            elapsed_ms,
        )
        emit_throughput(
            "assist_capability_completed",
            ordinal=ordinal,
            capability=AssistCapability.TRANSLATION.value,
            outcome=update.state.value,
            elapsed_ms=elapsed_ms,
        )
        if self._state is AssistModeLifecycleState.RUNNING and await self._publish(
            update
        ):
            if update.state is AssistState.READY:
                emit_throughput("assist_translation_update_ready", ordinal=ordinal)
            elif update.state is AssistState.FAILED:
                emit_throughput("assist_translation_update_failed", ordinal=ordinal)

    async def _publish_translation_failure(
        self,
        transcript_id: UUID,
        ordinal: int,
        started_at: float,
    ) -> None:
        """Keep unexpected translation failures generic and session-scoped."""

        emit_throughput(
            "assist_capability_completed",
            ordinal=ordinal,
            capability=AssistCapability.TRANSLATION.value,
            outcome="failed",
            elapsed_ms=_elapsed_milliseconds(started_at),
        )
        await self._publish_generic_failure(transcript_id, AssistCapability.TRANSLATION)
        emit_throughput("assist_translation_update_failed", ordinal=ordinal)

    async def _publish_translation_unavailable(
        self,
        transcript_id: UUID,
        ordinal: int,
    ) -> None:
        """Publish a terminal best-effort state without attaching stale content."""

        if self._state is AssistModeLifecycleState.RUNNING:
            await self._publish(
                AssistUpdate(
                    transcript_id=transcript_id,
                    capability=AssistCapability.TRANSLATION,
                    state=AssistState.UNAVAILABLE,
                )
            )

    def _record_translation_pending(self, ordinal: int) -> None:
        """Keep one logical newest translation pending while retaining other work."""

        had_pending = (
            self._latest_translation_ordinal is not None
            and self._latest_translation_ordinal != self._active_translation_ordinal
        )
        self._latest_translation_ordinal = ordinal
        self._emit_translation_scheduler("coalesced" if had_pending else "pending")

    def _estimated_translation_duration_ms(self) -> int | None:
        """Use the slowest recent successful local translation as a safe estimate."""

        if not self._completed_translation_durations_ms:
            return None
        return max(self._completed_translation_durations_ms)

    def _emit_translation_scheduler(
        self,
        state: str,
        *,
        admission: TranslationAdmission | None = None,
        estimated_translation_duration_ms: int | None = None,
    ) -> None:
        """Emit bounded scheduler state without transcript or provider content."""

        pending_count = int(
            self._latest_translation_ordinal is not None
            and self._latest_translation_ordinal != self._active_translation_ordinal
        )
        fields: dict[str, int | str] = {
            "state": state,
            "active_translation_count": int(
                self._active_translation_ordinal is not None
            ),
            "pending_translation_count": pending_count,
            "upstream_pending_stt_chunks": (
                self._priority_gate.upstream_pending_stt_chunks
            ),
        }
        if admission is not None:
            if state == "deferred" and admission.deferred_reason is not None:
                fields["admission_reason"] = admission.deferred_reason.value
                fields["estimated_idle_window_ms"] = admission.deferred_idle_window_ms
            else:
                fields["admission_reason"] = admission.reason.value
                fields["estimated_idle_window_ms"] = admission.idle_window_ms
            fields["extended_idle_margin_ms"] = admission.extended_idle_margin_ms
        if estimated_translation_duration_ms is not None:
            fields["estimated_translation_duration_ms"] = (
                estimated_translation_duration_ms
            )
        emit_throughput("assist_translation_scheduler", **fields)

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


def _elapsed_milliseconds(started_at: float) -> int:
    """Return a privacy-safe monotonic duration for runtime diagnostics."""

    return int((time.monotonic() - started_at) * 1_000)


async def _await_assist_operation(
    operation: Callable[[], Awaitable[AssistUpdate]],
) -> object:
    """Give heterogeneous scheduler tasks one non-sensitive common type."""

    return await operation()
