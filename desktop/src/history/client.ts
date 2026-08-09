import { invoke } from "@tauri-apps/api/core";

import type { MeetingDetail, MeetingHistoryResponse } from "./types";
import type {
  GeneratedMeetingTranslationArtifact,
  MeetingTranslationArtifact,
  TranslationArtifactSegment,
} from "./translationTypes";
import type {
  GeneratedMeetingReviewArtifact,
  MeetingReviewArtifact,
  MeetingReviewContent,
  ReviewActionItem,
  ReviewFeedback,
  ReviewInterviewQuestion,
  ReviewOpenQuestion,
  ReviewTechnicalTerm,
} from "./reviewTypes";

export function listMeetings(
  limit: number,
  offset: number,
): Promise<MeetingHistoryResponse> {
  return invoke<MeetingHistoryResponse>("list_meetings", { limit, offset });
}

export function getMeetingDetail(meetingId: string): Promise<MeetingDetail> {
  return invoke<MeetingDetail>("get_meeting_detail", { meetingId });
}

export async function getMeetingTranslation(
  meetingId: string,
  version?: number,
): Promise<MeetingTranslationArtifact | null> {
  try {
    const payload = await invoke<unknown>("get_meeting_translation", { meetingId, version });
    return payload === null ? null : translationArtifactFrom(payload);
  } catch {
    throw new Error("Translation is temporarily unavailable.");
  }
}

export async function generateMeetingTranslation(
  meetingId: string,
  forceRegenerate: boolean,
): Promise<GeneratedMeetingTranslationArtifact> {
  try {
    const payload = await invoke<unknown>("generate_meeting_translation", {
      meetingId,
      forceRegenerate,
    });
    const artifact = translationArtifactFrom(payload);
    if (!isRecord(payload) || typeof payload.reusedExisting !== "boolean") {
      throw new Error("Invalid translation response.");
    }
    return { ...artifact, reusedExisting: payload.reusedExisting };
  } catch {
    throw new Error("Translation is temporarily unavailable.");
  }
}

function translationArtifactFrom(payload: unknown): MeetingTranslationArtifact {
  if (!isRecord(payload) ||
    !isString(payload.artifactId) ||
    !isString(payload.meetingId) ||
    !isPositiveInteger(payload.version) ||
    payload.targetLanguage !== "tr" ||
    payload.status !== "completed" ||
    !isString(payload.createdAt) ||
    !(payload.completedAt === null || isString(payload.completedAt)) ||
    !isNonNegativeInteger(payload.sourceTranscriptCount) ||
    !Array.isArray(payload.segments)
  ) {
    throw new Error("Invalid translation response.");
  }
  const segments = payload.segments.map(translationSegmentFrom);
  return {
    artifactId: payload.artifactId,
    meetingId: payload.meetingId,
    version: payload.version,
    targetLanguage: "tr",
    status: "completed",
    createdAt: payload.createdAt,
    completedAt: payload.completedAt,
    sourceTranscriptCount: payload.sourceTranscriptCount,
    segments,
  };
}

