"""Tests for deterministic exact-value Meeting review merging."""

import pytest
from app.application.dto.meeting_review import (
    BatchReviewResult,
    MeetingReviewContent,
    ReviewActionItem,
    ReviewFeedback,
    ReviewInterviewQuestion,
    ReviewOpenQuestion,
    ReviewTechnicalTerm,
)
from app.application.exceptions import ApplicationValidationError
from app.application.services import MeetingReviewMerger


def _content(
    index: int,
    *,
    feedback: ReviewFeedback | None = None,
) -> MeetingReviewContent:
    return MeetingReviewContent(
        summary=f"Summary {index}.",
        key_decisions=("Shared decision.", f"Decision {index}."),
        action_items=(
            ReviewActionItem(text="Shared action.", owner=None, due_date=None),
            ReviewActionItem(text=f"Action {index}.", owner="Owner", due_date=None),
        ),
        open_questions=(
            ReviewOpenQuestion(question="Shared question?"),
            ReviewOpenQuestion(question=f"Question {index}?"),
        ),
        technical_questions=(
            ReviewInterviewQuestion(
                question="Shared technical question?",
                answer_summary=None,
                evaluation=None,
                improvement_suggestion=None,
            ),
        ),
        technical_terms=(
            ReviewTechnicalTerm(term="Shared term", explanation="Shared explanation."),
            ReviewTechnicalTerm(term=f"Term {index}", explanation=f"Meaning {index}."),
        ),
        feedback=feedback,
    )


def test_merge_sorts_batches_and_preserves_first_seen_exact_values() -> None:
    first = BatchReviewResult(batch_index=0, content=_content(0))
    second = BatchReviewResult(batch_index=1, content=_content(1))

    merged = MeetingReviewMerger().merge((second, first))

    assert merged.summary == "Summary 0.\n\nSummary 1."
    assert merged.key_decisions == (
        "Shared decision.",
        "Decision 0.",
        "Decision 1.",
    )
    assert [item.text for item in merged.action_items] == [
        "Shared action.",
        "Action 0.",
        "Action 1.",
    ]
    assert [item.question for item in merged.open_questions] == [
        "Shared question?",
        "Question 0?",
        "Question 1?",
    ]
    assert [item.term for item in merged.technical_terms] == [
        "Shared term",
        "Term 0",
        "Term 1",
    ]
    assert len(merged.technical_questions) == 1


def test_merge_feedback_deduplicates_collections_and_combines_text() -> None:
    first_feedback = ReviewFeedback(
        strengths=("Clear.", "Structured."),
        improvement_areas=("More detail.",),
        overall_feedback="First feedback.",
    )
    second_feedback = ReviewFeedback(
        strengths=("Clear.", "Concise."),
        improvement_areas=("More detail.", "Give examples."),
        overall_feedback="Second feedback.",
    )

    merged = MeetingReviewMerger().merge(
        (
            BatchReviewResult(
                batch_index=0,
                content=_content(0, feedback=first_feedback),
            ),
            BatchReviewResult(
                batch_index=1,
                content=_content(1, feedback=second_feedback),
            ),
        )
    )

    assert merged.feedback == ReviewFeedback(
        strengths=("Clear.", "Structured.", "Concise."),
        improvement_areas=("More detail.", "Give examples."),
        overall_feedback="First feedback.\n\nSecond feedback.",
    )


@pytest.mark.parametrize(
    "batches",
    [
        (),
        (
            BatchReviewResult(batch_index=0, content=_content(0)),
            BatchReviewResult(batch_index=0, content=_content(1)),
        ),
        (BatchReviewResult(batch_index=1, content=_content(1)),),
        (
            BatchReviewResult(batch_index=0, content=_content(0)),
            BatchReviewResult(batch_index=2, content=_content(2)),
        ),
    ],
)
def test_merge_rejects_empty_duplicate_or_non_contiguous_indices(
    batches: tuple[BatchReviewResult, ...],
) -> None:
    with pytest.raises(ApplicationValidationError):
        MeetingReviewMerger().merge(batches)


def test_merge_keeps_text_exact_and_returns_immutable_content() -> None:
    content = MeetingReviewContent(
        summary="  Original summary.  ",
        key_decisions=(" Decision. ",),
        action_items=(),
        open_questions=(),
        technical_questions=(),
        technical_terms=(),
        feedback=None,
    )

    merged = MeetingReviewMerger().merge(
        (BatchReviewResult(batch_index=0, content=content),)
    )

    assert merged.summary == "  Original summary.  "
    assert merged.key_decisions == (" Decision. ",)
    with pytest.raises(AttributeError):
        merged.summary = "Changed"
