import type { PlaybackUiState } from "./types";

export const initialPlaybackState: PlaybackUiState = {
  state: "idle", meetingId: null, playbackGeneration: null, durationSeconds: null,
  currentTimeSeconds: 0, hasGaps: false, inputComplete: false, lastSegmentIndex: null,
};
