"""Application use cases."""

from app.application.use_cases.add_transcript import AddTranscriptUseCase
from app.application.use_cases.create_meeting import CreateMeetingUseCase
from app.application.use_cases.end_meeting import EndMeetingUseCase
from app.application.use_cases.finalize_recording import FinalizeRecordingUseCase
from app.application.use_cases.generate_reply_suggestions import (
    GenerateReplySuggestionsUseCase,
)
from app.application.use_cases.mark_recording_failed import MarkRecordingFailedUseCase
from app.application.use_cases.mark_recording_started import MarkRecordingStartedUseCase
from app.application.use_cases.prepare_recording_session import (
    PrepareRecordingSessionUseCase,
)
from app.application.use_cases.process_live_audio_chunk import (
    ProcessLiveAudioChunkUseCase,
)
from app.application.use_cases.rename_meeting import RenameMeetingUseCase
from app.application.use_cases.simplify_transcript_segment import (
    SimplifyTranscriptSegmentUseCase,
)
from app.application.use_cases.start_live_transcription_session import (
    StartLiveTranscriptionSessionUseCase,
)
from app.application.use_cases.start_meeting import StartMeetingUseCase
from app.application.use_cases.translate_transcript_segment import (
    TranslateTranscriptSegmentUseCase,
)

__all__ = [
    "AddTranscriptUseCase",
    "CreateMeetingUseCase",
    "EndMeetingUseCase",
    "FinalizeRecordingUseCase",
    "GenerateReplySuggestionsUseCase",
    "MarkRecordingFailedUseCase",
    "MarkRecordingStartedUseCase",
    "PrepareRecordingSessionUseCase",
    "ProcessLiveAudioChunkUseCase",
    "RenameMeetingUseCase",
    "SimplifyTranscriptSegmentUseCase",
    "StartLiveTranscriptionSessionUseCase",
    "StartMeetingUseCase",
    "TranslateTranscriptSegmentUseCase",
]
