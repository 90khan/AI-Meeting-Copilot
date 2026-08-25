"""Tests for the session-scoped Assist Mode enrichment orchestrator."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import app.application.services.assist_mode_orchestrator as assist_mode_module
import pytest
from app.application.dto.ai import GermanLevel, ReplySuggestion, ReplyTone
from app.application.dto.assist_mode import (
    AssistCapability,
    AssistState,
    AssistUpdate,
    ReplyContext,
    TranscriptSegment,
)
from app.application.dto.live_transcription import AudioSource
from app.application.exceptions import ApplicationValidationError
from app.application.services import (
    AssistModeConfiguration,
    AssistModeLifecycleState,
    AssistModeOrchestrator,
    LiveTranscriptionPriorityGate,
)
from app.domain.value_objects import MeetingId


def _segment(index: int) -> TranscriptSegment:
    return TranscriptSegment(
        transcript_id=uuid4(),
        meeting_id=MeetingId.new(),
        text=f"segment {index}",
        timestamp=datetime(2026, 8, 2, 10, 0, tzinfo=UTC) + timedelta(seconds=index),
        source=AudioSource.MIXED,
        speaker="Unknown",
    )


def _ready_update(
    segment: TranscriptSegment,
    capability: AssistCapability,
) -> AssistUpdate:
    if capability is AssistCapability.TRANSLATION:
        return AssistUpdate(
            transcript_id=segment.transcript_id,
            capability=capability,
            state=AssistState.READY,
            translated_text="çeviri",
        )
    if capability is AssistCapability.SIMPLIFICATION:
        return AssistUpdate(
            transcript_id=segment.transcript_id,
            capability=capability,
            state=AssistState.READY,
            simplified_text="vereinfachter Text",
        )
    return AssistUpdate(
        transcript_id=segment.transcript_id,
        capability=capability,
        state=AssistState.READY,
        reply_suggestions=(
            ReplySuggestion(text="Eine kurze Antwort.", tone=ReplyTone.PROFESSIONAL),
        ),
    )


class _FakeTranslationUseCase:
    def __init__(
        self,
        *,
        calls: list[str],
        error: Exception | None = None,
        gate: asyncio.Event | None = None,
        started: asyncio.Event | None = None,
        cancelled: asyncio.Event | None = None,
    ) -> None:
        self.calls = calls
        self.error = error
        self.gate = gate
        self.started = started
        self.cancelled = cancelled

    async def execute(self, segment: TranscriptSegment) -> AssistUpdate:
        self.calls.append("translation")
        if self.started is not None:
            self.started.set()
        if self.gate is not None:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                if self.cancelled is not None:
                    self.cancelled.set()
                raise
        if self.error is not None:
            raise self.error
        return _ready_update(segment, AssistCapability.TRANSLATION)


class _FakeSimplificationUseCase:
    def __init__(self, *, calls: list[str]) -> None:
        self.calls = calls

    async def execute(
        self,
        segment: TranscriptSegment,
        *,
        target_level: GermanLevel,
    ) -> AssistUpdate:
        self.calls.append(f"simplification:{target_level.value}")
        return _ready_update(segment, AssistCapability.SIMPLIFICATION)


class _FakeReplyUseCase:
    def __init__(self, *, calls: list[str]) -> None:
        self.calls = calls
        self.contexts: list[ReplyContext] = []

    async def execute(self, context: ReplyContext) -> AssistUpdate:
        self.calls.append("reply")
        self.contexts.append(context)
        return _ready_update(
            context.newest_finalized_segment,
            AssistCapability.REPLY_COACHING,
        )


class _FakeSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.updates: list[AssistUpdate] = []
        self.fail = fail

    async def publish(self, update: AssistUpdate) -> None:
        if self.fail:
            raise RuntimeError("sink failed")
        self.updates.append(update)


def _orchestrator(
    *,
    configuration: AssistModeConfiguration | None = None,
    calls: list[str] | None = None,
    translation_error: Exception | None = None,
    translation_gate: asyncio.Event | None = None,
    translation_started: asyncio.Event | None = None,
    translation_cancelled: asyncio.Event | None = None,
    priority_gate: LiveTranscriptionPriorityGate | None = None,
    sink: _FakeSink | None = None,
) -> tuple[AssistModeOrchestrator, _FakeReplyUseCase, _FakeSink, list[str]]:
    recorded_calls = calls if calls is not None else []
    reply = _FakeReplyUseCase(calls=recorded_calls)
    update_sink = sink if sink is not None else _FakeSink()
    return (
        AssistModeOrchestrator(
            configuration=configuration or AssistModeConfiguration(),
            translation_use_case=_FakeTranslationUseCase(
                calls=recorded_calls,
                error=translation_error,
                gate=translation_gate,
                started=translation_started,
                cancelled=translation_cancelled,
            ),
            simplification_use_case=_FakeSimplificationUseCase(calls=recorded_calls),
            reply_suggestions_use_case=reply,
            update_sink=update_sink,
            priority_gate=priority_gate,
        ),
        reply,
        update_sink,
        recorded_calls,
    )


async def _wait_until(predicate: Callable[[], bool]) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("Timed out waiting for asynchronous work.")


def test_configuration_validates_v1_simplification_and_queue_limits() -> None:
    """Configuration only permits bounded queues and B1/B2 simplification."""

    with pytest.raises(ApplicationValidationError, match="Simplification level"):
        AssistModeConfiguration(simplification_enabled=True)
    with pytest.raises(ApplicationValidationError, match="B1 or B2"):
        AssistModeConfiguration(
            simplification_enabled=True,
            simplification_level=GermanLevel.C1,
        )
    with pytest.raises(ApplicationValidationError, match="queue size"):
        AssistModeConfiguration(max_queue_size=0)


def test_lifecycle_is_idempotent_and_enqueue_requires_a_running_session() -> None:
    """The worker is created once and cannot accept work outside its lifecycle."""

    async def run() -> None:
        orchestrator, _, _, _ = _orchestrator()
        with pytest.raises(RuntimeError, match="not running"):
            await orchestrator.enqueue(_segment(1))

        await orchestrator.start()
        worker = orchestrator._worker
        await orchestrator.start()
        assert orchestrator.state is AssistModeLifecycleState.RUNNING
        assert orchestrator._worker is worker

        await orchestrator.stop()
        await orchestrator.stop()
        assert orchestrator.state is AssistModeLifecycleState.STOPPED
        with pytest.raises(RuntimeError, match="not running"):
            await orchestrator.enqueue(_segment(2))

    asyncio.run(run())


def test_processing_priority_updates_and_context_are_sequential() -> None:
    """A segment follows translation, reply coaching, then simplification."""

    async def run() -> None:
        configuration = AssistModeConfiguration(
            simplification_enabled=True,
            simplification_level=GermanLevel.B1,
        )
        orchestrator, reply, sink, calls = _orchestrator(configuration=configuration)
        first = _segment(1)
        second = _segment(2)
        await orchestrator.start()
        await orchestrator.enqueue(first)
        await _wait_until(lambda: len(sink.updates) == 6)
        await orchestrator.enqueue(second)
        await _wait_until(lambda: len(sink.updates) == 12)

        assert calls == [
            "translation",
            "reply",
            "simplification:b1",
            "translation",
            "reply",
            "simplification:b1",
        ]
        assert [(update.capability, update.state) for update in sink.updates[:6]] == [
            (AssistCapability.TRANSLATION, AssistState.PROCESSING),
            (AssistCapability.TRANSLATION, AssistState.READY),
            (AssistCapability.REPLY_COACHING, AssistState.PROCESSING),
            (AssistCapability.REPLY_COACHING, AssistState.READY),
            (AssistCapability.SIMPLIFICATION, AssistState.PROCESSING),
            (AssistCapability.SIMPLIFICATION, AssistState.READY),
        ]
        assert reply.contexts[-1].newest_finalized_segment is second
        assert reply.contexts[-1].previous_finalized_segments == (first,)
        await orchestrator.stop()

    asyncio.run(run())


def test_disabled_capabilities_are_not_called() -> None:
    """Disabled capabilities create neither work nor state updates."""

    async def run() -> None:
        orchestrator, _, sink, calls = _orchestrator(
            configuration=AssistModeConfiguration(
                translation_enabled=False,
                reply_coaching_enabled=False,
            )
        )
        await orchestrator.start()
        await orchestrator.enqueue(_segment(1))
        await _wait_until(lambda: orchestrator._queue.empty())
        await asyncio.sleep(0)

        assert calls == []
        assert sink.updates == []
        await orchestrator.stop()

    asyncio.run(run())


def test_full_queue_drops_oldest_pending_segment_and_preserves_newest() -> None:
    """Overload reports a generic unavailable update and retains the newest work."""

    async def run() -> None:
        gate = asyncio.Event()
        started = asyncio.Event()
        configuration = AssistModeConfiguration(
            reply_coaching_enabled=True,
            max_queue_size=1,
        )
        orchestrator, _, sink, calls = _orchestrator(
            configuration=configuration,
            translation_gate=gate,
            translation_started=started,
        )
        first, dropped, newest = _segment(1), _segment(2), _segment(3)
        await orchestrator.start()
        await orchestrator.enqueue(first)
        await started.wait()
        await orchestrator.enqueue(dropped)
        await orchestrator.enqueue(newest)

        await _wait_until(
            lambda: any(
                update.transcript_id == dropped.transcript_id
                and update.state is AssistState.UNAVAILABLE
                for update in sink.updates
            )
        )
        gate.set()
        await _wait_until(
            lambda: any(
                update.transcript_id == newest.transcript_id
                and update.state is AssistState.READY
                for update in sink.updates
            )
        )

        assert not any(
            update.transcript_id == dropped.transcript_id
            and update.state is AssistState.PROCESSING
            for update in sink.updates
        )
        assert any(
            update.transcript_id == newest.transcript_id
            and update.state is AssistState.READY
            for update in sink.updates
        )
        assert calls == ["translation", "reply", "translation", "reply"]
        await orchestrator.stop()

    asyncio.run(run())


def test_stt_priority_defers_and_preempts_best_effort_translation() -> None:
    """An active or newly-started STT call never waits for Ollama work."""

    async def run() -> None:
        translation_gate = asyncio.Event()
        translation_started = asyncio.Event()
        translation_cancelled = asyncio.Event()
        priority_gate = LiveTranscriptionPriorityGate()
        orchestrator, _, sink, calls = _orchestrator(
            configuration=AssistModeConfiguration(reply_coaching_enabled=False),
            translation_gate=translation_gate,
            translation_started=translation_started,
            translation_cancelled=translation_cancelled,
            priority_gate=priority_gate,
        )
        priority_gate.stt_started()
        await orchestrator.start()

        assert await orchestrator.enqueue(_segment(1)) is True
        await asyncio.sleep(0)
        assert calls == []
        assert sink.updates == []

        priority_gate.stt_finished()
        await translation_started.wait()
        priority_gate.stt_started()
        await translation_cancelled.wait()
        await _wait_until(
            lambda: any(
                update.state is AssistState.UNAVAILABLE for update in sink.updates
            )
        )

        assert calls == ["translation"]
        await orchestrator.stop()

    asyncio.run(run())


def test_upstream_backlog_defers_the_latest_translation_until_it_clears() -> None:
    """A source-side STT backlog cannot be mistaken for scheduler idle capacity."""

    async def run() -> None:
        translation_started = asyncio.Event()
        priority_gate = LiveTranscriptionPriorityGate()
        orchestrator, _, _, calls = _orchestrator(
            configuration=AssistModeConfiguration(reply_coaching_enabled=False),
            translation_started=translation_started,
            priority_gate=priority_gate,
        )
        priority_gate.set_upstream_pending_stt_chunks(2)
        await orchestrator.start()
        await orchestrator.enqueue(_segment(1))
        await asyncio.sleep(0)

        assert calls == []
        assert priority_gate.upstream_pending_stt_chunks == 2

        priority_gate.set_upstream_pending_stt_chunks(0)
        await translation_started.wait()
        assert calls == ["translation"]
        await orchestrator.stop()

    asyncio.run(run())


def test_slow_translation_coalesces_to_one_latest_pending_segment() -> None:
    """A slow provider has one active translation and one logical pending item."""

    async def run() -> None:
        translation_gate = asyncio.Event()
        translation_started = asyncio.Event()
        priority_gate = LiveTranscriptionPriorityGate()
        orchestrator, _, sink, calls = _orchestrator(
            configuration=AssistModeConfiguration(reply_coaching_enabled=False),
            translation_gate=translation_gate,
            translation_started=translation_started,
            priority_gate=priority_gate,
        )
        first, superseded, newest = _segment(1), _segment(2), _segment(3)
        await orchestrator.start()
        await orchestrator.enqueue(first)
        await translation_started.wait()
        await orchestrator.enqueue(superseded)
        await orchestrator.enqueue(newest)

        translation_gate.set()
        await _wait_until(
            lambda: any(
                update.transcript_id == newest.transcript_id
                and update.state is AssistState.READY
                for update in sink.updates
            )
        )

        assert calls == ["translation", "translation"]
        assert not any(
            update.transcript_id == superseded.transcript_id for update in sink.updates
        )
        assert not any(
            update.transcript_id == superseded.transcript_id
            and update.state is AssistState.READY
            for update in sink.updates
        )
        await orchestrator.stop()

    asyncio.run(run())


def test_translation_only_outer_queue_keeps_only_the_latest_waiting_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Translation-only bursts never build a stale generic Assist backlog."""

    events: list[tuple[str, dict[str, int | str]]] = []

    def capture(stage: str, /, **fields: int | str) -> None:
        events.append((stage, fields))

    monkeypatch.setattr(assist_mode_module, "emit_throughput", capture)

    async def run() -> None:
        priority_gate = LiveTranscriptionPriorityGate()
        orchestrator, _, sink, calls = _orchestrator(
            configuration=AssistModeConfiguration(reply_coaching_enabled=False),
            priority_gate=priority_gate,
        )
        segments = tuple(_segment(index) for index in range(10))
        priority_gate.stt_started()
        await orchestrator.start()
        await orchestrator.enqueue(segments[0])
        await asyncio.sleep(0)
        for segment in segments[1:]:
            await orchestrator.enqueue(segment)

        assert orchestrator._queue.qsize() == 1
        assert orchestrator._latest_translation_ordinal == len(segments)
        assert calls == []

        priority_gate.stt_finished()
        await _wait_until(
            lambda: any(
                update.transcript_id == segments[-1].transcript_id
                and update.state is AssistState.READY
                for update in sink.updates
            )
        )

        assert calls == ["translation"]
        assert not any(
            update.transcript_id != segments[-1].transcript_id
            and update.state is AssistState.READY
            for update in sink.updates
        )
        await orchestrator.stop()

    asyncio.run(run())
    coalesced_events = [
        fields for stage, fields in events if stage == "assist_outer_queue_coalesced"
    ]
    assert len(coalesced_events) == len(range(10)) - 2
    assert all(
        fields
        == {
            "capability": "translation",
            "replaced_count": 1,
            "queue_depth": 1,
        }
        for fields in coalesced_events
    )


