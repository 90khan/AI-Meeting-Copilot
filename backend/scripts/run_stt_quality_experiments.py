"""Run private, offline Quality L Faster-Whisper prompt/context experiments."""

from __future__ import annotations

import argparse
import asyncio
import resource
import sys
import time
import wave
from collections.abc import Awaitable, Callable
from io import BytesIO
from pathlib import Path

from app.application.dto.ai import (
    AudioFormat,
    AudioInput,
    TranscriptionResult,
)
from app.core.config import Settings, get_settings
from app.development.quality_baseline import load_quality_reference_fixture
from app.development.stt_quality_experiments import (
    EXPERIMENT_DEFINITIONS,
    QUALITY_M_EXPERIMENT_DEFINITIONS,
    OfflineTranscription,
    SttExperimentDefinition,
    render_stt_quality_report,
    run_stt_experiments,
)
from app.infrastructure.providers.faster_whisper import FasterWhisperModelManager
from app.infrastructure.providers.faster_whisper.speech_to_text import (
    FasterWhisperSpeechToTextProvider,
)

_LIVE_SAMPLE_RATE_HZ = 16_000
_LIVE_CHANNELS = 1
_LIVE_SAMPLE_WIDTH_BYTES = 2
_LIVE_CHUNK_SECONDS = 4
_LIVE_CHUNK_OVERLAP_SECONDS = 1
_PROGRESS_INTERVAL_CHUNKS = 50


class _OfflineFasterWhisperTranscriber:
    """Direct, local-only model invocation with production-equivalent defaults."""

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._manager = FasterWhisperModelManager(
            model_name=settings.faster_whisper_model,
            device=settings.faster_whisper_device,
            compute_type=settings.faster_whisper_compute_type,
            cpu_threads=settings.faster_whisper_cpu_threads,
            download_directory=settings.faster_whisper_download_directory,
        )

    async def transcribe(
        self,
        audio: AudioInput,
        initial_prompt: str | None,
    ) -> OfflineTranscription:
        """Use the live adapter's options plus one explicit offline hint."""

        return await asyncio.to_thread(self._transcribe_blocking, audio, initial_prompt)

    def close(self) -> None:
        """Release the local model reference without touching model files."""

        self._manager.close()

    def _transcribe_blocking(
        self,
        audio: AudioInput,
        initial_prompt: str | None,
    ) -> OfflineTranscription:
        started_at = time.monotonic()
        model_started_at = time.monotonic()
        model, model_loaded = self._manager.get_model_with_load_state()
        model_load_ms = _elapsed_ms(model_started_at)
        process_rss_bytes = _process_rss_bytes()
        kwargs: dict[str, object] = {
            "beam_size": self._settings.faster_whisper_beam_size,
            "vad_filter": self._settings.faster_whisper_vad_enabled,
            "language": "de",
        }
        if initial_prompt is not None:
            kwargs["initial_prompt"] = initial_prompt
        provider_segments, info = model.transcribe(BytesIO(audio.data), **kwargs)
        segments = FasterWhisperSpeechToTextProvider._map_segments(provider_segments)
        return OfflineTranscription(
            result=TranscriptionResult(
                language=FasterWhisperSpeechToTextProvider._map_language(info),
                segments=segments,
                duration_seconds=FasterWhisperSpeechToTextProvider._map_duration(
                    info, segments
                ),
            ),
            elapsed_ms=_elapsed_ms(started_at),
            model_load_ms=model_load_ms,
            model_loaded=model_loaded,
            process_rss_bytes=process_rss_bytes,
        )


def main() -> int:
    """Run all fixed Quality L conditions over one explicitly local WAV."""

    arguments = _parse_arguments()
    cases = load_quality_reference_fixture(arguments.fixture)
    case = next((item for item in cases if item.identifier == arguments.case_id), None)
    if case is None:
        raise ValueError("Quality L case ID is unavailable in the fixture.")
    audio_chunks = _load_live_profile_wav_chunks(arguments.audio)
    return asyncio.run(
        _run(
            settings=get_settings(),
            reference_de=case.reference_de,
            evaluation_terms=case.evaluation_terms,
            audio_chunks=audio_chunks,
            report=arguments.report,
            definitions=(
                (EXPERIMENT_DEFINITIONS[2],)
                if arguments.quality_n
                else (
                    QUALITY_M_EXPERIMENT_DEFINITIONS[-2:]
                    if arguments.quality_m_new_only
                    else (
                        QUALITY_M_EXPERIMENT_DEFINITIONS
                        if arguments.quality_m
                        else None
                    )
                )
            ),
        )
    )


