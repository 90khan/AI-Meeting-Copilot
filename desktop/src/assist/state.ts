import type {
  AssistConnectionStatus,
  AssistReplySuggestionsPayload,
  AssistSegmentUpdatePayload,
  AssistState,
  AssistTranscriptSegment,
  TranscriptSegmentPayload,
} from "./types";

const MAX_TRANSCRIPT_ROWS = 500;

export const initialAssistState: AssistState = {
  transcriptOrder: [],
  segments: {},
  replySuggestions: { state: "idle" },
  connectionStatus: "inactive",
};

export type AssistAction =
  | { type: "transcript"; payload: TranscriptSegmentPayload }
  | { type: "segment-update"; payload: AssistSegmentUpdatePayload }
  | { type: "reply-suggestions"; payload: AssistReplySuggestionsPayload }
  | { type: "connection"; status: AssistConnectionStatus }
  | { type: "clear" };

function toSegment(payload: TranscriptSegmentPayload): AssistTranscriptSegment {
  return {
    transcriptId: payload.transcript_id,
    text: payload.text,
    timestamp: payload.timestamp,
    source: payload.source,
    speaker: payload.speaker,
    translation: { state: "idle" },
    simplification: { state: "idle" },
  };
}

export function assistReducer(state: AssistState, action: AssistAction): AssistState {
  if (action.type === "clear") return initialAssistState;
  if (action.type === "connection") return { ...state, connectionStatus: action.status };

  if (action.type === "transcript") {
    const existing = state.segments[action.payload.transcript_id];
    if (existing) {
      return {
        ...state,
        segments: { ...state.segments, [existing.transcriptId]: { ...existing, ...toSegment(action.payload), translation: existing.translation, simplification: existing.simplification } },
      };
    }
    const transcriptOrder = [...state.transcriptOrder, action.payload.transcript_id];
    const segments = { ...state.segments, [action.payload.transcript_id]: toSegment(action.payload) };
    while (transcriptOrder.length > MAX_TRANSCRIPT_ROWS) {
      const oldestId = transcriptOrder.shift();
      if (oldestId) delete segments[oldestId];
    }
    return { ...state, transcriptOrder, segments };
  }

  if (action.type === "segment-update") {
    const segment = state.segments[action.payload.transcript_id];
    if (!segment) return state;
    if (action.payload.capability === "translation") {
      const translation = action.payload.state === "ready" && action.payload.translated_text
        ? { state: "ready" as const, value: action.payload.translated_text }
        : { state: action.payload.state === "ready" ? "failed" as const : action.payload.state };
      return { ...state, segments: { ...state.segments, [segment.transcriptId]: { ...segment, translation } } };
    }
    const simplification = action.payload.state === "ready" && action.payload.simplified_text && action.payload.target_level
      ? { state: "ready" as const, value: { text: action.payload.simplified_text, targetLevel: action.payload.target_level } }
      : { state: action.payload.state === "ready" ? "failed" as const : action.payload.state };
    return { ...state, segments: { ...state.segments, [segment.transcriptId]: { ...segment, simplification } } };
  }

  const replySuggestions = action.payload.state === "ready" && action.payload.suggestions
    ? { state: "ready" as const, value: action.payload.suggestions }
    : { state: action.payload.state === "ready" ? "failed" as const : action.payload.state };
  return { ...state, replySuggestions };
}