def test_translation_only_coalescing_does_not_change_reply_coaching_order() -> None:
    """Mixed-capability sessions retain their existing segment FIFO semantics."""

    async def run() -> None:
        priority_gate = LiveTranscriptionPriorityGate()
        orchestrator, reply, _, _ = _orchestrator(priority_gate=priority_gate)
        first, second, third = (_segment(index) for index in range(3))
        priority_gate.stt_started()
        await orchestrator.start()
        await orchestrator.enqueue(first)
        await asyncio.sleep(0)
        await orchestrator.enqueue(second)
        await orchestrator.enqueue(third)

        assert orchestrator._queue.qsize() == 2

        priority_gate.stt_finished()
        await _wait_until(lambda: len(reply.contexts) == 3)

        assert [
            context.newest_finalized_segment.transcript_id for context in reply.contexts
        ] == [first.transcript_id, second.transcript_id, third.transcript_id]
        await orchestrator.stop()

    asyncio.run(run())


def test_translation_only_coalesced_work_does_not_prevent_shutdown() -> None:
    """The one-slot pending mailbox is cleared by the existing stop lifecycle."""

    async def run() -> None:
        priority_gate = LiveTranscriptionPriorityGate()
        orchestrator, _, sink, calls = _orchestrator(
            configuration=AssistModeConfiguration(reply_coaching_enabled=False),
            priority_gate=priority_gate,
        )
        priority_gate.stt_started()
        await orchestrator.start()
        await orchestrator.enqueue(_segment(1))
        await asyncio.sleep(0)
        await orchestrator.enqueue(_segment(2))
        await orchestrator.enqueue(_segment(3))

        await orchestrator.stop()

        assert orchestrator.state is AssistModeLifecycleState.STOPPED
        assert orchestrator._queue.empty() is True
        assert calls == []
        assert sink.updates == []

    asyncio.run(run())