async def _run(
    *,
    settings: Settings,
    reference_de: str,
    evaluation_terms: tuple[str, ...],
    audio_chunks: tuple[AudioInput, ...],
    report: Path,
    definitions: tuple[SttExperimentDefinition, ...] | None,
) -> int:
    print(
        "Quality L configuration: "
        f"model={settings.faster_whisper_model} "
        f"device={settings.faster_whisper_device} "
        f"compute_type={settings.faster_whisper_compute_type} "
        f"beam_size={settings.faster_whisper_beam_size} "
        f"vad={'on' if settings.faster_whisper_vad_enabled else 'off'} "
        "language=de chunks=4s overlap=1s "
        "condition_on_previous_text=provider_default",
        flush=True,
    )
    transcriber = _OfflineFasterWhisperTranscriber(settings=settings)
    try:
        results = await run_stt_experiments(
            reference_de=reference_de,
            audio_chunks=audio_chunks,
            evaluation_terms=evaluation_terms,
            transcribe=_with_progress(transcriber.transcribe, len(audio_chunks)),
            definitions=definitions,
        )
        await asyncio.to_thread(
            report.write_text,
            render_stt_quality_report(results),
            encoding="utf-8",
        )
    finally:
        transcriber.close()
    print("Quality L private report written.")
    return 0


def _with_progress(
    transcribe: Callable[[AudioInput, str | None], Awaitable[OfflineTranscription]],
    chunk_count: int,
) -> Callable[[AudioInput, str | None], Awaitable[OfflineTranscription]]:
    sequence = 0

    async def run(
        audio: AudioInput,
        initial_prompt: str | None,
    ) -> OfflineTranscription:
        nonlocal sequence
        sequence = sequence % chunk_count + 1
        should_report = (
            sequence == 1
            or sequence == chunk_count
            or sequence % _PROGRESS_INTERVAL_CHUNKS == 0
        )
        if should_report:
            print(f"[quality-l chunk {sequence}/{chunk_count}] started", flush=True)
        result = await transcribe(audio, initial_prompt)
        if should_report:
            print(
                f"[quality-l chunk {sequence}/{chunk_count}] completed "
                f"elapsed_ms={result.elapsed_ms}",
                flush=True,
            )
        return result

    return run


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline Quality L Faster-Whisper prompt/context replay."
    )
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    variant_group = parser.add_mutually_exclusive_group()
    variant_group.add_argument(
        "--quality-m",
        action="store_true",
        help="Run the predefined C/D/E/F prompt/context comparison only.",
    )
    variant_group.add_argument(
        "--quality-m-new-only",
        action="store_true",
        help="Run only the predefined new Quality M E/F conditions.",
    )
    variant_group.add_argument(
        "--quality-n",
        action="store_true",
        help="Run only the fixed Quality C context-only control condition.",
    )
    return parser.parse_args()


def _load_live_profile_wav_chunks(path: Path) -> tuple[AudioInput, ...]:
    """Split private PCM16 mono 16 kHz audio using the live 4 s / 1 s profile."""

    with wave.open(str(path), "rb") as wav_file:
        if (
            wav_file.getcomptype() != "NONE"
            or wav_file.getframerate() != _LIVE_SAMPLE_RATE_HZ
            or wav_file.getnchannels() != _LIVE_CHANNELS
            or wav_file.getsampwidth() != _LIVE_SAMPLE_WIDTH_BYTES
        ):
            raise ValueError("Quality L input must be PCM16 mono 16 kHz WAV.")
        frames = wav_file.readframes(wav_file.getnframes())
    chunk_frames = _LIVE_SAMPLE_RATE_HZ * _LIVE_CHUNK_SECONDS
    hop_frames = _LIVE_SAMPLE_RATE_HZ * (
        _LIVE_CHUNK_SECONDS - _LIVE_CHUNK_OVERLAP_SECONDS
    )
    frame_width = _LIVE_CHANNELS * _LIVE_SAMPLE_WIDTH_BYTES
    chunks = tuple(
        AudioInput(
            data=_wav_bytes(
                frames[start * frame_width : (start + chunk_frames) * frame_width]
            ),
            sample_rate_hz=_LIVE_SAMPLE_RATE_HZ,
            channels=_LIVE_CHANNELS,
            audio_format=AudioFormat.WAV,
        )
        for start in range(0, len(frames) // frame_width - chunk_frames + 1, hop_frames)
    )
    if not chunks:
        raise ValueError("Quality L input must contain one complete live chunk.")
    return chunks


def _wav_bytes(frames: bytes) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(_LIVE_CHANNELS)
        wav_file.setsampwidth(_LIVE_SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(_LIVE_SAMPLE_RATE_HZ)
        wav_file.writeframes(frames)
    return output.getvalue()


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1_000)


def _process_rss_bytes() -> int:
    """Return the process resident-set high-water mark in bytes for reports."""

    maximum_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, while common Unix implementations report KiB.
    return maximum_rss if sys.platform == "darwin" else maximum_rss * 1_024


if __name__ == "__main__":
    raise SystemExit(main())
