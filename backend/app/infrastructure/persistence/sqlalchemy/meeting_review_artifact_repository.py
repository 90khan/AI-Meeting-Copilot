"""SQLAlchemy adapter for immutable Meeting review artifacts."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.dto.meeting_review import (
    MeetingReviewArtifact,
    MeetingReviewArtifactStatus,
    MeetingReviewContent,
    ReviewActionItem,
    ReviewFeedback,
    ReviewInterviewQuestion,
    ReviewOpenQuestion,
    ReviewTechnicalTerm,
)
from app.application.exceptions import ApplicationValidationError
from app.application.interfaces.meeting_review_artifact_repository import (
    MeetingReviewArtifactRepository,
)
from app.domain.value_objects import MeetingId
from app.infrastructure.persistence.sqlalchemy.models import (
    meeting_review_artifact_model,
)

MeetingReviewArtifactModel = meeting_review_artifact_model.MeetingReviewArtifactModel


class SQLAlchemyMeetingReviewArtifactRepository(MeetingReviewArtifactRepository):
    """Persist artifacts through a caller-owned synchronous SQLAlchemy Session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def save(self, artifact: MeetingReviewArtifact) -> None:
        """Insert or update an artifact without taking commit ownership."""

        model = self._session.get(MeetingReviewArtifactModel, str(artifact.artifact_id))
        if model is None:
            model = MeetingReviewArtifactModel(artifact_id=str(artifact.artifact_id))
            self._session.add(model)
        self._apply(model, artifact)
        self._session.flush()

    async def get_by_id(self, artifact_id: UUID) -> MeetingReviewArtifact | None:
        """Return one immutable artifact by its stable identity."""

        model = self._session.get(MeetingReviewArtifactModel, str(artifact_id))
        return None if model is None else self._to_artifact(model)

    async def get_by_meeting_and_version(
        self,
        meeting_id: MeetingId,
        review_type: str,
        version: int,
    ) -> MeetingReviewArtifact | None:
        """Return one exact artifact version when it exists."""

        statement = select(MeetingReviewArtifactModel).where(
            MeetingReviewArtifactModel.meeting_id == str(meeting_id),
            MeetingReviewArtifactModel.review_type == review_type,
            MeetingReviewArtifactModel.version == version,
        )
        model = self._session.scalar(statement)
        return None if model is None else self._to_artifact(model)

    async def get_latest_completed(
        self,
        meeting_id: MeetingId,
        review_type: str,
    ) -> MeetingReviewArtifact | None:
        """Return the highest-version completed artifact deterministically."""

        statement = (
            select(MeetingReviewArtifactModel)
            .where(
                MeetingReviewArtifactModel.meeting_id == str(meeting_id),
                MeetingReviewArtifactModel.review_type == review_type,
                MeetingReviewArtifactModel.status
                == MeetingReviewArtifactStatus.COMPLETED.value,
            )
            .order_by(
                MeetingReviewArtifactModel.version.desc(),
                MeetingReviewArtifactModel.created_at.desc(),
                MeetingReviewArtifactModel.artifact_id.desc(),
            )
            .limit(1)
        )
        model = self._session.scalar(statement)
        return None if model is None else self._to_artifact(model)

    async def get_latest_version(
        self,
        meeting_id: MeetingId,
        review_type: str,
    ) -> int:
        """Return the highest version across all lifecycle states, or zero."""

        statement = select(func.max(MeetingReviewArtifactModel.version)).where(
            MeetingReviewArtifactModel.meeting_id == str(meeting_id),
            MeetingReviewArtifactModel.review_type == review_type,
        )
        latest_version = self._session.scalar(statement)
        return 0 if latest_version is None else int(latest_version)

    async def list_for_meeting(
        self,
        meeting_id: MeetingId,
        review_type: str,
    ) -> tuple[MeetingReviewArtifact, ...]:
        """Return immutable review artifacts in deterministic version order."""

        statement = (
            select(MeetingReviewArtifactModel)
            .where(
                MeetingReviewArtifactModel.meeting_id == str(meeting_id),
                MeetingReviewArtifactModel.review_type == review_type,
            )
            .order_by(
                MeetingReviewArtifactModel.version.desc(),
                MeetingReviewArtifactModel.created_at.desc(),
                MeetingReviewArtifactModel.artifact_id.desc(),
            )
        )
        return tuple(
            self._to_artifact(model) for model in self._session.scalars(statement)
        )

    @staticmethod
    def _apply(
        model: MeetingReviewArtifactModel,
        artifact: MeetingReviewArtifact,
    ) -> None:
        model.meeting_id = str(artifact.meeting_id)
        model.version = artifact.version
        model.review_type = artifact.review_type
        model.status = artifact.status.value
        model.created_at = artifact.created_at
        model.completed_at = artifact.completed_at
        model.source_transcript_count = artifact.source_transcript_count
        model.provider_name = artifact.provider_name
        model.model_name = artifact.model_name
        model.prompt_version = artifact.prompt_version
        model.schema_version = artifact.schema_version
        model.failure_code = artifact.failure_code
        model.content_json = (
            None
            if artifact.content is None
            else SQLAlchemyMeetingReviewArtifactRepository._content_to_json(
                artifact.content
            )
        )

    @classmethod
    def _to_artifact(
        cls,
        model: MeetingReviewArtifactModel,
    ) -> MeetingReviewArtifact:
        return MeetingReviewArtifact(
            artifact_id=UUID(model.artifact_id),
            meeting_id=MeetingId(UUID(model.meeting_id)),
            version=model.version,
            review_type=model.review_type,
            status=MeetingReviewArtifactStatus(model.status),
            created_at=cls._normalize_utc(model.created_at),
            completed_at=cls._normalize_optional_utc(model.completed_at),
            source_transcript_count=model.source_transcript_count,
            content=cls._content_from_json(model.content_json),
            provider_name=model.provider_name,
            model_name=model.model_name,
            prompt_version=model.prompt_version,
            schema_version=model.schema_version,
            failure_code=model.failure_code,
        )

    @staticmethod
    def _content_to_json(content: MeetingReviewContent) -> dict[str, object]:
        return {
            "summary": content.summary,
            "key_decisions": list(content.key_decisions),
            "action_items": [
                {"text": item.text, "owner": item.owner, "due_date": item.due_date}
                for item in content.action_items
            ],
            "open_questions": [
                {"question": item.question} for item in content.open_questions
            ],
            "technical_questions": [
                {
                    "question": item.question,
                    "answer_summary": item.answer_summary,
                    "evaluation": item.evaluation,
                    "improvement_suggestion": item.improvement_suggestion,
                }
                for item in content.technical_questions
            ],
            "technical_terms": [
                {"term": item.term, "explanation": item.explanation}
                for item in content.technical_terms
            ],
            "feedback": (
                None
                if content.feedback is None
                else {
                    "strengths": list(content.feedback.strengths),
                    "improvement_areas": list(content.feedback.improvement_areas),
                    "overall_feedback": content.feedback.overall_feedback,
                }
            ),
        }

    @classmethod
    def _content_from_json(cls, value: object) -> MeetingReviewContent | None:
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {
            "summary",
            "key_decisions",
            "action_items",
            "open_questions",
            "technical_questions",
            "technical_terms",
            "feedback",
        }:
            raise ApplicationValidationError("Persisted review content is invalid.")

        try:
            return MeetingReviewContent(
                summary=cls._string(value["summary"]),
                key_decisions=cls._strings(value["key_decisions"]),
                action_items=cls._action_items(value["action_items"]),
                open_questions=cls._open_questions(value["open_questions"]),
                technical_questions=cls._technical_questions(
                    value["technical_questions"]
                ),
                technical_terms=cls._technical_terms(value["technical_terms"]),
                feedback=cls._feedback(value["feedback"]),
            )
        except (TypeError, ValueError, ApplicationValidationError) as error:
            raise ApplicationValidationError(
                "Persisted review content is invalid."
            ) from error

    @staticmethod
    def _string(value: object) -> str:
        if not isinstance(value, str):
            raise ValueError
        return value

    @classmethod
    def _strings(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise ValueError
        return tuple(cls._string(item) for item in value)

    @classmethod
    def _action_items(cls, value: object) -> tuple[ReviewActionItem, ...]:
        if not isinstance(value, list):
            raise ValueError
        items: list[ReviewActionItem] = []
        for item in value:
            if not isinstance(item, dict) or set(item) != {
                "text",
                "owner",
                "due_date",
            }:
                raise ValueError
            items.append(
                ReviewActionItem(
                    text=cls._string(item["text"]),
                    owner=cls._optional_string(item["owner"]),
                    due_date=cls._optional_string(item["due_date"]),
                )
            )
        return tuple(items)

    @classmethod
    def _open_questions(cls, value: object) -> tuple[ReviewOpenQuestion, ...]:
        if not isinstance(value, list):
            raise ValueError
        questions: list[ReviewOpenQuestion] = []
        for item in value:
            if not isinstance(item, dict) or set(item) != {"question"}:
                raise ValueError
            questions.append(ReviewOpenQuestion(question=cls._string(item["question"])))
        return tuple(questions)

    @classmethod
    def _technical_questions(
        cls,
        value: object,
    ) -> tuple[ReviewInterviewQuestion, ...]:
        if not isinstance(value, list):
            raise ValueError
        questions: list[ReviewInterviewQuestion] = []
        for item in value:
            if not isinstance(item, dict) or set(item) != {
                "question",
                "answer_summary",
                "evaluation",
                "improvement_suggestion",
            }:
                raise ValueError
            questions.append(
                ReviewInterviewQuestion(
                    question=cls._string(item["question"]),
                    answer_summary=cls._optional_string(item["answer_summary"]),
                    evaluation=cls._optional_string(item["evaluation"]),
                    improvement_suggestion=cls._optional_string(
                        item["improvement_suggestion"]
                    ),
                )
            )
        return tuple(questions)

    @classmethod
    def _technical_terms(cls, value: object) -> tuple[ReviewTechnicalTerm, ...]:
        if not isinstance(value, list):
            raise ValueError
        terms: list[ReviewTechnicalTerm] = []
        for item in value:
            if not isinstance(item, dict) or set(item) != {"term", "explanation"}:
                raise ValueError
            terms.append(
                ReviewTechnicalTerm(
                    term=cls._string(item["term"]),
                    explanation=cls._string(item["explanation"]),
                )
            )
        return tuple(terms)

    @classmethod
    def _feedback(cls, value: object) -> ReviewFeedback | None:
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {
            "strengths",
            "improvement_areas",
            "overall_feedback",
        }:
            raise ValueError
        return ReviewFeedback(
            strengths=cls._strings(value["strengths"]),
            improvement_areas=cls._strings(value["improvement_areas"]),
            overall_feedback=cls._string(value["overall_feedback"]),
        )

    @staticmethod
    def _optional_string(value: object) -> str | None:
        if value is None:
            return None
        return SQLAlchemyMeetingReviewArtifactRepository._string(value)

    @staticmethod
    def _normalize_optional_utc(value: datetime | None) -> datetime | None:
        return (
            None
            if value is None
            else SQLAlchemyMeetingReviewArtifactRepository._normalize_utc(value)
        )

    @staticmethod
    def _normalize_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        if value.utcoffset() != timedelta(0):
            raise ApplicationValidationError(
                "Persisted review timestamps must use UTC."
            )
        return value
