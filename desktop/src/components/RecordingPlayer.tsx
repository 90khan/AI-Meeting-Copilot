import { useEffect, useRef, useState } from "react";

import { activateRecordingPlaybackEvents, prepareRecordingPlayback, startRecordingPlaybackStream, stopRecordingPlayback } from "../playback/client";
import { subscribeToPlaybackEvents } from "../playback/events";
import { initialPlaybackState } from "../playback/state";
import type { PlaybackChunkEvent, PlaybackState, PlaybackUiState, RecordingPlaybackInfo } from "../playback/types";

const GENERIC_FAILURE = "Playback is unavailable.";
const SCHEDULE_LEAD_SECONDS = 0.05;
const POSITION_UPDATE_INTERVAL_MS = 200;

function base64Bytes(encoded: string): ArrayBuffer {
  const binary = window.atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes.buffer;
}

function timeLabel(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "0:00";
  const total = Math.max(0, Math.floor(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function RecordingPlayer({ meetingId, durationSeconds, hasGaps, onPositionChange, onPlaybackStateChange, onTimingAvailable }: {
  meetingId: string;
  durationSeconds: number | null;
  hasGaps: boolean;
  onPositionChange?: (seconds: number) => void;
  onPlaybackStateChange?: (state: PlaybackState) => void;
  onTimingAvailable?: (timing: Pick<RecordingPlaybackInfo, "captureAnchorUtc" | "hasGaps">) => void;
}) {
  const [state, setState] = useState<PlaybackUiState>({ ...initialPlaybackState, meetingId, durationSeconds, hasGaps });
  const stateRef = useRef(state);
  const contextRef = useRef<AudioContext | null>(null);
  const sourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const generationRef = useRef<number | null>(null);
  const nextScheduledTimeRef = useRef(0);
  const firstScheduledTimeRef = useRef(0);
  const lastIndexRef = useRef<number | null>(null);
  const decodeChainRef = useRef<Promise<void>>(Promise.resolve());
  const animationFrameRef = useRef<number | null>(null);
  const lastPositionUpdateRef = useRef(0);
  const mountedRef = useRef(true);
  const onPositionChangeRef = useRef(onPositionChange);
  const onPlaybackStateChangeRef = useRef(onPlaybackStateChange);
  const onTimingAvailableRef = useRef(onTimingAvailable);
  onPositionChangeRef.current = onPositionChange;
  onPlaybackStateChangeRef.current = onPlaybackStateChange;
  onTimingAvailableRef.current = onTimingAvailable;

  const update = (next: PlaybackUiState): void => {
    const previousState = stateRef.current.state;
    stateRef.current = next;
    if (previousState !== next.state) onPlaybackStateChangeRef.current?.(next.state);
    if (mountedRef.current) setState(next);
  };
  const cleanupAudio = (): void => {
    if (animationFrameRef.current !== null) cancelAnimationFrame(animationFrameRef.current);
    animationFrameRef.current = null;
    for (const source of sourcesRef.current) { try { source.stop(); } catch { /* already ended */ } }
    sourcesRef.current.clear();
    const context = contextRef.current;
    contextRef.current = null;
    if (context !== null && context.state !== "closed") void context.close();
    nextScheduledTimeRef.current = 0; firstScheduledTimeRef.current = 0; lastIndexRef.current = null;
    decodeChainRef.current = Promise.resolve();
    lastPositionUpdateRef.current = 0;
  };
  const fail = (): void => {
    cleanupAudio(); generationRef.current = null;
    onPositionChangeRef.current?.(0);
    update({ ...stateRef.current, state: "failed", playbackGeneration: null, inputComplete: false });
    void stopRecordingPlayback().catch(() => undefined);
  };
  const tick = (): void => {
    const context = contextRef.current;
    const current = stateRef.current;
    if (context === null || current.state !== "playing") return;
    const elapsed = Math.max(0, context.currentTime - firstScheduledTimeRef.current);
    const now = performance.now();
    if (now - lastPositionUpdateRef.current >= POSITION_UPDATE_INTERVAL_MS) {
      const position = current.durationSeconds === null ? elapsed : Math.min(elapsed, current.durationSeconds);
      lastPositionUpdateRef.current = now;
      update({ ...current, currentTimeSeconds: position });
      onPositionChangeRef.current?.(position);
    }
    animationFrameRef.current = requestAnimationFrame(tick);
  };
  const maybeFinish = (): void => {
    const current = stateRef.current;
    if (!current.inputComplete || sourcesRef.current.size !== 0) return;
    const position = current.durationSeconds ?? current.currentTimeSeconds;
    update({ ...current, state: "ended", currentTimeSeconds: position });
    onPositionChangeRef.current?.(position);
  };
  const scheduleChunk = async (event: PlaybackChunkEvent): Promise<void> => {
    if (event.generation !== generationRef.current || stateRef.current.state === "failed") return;
    if (lastIndexRef.current !== null && event.segmentIndex <= lastIndexRef.current) { fail(); return; }
    const context = contextRef.current;
    if (context === null) { fail(); return; }
    try {
      const buffer = await context.decodeAudioData(base64Bytes(event.wavPayloadBase64));
      if (event.generation !== generationRef.current || context !== contextRef.current) return;
      const startAt = Math.max(context.currentTime + SCHEDULE_LEAD_SECONDS, nextScheduledTimeRef.current);
      if (firstScheduledTimeRef.current === 0) firstScheduledTimeRef.current = startAt;
      const source = context.createBufferSource();
      source.buffer = buffer; source.connect(context.destination);
      sourcesRef.current.add(source);
      source.onended = () => { sourcesRef.current.delete(source); maybeFinish(); };
      source.start(startAt);
      nextScheduledTimeRef.current = startAt + buffer.duration;
      lastIndexRef.current = event.segmentIndex;
      update({ ...stateRef.current, lastSegmentIndex: event.segmentIndex });
    } catch { fail(); }
  };

  useEffect(() => {
    mountedRef.current = true;
    let cleanup: (() => void) | undefined;
    void subscribeToPlaybackEvents({
      onChunk: (event) => {
        // Decoding is asynchronous; serialising it preserves the strict
        // stream order even when a later WAV decodes more quickly.
        decodeChainRef.current = decodeChainRef.current.then(() => scheduleChunk(event));
      },
      onEnded: (event) => {
        if (event.generation !== generationRef.current) return;
        void decodeChainRef.current.then(() => {
          if (event.generation !== generationRef.current) return;
          update({ ...stateRef.current, inputComplete: true });
          maybeFinish();
        });
      },
      onFailed: (event) => { if (event.generation === generationRef.current) fail(); },
    }).then((unlisten) => { cleanup = unlisten; }).catch(() => fail());
    return () => { mountedRef.current = false; cleanup?.(); cleanupAudio(); generationRef.current = null; void stopRecordingPlayback().catch(() => undefined); };
  // The player is remounted with a Meeting key, keeping listeners session-scoped.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meetingId]);

  const play = async (): Promise<void> => {
    if (stateRef.current.state === "playing" || stateRef.current.state === "loading") return;
    if (stateRef.current.state === "paused") {
      try { await contextRef.current?.resume(); update({ ...stateRef.current, state: "playing" }); tick(); } catch { fail(); }
      return;
    }
    update({ ...stateRef.current, state: "loading" });
    try {
      const context = new AudioContext(); contextRef.current = context;
      const info = await prepareRecordingPlayback(meetingId);
      const generation = await startRecordingPlaybackStream();
      generationRef.current = generation;
      update({ ...stateRef.current, state: "playing", meetingId, playbackGeneration: generation, durationSeconds: info.durationSeconds, captureAnchorUtc: info.captureAnchorUtc, hasGaps: info.hasGaps, currentTimeSeconds: 0, inputComplete: false, lastSegmentIndex: null });
      onPositionChangeRef.current?.(0);
      onTimingAvailableRef.current?.({ captureAnchorUtc: info.captureAnchorUtc, hasGaps: info.hasGaps });
      await activateRecordingPlaybackEvents(generation);
      tick();
    } catch { fail(); }
  };
  const pause = async (): Promise<void> => { try { await contextRef.current?.suspend(); update({ ...stateRef.current, state: "paused" }); } catch { fail(); } };
  const stop = async (): Promise<void> => {
    cleanupAudio();
    generationRef.current = null;
    onPositionChangeRef.current?.(0);
    update({ ...initialPlaybackState, state: "stopped", meetingId, durationSeconds, hasGaps });
    try { await stopRecordingPlayback(); } catch { /* status remains safe */ }
  };
  const playing = state.state === "playing";
  const canPause = playing;
  return <section className="recording-player" aria-labelledby="recording-player-title">
    <h4 id="recording-player-title">Audio Recording</h4>
    {state.hasGaps && <p className="playback-gap">Recording contains capture gaps.</p>}
    <p aria-live="polite">{state.state === "failed" ? GENERIC_FAILURE : `Playback: ${state.state}`}</p>
    <div className="playback-controls">
      <button type="button" disabled={playing || state.state === "loading"} onClick={() => void play()}>{state.state === "paused" ? "Resume" : "Play"}</button>
      <button type="button" disabled={!canPause} onClick={() => void pause()}>Pause</button>
      <button type="button" disabled={state.state === "idle" || state.state === "stopped"} onClick={() => void stop()}>Stop</button>
    </div>
    <p className="playback-time">{timeLabel(state.currentTimeSeconds)} / {timeLabel(state.durationSeconds)}</p>
    <progress max={state.durationSeconds ?? 1} value={Math.min(state.currentTimeSeconds, state.durationSeconds ?? 1)} aria-label="Playback progress" />
  </section>;
}
