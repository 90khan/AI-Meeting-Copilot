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
  durationSeconds: number | null;
  segmentCount: number;
  hasGaps: boolean;
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
  durationSeconds: number | null;
  currentTimeSeconds: number;
  hasGaps: boolean;
  inputComplete: boolean;
  lastSegmentIndex: number | null;
}
