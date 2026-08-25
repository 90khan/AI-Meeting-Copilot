"""Tests for cooperative STT priority state."""

import asyncio

import pytest
from app.application.services import LiveTranscriptionPriorityGate
from app.application.services.live_transcription_priority_gate import (
    TranslationAdmissionReason,
)


def test_gate_tracks_stt_activity_and_wakes_idle_waiters() -> None:
    """The gate uses bounded counters and no timing or content data."""

    async def run() -> None:
        gate = LiveTranscriptionPriorityGate()
        gate.stt_started()
        gate.stt_started()
        assert gate.active_stt_count == 2
        generation = gate.stt_start_generation
        waiter = asyncio.create_task(gate.wait_until_idle())
        assert waiter.done() is False

        gate.stt_finished()
        assert waiter.done() is False
        gate.stt_finished()
        await waiter
        assert gate.active_stt_count == 0
        assert generation == 2

    asyncio.run(run())


def test_gate_wakes_a_translation_preemption_waiter_for_a_new_stt_start() -> None:
    """A new STT request is observable without timers or provider data."""

    async def run() -> None:
        gate = LiveTranscriptionPriorityGate()
        generation = gate.stt_start_generation
        waiter = asyncio.create_task(gate.wait_for_stt_start_after(generation))

        gate.stt_started()
        await waiter

        assert gate.active_stt_count == 1
        gate.stt_finished()

    asyncio.run(run())


def test_admission_uses_only_observed_stt_start_cadence() -> None:
    """A translation is deferred when its measured duration cannot fit."""

    now = 0.0

    def clock() -> float:
        return now

    gate = LiveTranscriptionPriorityGate(clock=clock)
    gate.stt_started()
    gate.stt_finished()
    now = 3.0
    gate.stt_started()
    gate.stt_finished()

    now = 5.0
    deferred = gate.translation_admission(estimated_translation_duration_ms=1_500)
    assert deferred is not None
    assert deferred.reason is TranslationAdmissionReason.PREDICTED_WINDOW
    assert deferred.idle_window_ms == 1_000
    assert deferred.wait_seconds == 2.5

    now = 4.0
    admitted = gate.translation_admission(estimated_translation_duration_ms=1_500)
    assert admitted is not None
    assert admitted.reason is TranslationAdmissionReason.PREDICTED_WINDOW
    assert admitted.idle_window_ms == 2_000
    assert admitted.wait_seconds == 0.0

    now = 7.6
    extended_idle = gate.translation_admission(estimated_translation_duration_ms=1_500)
    assert extended_idle is not None
    assert extended_idle.reason is TranslationAdmissionReason.EXTENDED_IDLE
    assert extended_idle.wait_seconds == 0.0


def test_extended_idle_requires_the_observed_cadence_jitter_margin() -> None:
    """Passing only the earliest expected boundary cannot admit translation."""

    now = 0.0

    def clock() -> float:
        return now

    gate = LiveTranscriptionPriorityGate(clock=clock)
    gate.stt_started()
    gate.stt_finished()
    now = 3.0
    gate.stt_started()
    gate.stt_finished()
    now = 6.2
    gate.stt_started()
    gate.stt_finished()

    now = 8.5
    deferred = gate.translation_admission(estimated_translation_duration_ms=1_000)
    assert deferred is not None
    assert deferred.reason is TranslationAdmissionReason.PREDICTED_WINDOW
    assert deferred.idle_window_ms in {699, 700}
    assert deferred.wait_seconds == pytest.approx(1.9)
    assert deferred.extended_idle_margin_ms == 1_000

    now = 9.25
    still_deferred = gate.translation_admission(estimated_translation_duration_ms=1_000)
    assert still_deferred is not None
    assert still_deferred.reason is TranslationAdmissionReason.PREDICTED_WINDOW
    assert still_deferred.wait_seconds == pytest.approx(1.15)

    now = 10.41
    extended_idle = gate.translation_admission(estimated_translation_duration_ms=1_000)
    assert extended_idle is not None
    assert extended_idle.reason is TranslationAdmissionReason.EXTENDED_IDLE
    assert extended_idle.extended_idle_margin_ms == 1_000


def test_upstream_backlog_suppresses_otherwise_safe_translation_admission() -> None:
    """Queued source audio is never mistaken for a genuine extended idle period."""

    now = 0.0

    def clock() -> float:
        return now

    gate = LiveTranscriptionPriorityGate(clock=clock)
    gate.stt_started()
    gate.stt_finished()
    now = 3.0
    gate.stt_started()
    gate.stt_finished()

    now = 7.6
    assert (
        gate.translation_admission(estimated_translation_duration_ms=1_500).reason
        is TranslationAdmissionReason.EXTENDED_IDLE
    )

    gate.set_upstream_pending_stt_chunks(3)
    deferred = gate.translation_admission(estimated_translation_duration_ms=1_500)
    assert deferred is not None
    assert deferred.reason is TranslationAdmissionReason.UPSTREAM_BACKLOG
    assert deferred.wait_seconds == 0.0

    gate.set_upstream_pending_stt_chunks(0)
    admitted = gate.translation_admission(estimated_translation_duration_ms=1_500)
    assert admitted is not None
    assert admitted.reason is TranslationAdmissionReason.EXTENDED_IDLE