def test_use_case_failure_is_isolated_and_later_capabilities_continue() -> None:
    """Unexpected enrichment errors become generic failures without blocking reply."""

    async def run() -> None:
        orchestrator, _, sink, calls = _orchestrator(
            translation_error=RuntimeError("provider detail")
        )
        segment = _segment(1)
        await orchestrator.start()
        await orchestrator.enqueue(segment)
        await _wait_until(lambda: len(sink.updates) == 4)

        assert calls == ["translation", "reply"]
        assert [update.state for update in sink.updates] == [
            AssistState.PROCESSING,
            AssistState.FAILED,
            AssistState.PROCESSING,
            AssistState.READY,
        ]
        assert all("provider detail" not in str(update) for update in sink.updates)
        await orchestrator.stop()

    asyncio.run(run())


def test_sink_failure_marks_the_session_failed_and_prevents_future_work() -> None:
    """A transport sink failure terminates only transient Assist Mode work."""

    async def run() -> None:
        sink = _FakeSink(fail=True)
        orchestrator, _, _, _ = _orchestrator(sink=sink)
        await orchestrator.start()
        await orchestrator.enqueue(_segment(1))
        await _wait_until(lambda: orchestrator.state is AssistModeLifecycleState.FAILED)

        with pytest.raises(RuntimeError, match="not running"):
            await orchestrator.enqueue(_segment(2))
        await orchestrator.stop()
        assert orchestrator.state is AssistModeLifecycleState.FAILED

    asyncio.run(run())


