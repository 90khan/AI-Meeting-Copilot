import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import type {
  AssistReplySuggestionsPayload,
  AssistSegmentUpdatePayload,
  TranscriptSegmentPayload,
} from "./types";
import type { AssistAction } from "./state";

const isNonBlankString = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0;

function transcriptPayload(value: unknown): TranscriptSegmentPayload | null {
  if (!value || typeof value !== "object") return null;
  const payload = value as Record<string, unknown>;
  return ["transcript_id", "text", "timestamp", "source", "speaker"].every((key) => isNonBlankString(payload[key]))
    ? payload as unknown as TranscriptSegmentPayload
    : null;
}

function segmentUpdatePayload(value: unknown): AssistSegmentUpdatePayload | null {
  if (!value || typeof value !== "object") return null;
  const payload = value as Record<string, unknown>;
  const validState = ["processing", "ready", "failed", "unavailable"].includes(String(payload.state));
  const validCapability = payload.capability === "translation" || payload.capability === "simplification";
  if (!isNonBlankString(payload.transcript_id) || !validState || !validCapability) return null;
  if (payload.state === "ready" && payload.capability === "translation" && !isNonBlankString(payload.translated_text)) return null;
  if (payload.state === "ready" && payload.capability === "simplification" && (!isNonBlankString(payload.simplified_text) || (payload.target_level !== "b1" && payload.target_level !== "b2"))) return null;
  return payload as unknown as AssistSegmentUpdatePayload;
}

function replySuggestionsPayload(value: unknown): AssistReplySuggestionsPayload | null {
  if (!value || typeof value !== "object") return null;
  const payload = value as Record<string, unknown>;
  if (!isNonBlankString(payload.anchor_transcript_id) || !["processing", "ready", "failed", "unavailable"].includes(String(payload.state))) return null;
  if (payload.state === "ready" && (!Array.isArray(payload.suggestions) || payload.suggestions.some((item) => !item || typeof item !== "object" || !isNonBlankString((item as Record<string, unknown>).text) || !isNonBlankString((item as Record<string, unknown>).tone)))) return null;
  return payload as unknown as AssistReplySuggestionsPayload;
}

export async function subscribeToAssistEvents(dispatch: (action: AssistAction) => void): Promise<() => void> {
  let active = true;
  const listeners = await Promise.all([
    listen("assist://transcript-segment", (event) => {
      const payload = transcriptPayload(event.payload);
      if (active && payload) dispatch({ type: "transcript", payload });
    }),
    listen("assist://segment-update", (event) => {
      const payload = segmentUpdatePayload(event.payload);
      if (active && payload) dispatch({ type: "segment-update", payload });
    }),
    listen("assist://reply-suggestions", (event) => {
      const payload = replySuggestionsPayload(event.payload);
      if (active && payload) dispatch({ type: "reply-suggestions", payload });
    }),
  ]);
  return () => {
    active = false;
    listeners.forEach((unlisten: UnlistenFn) => unlisten());
  };
}
