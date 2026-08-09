import { useEffect, useRef, useState } from "react";

import { generateMeetingReview, getMeetingReview } from "../history/client";
import type { MeetingReviewArtifact } from "../history/reviewTypes";

type ReviewState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; artifact: MeetingReviewArtifact }
  | { kind: "generating" }
  | { kind: "unavailable" }
  | { kind: "failed" };

export function MeetingReviewPanel({ meetingId }: { meetingId: string }) {
  const [review, setReview] = useState<ReviewState>({ kind: "idle" });
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    setReview({ kind: "loading" });
    void getMeetingReview(meetingId).then((artifact) => {
      if (!mounted.current) return;
      setReview(artifact === null ? { kind: "unavailable" } : artifact.meetingId === meetingId ? { kind: "ready", artifact } : { kind: "failed" });
    }).catch(() => {
      if (mounted.current) setReview({ kind: "failed" });
    });
    return () => { mounted.current = false; };
  }, [meetingId]);

  const generate = async (): Promise<void> => {
    if (review.kind === "loading" || review.kind === "generating") return;
    const forceRegenerate = review.kind === "ready";
    setReview({ kind: "generating" });
    try {
      const artifact = await generateMeetingReview(meetingId, forceRegenerate);
      if (mounted.current) setReview(artifact.meetingId === meetingId ? { kind: "ready", artifact } : { kind: "failed" });
    } catch {
      if (mounted.current) setReview({ kind: "failed" });
    }
  };

  const pending = review.kind === "loading" || review.kind === "generating";
  return <section className="meeting-review" aria-labelledby="review-title">
    <header className="review-header">
      <h4 id="review-title">Meeting Review</h4>
      <button type="button" disabled={pending} onClick={() => void generate()}>
        {review.kind === "generating" ? "Generating…" : review.kind === "ready" ? "Regenerate Review" : "Generate Review"}
      </button>
    </header>
    <p className="review-status" aria-live="polite">
      {review.kind === "loading" && "Checking for a review…"}
      {review.kind === "generating" && "Generating review…"}
      {review.kind === "unavailable" && "No review yet."}
      {review.kind === "failed" && "Review is temporarily unavailable."}
      {review.kind === "ready" && `Review version ${review.artifact.version} · ${review.artifact.completedAt === null ? "Completed time unavailable" : `Completed ${new Date(review.artifact.completedAt).toLocaleString()}`}`}
    </p>
    {review.kind === "ready" && <ReviewContent artifact={review.artifact} />}
  </section>;
}

function ReviewContent({ artifact }: { artifact: MeetingReviewArtifact }) {
  const content = artifact.content;
  return <div className="review-content">
    <ReviewSection title="Summary"><p>{content.summary}</p></ReviewSection>
    <StringSection title="Key Decisions" items={content.keyDecisions} />
    <ReviewSection title="Action Items">{content.actionItems.length === 0 ? <p>None.</p> : <ul>{content.actionItems.map((item, index) => <li key={`${item.text}-${index}`}><strong>{item.text}</strong>{item.owner !== null && <span> · Owner: {item.owner}</span>}{item.dueDate !== null && <span> · Due: {item.dueDate}</span>}</li>)}</ul>}</ReviewSection>
    <ReviewSection title="Open Questions">{content.openQuestions.length === 0 ? <p>None.</p> : <ul>{content.openQuestions.map((item, index) => <li key={`${item.question}-${index}`}>{item.question}</li>)}</ul>}</ReviewSection>
    <ReviewSection title="Technical Questions">{content.technicalQuestions.length === 0 ? <p>None.</p> : <div className="review-cards">{content.technicalQuestions.map((item, index) => <article className="review-card" key={`${item.question}-${index}`}><strong>{item.question}</strong>{item.answerSummary !== null && <p>Answer summary: {item.answerSummary}</p>}{item.evaluation !== null && <p>Evaluation: {item.evaluation}</p>}{item.improvementSuggestion !== null && <p>Improvement: {item.improvementSuggestion}</p>}</article>)}</div>}</ReviewSection>
    <ReviewSection title="Technical Terms">{content.technicalTerms.length === 0 ? <p>None.</p> : <ul>{content.technicalTerms.map((item, index) => <li key={`${item.term}-${index}`}><strong>{item.term}</strong>: {item.explanation}</li>)}</ul>}</ReviewSection>
    {content.feedback !== null && <ReviewSection title="Feedback"><h5>Strengths</h5><StringList items={content.feedback.strengths} /><h5>Improvement Areas</h5><StringList items={content.feedback.improvementAreas} /><h5>Overall Feedback</h5><p>{content.feedback.overallFeedback}</p></ReviewSection>}
  </div>;
}

function ReviewSection({ title, children }: { title: string; children: React.ReactNode }) { return <section className="review-section"><h5>{title}</h5>{children}</section>; }
function StringSection({ title, items }: { title: string; items: string[] }) { return <ReviewSection title={title}><StringList items={items} /></ReviewSection>; }
function StringList({ items }: { items: string[] }) { return items.length === 0 ? <p>None.</p> : <ul>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>; }