def test_stop_cancels_pending_work_clears_context_and_allows_no_late_updates() -> None:
    """Stopping cancels an in-flight provider and discards queued session work."""

    async def run() -> None:
        gate = asyncio.Event()
        started = asyncio.Event()
        orchestrator, _, sink, _ = _orchestrator(
            translation_gate=gate,
            translation_started=started,
        )
        await orchestrator.start()
        await orchestrator.enqueue(_segment(1))
        await started.wait()
        await orchestrator.enqueue(_segment(2))

        await orchestrator.stop()
        update_count = len(sink.updates)
        await asyncio.sleep(0)

        assert orchestrator.state is AssistModeLifecycleState.STOPPED
        assert orchestrator._context.is_empty() is True
        assert orchestrator._queue.empty() is True
        assert len(sink.updates) == update_count

    asyncio.run(run())


def test_assist_metrics_use_only_internal_ordinals_and_closed_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queue and capability timing never include transcript IDs or text."""

    events: list[tuple[str, dict[str, int | str]]] = []

    def capture(stage: str, /, **fields: int | str) -> None:
        events.append((stage, fields))

    monkeypatch.setattr(assist_mode_module, "emit_throughput", capture)

    async def run() -> None:
        orchestrator, _, sink, _ = _orchestrator(
            configuration=AssistModeConfiguration(reply_coaching_enabled=False)
        )
        await orchestrator.start()
        await orchestrator.enqueue(_segment(1))
        await _wait_until(lambda: len(sink.updates) == 2)
        await orchestrator.stop()

    asyncio.run(run())

    assert events[0] == (
        "assist_translation_scheduler",
        {
            "state": "pending",
            "active_translation_count": 0,
            "pending_translation_count": 1,
            "upstream_pending_stt_chunks": 0,
        },
    )
    assert events[1] == ("assist_enqueued", {"ordinal": 1, "queue_depth": 1})
    started_fields = events[3]
    assert started_fields[0] == "assist_translation_scheduler"
    assert started_fields[1] == {
        "state": "started",
        "active_translation_count": 1,
        "pending_translation_count": 0,
        "upstream_pending_stt_chunks": 0,
        "admission_reason": "probe",
        "estimated_idle_window_ms": 0,
        "extended_idle_margin_ms": 0,
    }
    assert events[4] == (
        "assist_capability_started",
        {"ordinal": 1, "capability": "translation"},
    )
    assert any(stage == "assist_translation_update_ready" for stage, _ in events)
    assert any(
        stage == "assist_translation_scheduler" and fields["state"] == "completed"
        for stage, fields in events
    )
    segment_completed = next(
        fields for stage, fields in events if stage == "assist_segment_completed"
    )
    assert isinstance(segment_completed["elapsed_ms"], int)
    assert segment_completed["elapsed_ms"] >= 0
    assert all(
        "transcript_id" not in fields and "text" not in fields for _, fields in events
    )
