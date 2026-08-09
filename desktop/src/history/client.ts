import { invoke } from "@tauri-apps/api/core";

import type { MeetingDetail, MeetingHistoryResponse } from "./types";
import type {
  GeneratedMeetingTranslationArtifact,
  MeetingTranslationArtifact,
  TranslationArtifactSegment,
} from "./translationTypes";

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