function translationSegmentFrom(payload: unknown): TranslationArtifactSegment {
  if (!isRecord(payload) ||
    !isString(payload.transcriptId) ||
    !isString(payload.sourceText) ||
    !isString(payload.translatedText)
  ) {
    throw new Error("Invalid translation response.");
  }
  return {
    transcriptId: payload.transcriptId,
    sourceText: payload.sourceText,
    translatedText: payload.translatedText,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

export async function getMeetingReview(meetingId: string, version?: number): Promise<MeetingReviewArtifact | null> {
  try {
    const payload = await invoke<unknown>("get_meeting_review", { meetingId, version });
    return payload === null ? null : reviewArtifactFrom(payload);
  } catch {
    throw new Error("Review is temporarily unavailable.");
  }
}

export async function generateMeetingReview(meetingId: string, forceRegenerate: boolean): Promise<GeneratedMeetingReviewArtifact> {
  try {
    const payload = await invoke<unknown>("generate_meeting_review", { meetingId, forceRegenerate });
    const artifact = reviewArtifactFrom(payload);
    if (!isRecord(payload) || typeof payload.reusedExisting !== "boolean") throw new Error("Invalid review response.");
    return { ...artifact, reusedExisting: payload.reusedExisting };
  } catch {
    throw new Error("Review is temporarily unavailable.");
  }
}

function reviewArtifactFrom(payload: unknown): MeetingReviewArtifact {
  if (!isRecord(payload) || !isString(payload.artifactId) || !isString(payload.meetingId) ||
    !isPositiveInteger(payload.version) || payload.reviewType !== "interview_review" ||
    payload.status !== "completed" || !isString(payload.createdAt) ||
    !(payload.completedAt === null || isString(payload.completedAt)) ||
    !isNonNegativeInteger(payload.sourceTranscriptCount)) throw new Error("Invalid review response.");
  return { artifactId: payload.artifactId, meetingId: payload.meetingId, version: payload.version,
    reviewType: "interview_review", status: "completed", createdAt: payload.createdAt,
    completedAt: payload.completedAt, sourceTranscriptCount: payload.sourceTranscriptCount,
    content: reviewContentFrom(payload.content) };
}

function reviewContentFrom(payload: unknown): MeetingReviewContent {
  if (!isRecord(payload) || !isString(payload.summary) || !isStringArray(payload.keyDecisions) ||
    !Array.isArray(payload.actionItems) || !Array.isArray(payload.openQuestions) ||
    !Array.isArray(payload.technicalQuestions) || !Array.isArray(payload.technicalTerms) ||
    !(payload.feedback === null || isRecord(payload.feedback))) throw new Error("Invalid review response.");
  return { summary: payload.summary, keyDecisions: payload.keyDecisions,
    actionItems: payload.actionItems.map(actionItemFrom), openQuestions: payload.openQuestions.map(openQuestionFrom),
    technicalQuestions: payload.technicalQuestions.map(technicalQuestionFrom),
    technicalTerms: payload.technicalTerms.map(technicalTermFrom),
    feedback: payload.feedback === null ? null : feedbackFrom(payload.feedback) };
}

function actionItemFrom(value: unknown): ReviewActionItem { if (!isRecord(value) || !isString(value.text) || !(value.owner === null || isString(value.owner)) || !(value.dueDate === null || isString(value.dueDate))) throw new Error("Invalid review response."); return { text: value.text, owner: value.owner, dueDate: value.dueDate }; }
function openQuestionFrom(value: unknown): ReviewOpenQuestion { if (!isRecord(value) || !isString(value.question)) throw new Error("Invalid review response."); return { question: value.question }; }
function technicalTermFrom(value: unknown): ReviewTechnicalTerm { if (!isRecord(value) || !isString(value.term) || !isString(value.explanation)) throw new Error("Invalid review response."); return { term: value.term, explanation: value.explanation }; }
function technicalQuestionFrom(value: unknown): ReviewInterviewQuestion { if (!isRecord(value) || !isString(value.question) || !(value.answerSummary === null || isString(value.answerSummary)) || !(value.evaluation === null || isString(value.evaluation)) || !(value.improvementSuggestion === null || isString(value.improvementSuggestion))) throw new Error("Invalid review response."); return { question: value.question, answerSummary: value.answerSummary, evaluation: value.evaluation, improvementSuggestion: value.improvementSuggestion }; }
function feedbackFrom(value: Record<string, unknown>): ReviewFeedback { if (!isStringArray(value.strengths) || !isStringArray(value.improvementAreas) || !isString(value.overallFeedback)) throw new Error("Invalid review response."); return { strengths: value.strengths, improvementAreas: value.improvementAreas, overallFeedback: value.overallFeedback }; }
function isStringArray(value: unknown): value is string[] { return Array.isArray(value) && value.every(isString); }
