"""Command for processing one finalized live-audio chunk."""

from dataclasses import dataclass

from app.application.dto.ai import LanguageCode
from app.application.dto.live_transcription import CapturedAudioChunk
from app.application.exceptions import ApplicationValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessLiveAudioChunkCommand:
    """Carry one finalized chunk and bounded de-duplication context."""

    chunk: CapturedAudioChunk
    language_hint: LanguageCode | None = None
    previous_accepted_text: str | None = None

    def __post_init__(self) -> None:
        """Validate the optional prior accepted-text context."""

        if (
            self.previous_accepted_text is not None
            and not self.previous_accepted_text.strip()
        ):
            raise ApplicationValidationError(
                "Previous accepted text must not be blank."
            )
