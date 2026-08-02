export type AsyncState = "idle" | "processing" | "ready" | "failed" | "unavailable";
type NonReadyAsyncState = Exclude<AsyncState, "ready">;

export type AsyncValue<T> =
  | { state: NonReadyAsyncState }
  | { state: "ready"; value: T };

export type AssistCapability = "translation" | "simplification";
export type AssistConnectionStatus = "active" | "delayed" | "failed" | "inactive";

export interface ReplySuggestion {
  text: string;
  tone: string;
}

export interface AssistTranscriptSegment {
  transcriptId: string;
  text: string;
  timestamp: string;
  source: string;
  speaker: string;
  translation: AsyncValue<string>;
  simplification: AsyncValue<{ text: string; targetLevel: "b1" | "b2" }>;
}

export interface AssistState {
  transcriptOrder: string[];
  segments: Record<string, AssistTranscriptSegment>;
  replySuggestions: AsyncValue<ReplySuggestion[]>;
  connectionStatus: AssistConnectionStatus;
}

export interface TranscriptSegmentPayload {
  transcript_id: string;
  text: string;
  timestamp: string;
  source: string;
  speaker: string;
}

export interface AssistSegmentUpdatePayload {
  transcript_id: string;
  capability: AssistCapability;
  state: Exclude<AsyncState, "idle">;
  translated_text?: string;
  simplified_text?: string;
  target_level?: "b1" | "b2";
}

export interface AssistReplySuggestionsPayload {
  anchor_transcript_id: string;
  state: Exclude<AsyncState, "idle">;
  suggestions?: ReplySuggestion[];
}
