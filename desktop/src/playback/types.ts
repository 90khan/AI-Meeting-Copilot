export type PlaybackState =
  | "idle"
  | "loading"
  | "ready"
  | "playing"
  | "paused"
  | "stopped"
  | "ended"
  | "failed";

export interface RecordingPlaybackInfo {
  meetingId: string;
  format: "wav_pcm16_mono_16khz_segmented_v1";
  captureAnchorUtc: string;
  durationSeconds: number | null;
  segmentCount: number;
  hasGaps: boolean;
}

/** Safe result of a backend-resolved playback seek. */
export interface RecordingPlaybackSeekResult {
  atEnd: boolean;
  generation: number | null;
  resolvedSeconds: number | null;
  segmentIndex: number | null;
  offsetSamples: number | null;
}

export interface PlaybackChunkEvent {
  generation: number;
  segmentIndex: number;
  wavPayloadBase64: string;
}

export interface PlaybackTerminalEvent {
  generation: number;
}

export interface PlaybackUiState {
  state: PlaybackState;
  meetingId: string | null;
  playbackGeneration: number | null;
  captureAnchorUtc: string | null;
  durationSeconds: number | null;
  currentTimeSeconds: number;
  hasGaps: boolean;
  inputComplete: boolean;
  lastSegmentIndex: number | null;
}
