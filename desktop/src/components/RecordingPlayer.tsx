import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";

import {
  activateRecordingPlaybackEvents,
  prepareRecordingPlayback,
  seekRecordingPlayback,
  startRecordingPlaybackStream,
  stopRecordingPlayback,
} from "../playback/client";
import { subscribeToPlaybackEvents } from "../playback/events";
import { initialPlaybackState } from "../playback/state";
import type {
  PlaybackChunkEvent,
  PlaybackState,
  PlaybackUiState,
  RecordingPlaybackInfo,
} from "../playback/types";

const GENERIC_FAILURE = "Playback is unavailable.";
const SCHEDULE_LEAD_SECONDS = 0.05;
const POSITION_UPDATE_INTERVAL_MS = 200;
const SAMPLE_RATE_HZ = 16_000;

export type RecordingPlayerHandle = Readonly<{
  seekTo: (seconds: number) => void;
}>;

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

export const RecordingPlayer = forwardRef<RecordingPlayerHandle, {
  meetingId: string;
  durationSeconds: number | null;
  hasGaps: boolean;
  onPositionChange?: (seconds: number) => void;
  onPlaybackStateChange?: (state: PlaybackState) => void;
  onTimingAvailable?: (timing: Pick<RecordingPlaybackInfo, "captureAnchorUtc" | "hasGaps">) => void;
}>(function RecordingPlayer({ meetingId, durationSeconds, hasGaps, onPositionChange, onPlaybackStateChange, onTimingAvailable }, ref) {
  const [state, setState] = useState<PlaybackUiState>({ ...initialPlaybackState, meetingId, durationSeconds, hasGaps });
  const [rangePreview, setRangePreview] = useState<number | null>(null);
  const stateRef = useRef(state);
  const contextRef = useRef<AudioContext | null>(null);
  const sourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const generationRef = useRef<number | null>(null);
  const nextScheduledTimeRef = useRef(0);
  const playbackClockStartRef = useRef(0);
  const playbackBaseSecondsRef = useRef(0);
  const firstSegmentOffsetSecondsRef = useRef<number | null>(null);
  const lastIndexRef = useRef<number | null>(null);
  const decodeChainRef = useRef<Promise<void>>(Promise.resolve());
  const animationFrameRef = useRef<number | null>(null);
  const lastPositionUpdateRef = useRef(0);
  const mountedRef = useRef(true);
  const seekRequestRef = useRef(0);
  const pendingSeekRef = useRef<number | null>(null);
  const seekWorkerRef = useRef(false);
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
  const clearScheduledAudio = (): void => {
    if (animationFrameRef.current !== null) cancelAnimationFrame(animationFrameRef.current);
    animationFrameRef.current = null;
    for (const source of sourcesRef.current) { try { source.stop(); } catch { /* already ended */ } }
    sourcesRef.current.clear();
    nextScheduledTimeRef.current = 0;
    playbackClockStartRef.current = 0;
    firstSegmentOffsetSecondsRef.current = null;
    lastIndexRef.current = null;
    decodeChainRef.current = Promise.resolve();
    lastPositionUpdateRef.current = 0;
  };
  const cleanupAudio = (): void => {
    clearScheduledAudio();
    const context = contextRef.current;
    contextRef.current = null;
    if (context !== null && context.state !== "closed") void context.close();
  };
  const fail = (): void => {
    cleanupAudio();
    generationRef.current = null;
    onPositionChangeRef.current?.(0);
    update({ ...stateRef.current, state: "failed", playbackGeneration: null, inputComplete: false });
    void stopRecordingPlayback().catch(() => undefined);
  };
  const tick = (): void => {
    const context = contextRef.current;
    const current = stateRef.current;
    if (context === null || current.state !== "playing") return;
    const elapsed = Math.max(0, context.currentTime - playbackClockStartRef.current);
    const now = performance.now();
    if (now - lastPositionUpdateRef.current >= POSITION_UPDATE_INTERVAL_MS) {
      const position = current.durationSeconds === null
        ? playbackBaseSecondsRef.current + elapsed
        : Math.min(playbackBaseSecondsRef.current + elapsed, current.durationSeconds);
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
      const offsetSeconds = firstSegmentOffsetSecondsRef.current ?? 0;
      if (!Number.isFinite(offsetSeconds) || offsetSeconds < 0 || offsetSeconds >= buffer.duration) { fail(); return; }
      firstSegmentOffsetSecondsRef.current = null;
      const startAt = Math.max(context.currentTime + SCHEDULE_LEAD_SECONDS, nextScheduledTimeRef.current);
      if (playbackClockStartRef.current === 0) playbackClockStartRef.current = startAt;
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      sourcesRef.current.add(source);
      source.onended = () => { sourcesRef.current.delete(source); maybeFinish(); };
      source.start(startAt, offsetSeconds);
      nextScheduledTimeRef.current = startAt + buffer.duration - offsetSeconds;
      lastIndexRef.current = event.segmentIndex;
      update({ ...stateRef.current, lastSegmentIndex: event.segmentIndex });
    } catch { fail(); }
  };

  const prepare = async (): Promise<RecordingPlaybackInfo> => {
    if (stateRef.current.captureAnchorUtc !== null) {
      return {
        meetingId,
        format: "wav_pcm16_mono_16khz_segmented_v1",
        captureAnchorUtc: stateRef.current.captureAnchorUtc,
        durationSeconds: stateRef.current.durationSeconds,
        segmentCount: 0,
        hasGaps: stateRef.current.hasGaps,
      };
    }
    const info = await prepareRecordingPlayback(meetingId);
    if (!mountedRef.current) throw new Error();
    update({ ...stateRef.current, state: "ready", meetingId, durationSeconds: info.durationSeconds, captureAnchorUtc: info.captureAnchorUtc, hasGaps: info.hasGaps });
    onTimingAvailableRef.current?.({ captureAnchorUtc: info.captureAnchorUtc, hasGaps: info.hasGaps });
    return info;
  };
  const executeQueuedSeeks = async (): Promise<void> => {
    if (seekWorkerRef.current) return;
    seekWorkerRef.current = true;
    while (pendingSeekRef.current !== null) {
      const target = pendingSeekRef.current;
      const request = seekRequestRef.current;
      pendingSeekRef.current = null;
      const previousState = stateRef.current.state;
      const resumeAfterSeek = previousState === "playing";
      try {
        clearScheduledAudio();
        // The old stream is no longer allowed to schedule audio while the
        // sidecar resolves and opens the replacement generation.
        generationRef.current = null;
        const context = contextRef.current ?? new AudioContext();
        contextRef.current = context;
        await context.suspend();
        await prepare();
        const result = await seekRecordingPlayback(target);
        if (request !== seekRequestRef.current) continue;
        if (result.atEnd) {
          generationRef.current = null;
          playbackBaseSecondsRef.current = stateRef.current.durationSeconds ?? target;
          onPositionChangeRef.current?.(playbackBaseSecondsRef.current);
          update({ ...stateRef.current, state: "ended", currentTimeSeconds: playbackBaseSecondsRef.current, playbackGeneration: null, inputComplete: true });
          continue;
        }
        if (result.generation === null || result.resolvedSeconds === null || result.offsetSamples === null) throw new Error();
        const offsetSeconds = result.offsetSamples / SAMPLE_RATE_HZ;
        if (!Number.isFinite(offsetSeconds) || offsetSeconds < 0) throw new Error();
        generationRef.current = result.generation;
        firstSegmentOffsetSecondsRef.current = offsetSeconds;
        playbackBaseSecondsRef.current = result.resolvedSeconds;
        onPositionChangeRef.current?.(result.resolvedSeconds);
        update({ ...stateRef.current, state: resumeAfterSeek ? "playing" : "paused", playbackGeneration: result.generation, currentTimeSeconds: result.resolvedSeconds, inputComplete: false, lastSegmentIndex: null });
        await activateRecordingPlaybackEvents(result.generation);
        if (request !== seekRequestRef.current) continue;
        if (resumeAfterSeek) {
          await context.resume();
          tick();
        }
      } catch {
        if (request === seekRequestRef.current) fail();
      }
    }
    seekWorkerRef.current = false;
  };
  const seekTo = (seconds: number): void => {
    if (!Number.isFinite(seconds) || seconds < 0 || stateRef.current.state === "failed") return;
    const duration = stateRef.current.durationSeconds;
    const target = duration === null ? seconds : Math.min(seconds, duration);
    seekRequestRef.current += 1;
    pendingSeekRef.current = target;
    void executeQueuedSeeks();
  };

  useImperativeHandle(ref, () => ({ seekTo }), [meetingId]);

  useEffect(() => {
    mountedRef.current = true;
    let cleanup: (() => void) | undefined;
    void subscribeToPlaybackEvents({
      onChunk: (event) => { decodeChainRef.current = decodeChainRef.current.then(() => scheduleChunk(event)); },
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
    // Loading metadata without opening a stream makes exact transcript timing
    // available before the user presses Play.
    void prepare().catch(() => fail());
    return () => {
      mountedRef.current = false;
      seekRequestRef.current += 1;
      pendingSeekRef.current = null;
      cleanup?.();
      cleanupAudio();
      generationRef.current = null;
      void stopRecordingPlayback().catch(() => undefined);
    };
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
      const context = contextRef.current ?? new AudioContext();
      contextRef.current = context;
      const info = await prepare();
      const generation = await startRecordingPlaybackStream();
      generationRef.current = generation;
      playbackBaseSecondsRef.current = 0;
      firstSegmentOffsetSecondsRef.current = 0;
      update({ ...stateRef.current, state: "playing", meetingId, playbackGeneration: generation, durationSeconds: info.durationSeconds, captureAnchorUtc: info.captureAnchorUtc, hasGaps: info.hasGaps, currentTimeSeconds: 0, inputComplete: false, lastSegmentIndex: null });
      onPositionChangeRef.current?.(0);
      await activateRecordingPlaybackEvents(generation);
      tick();
    } catch { fail(); }
  };
  const pause = async (): Promise<void> => { try { await contextRef.current?.suspend(); update({ ...stateRef.current, state: "paused" }); } catch { fail(); } };
  const stop = async (): Promise<void> => {
    seekRequestRef.current += 1;
    pendingSeekRef.current = null;
    cleanupAudio();
    generationRef.current = null;
    onPositionChangeRef.current?.(0);
    update({ ...initialPlaybackState, state: "stopped", meetingId, durationSeconds, hasGaps });
    try { await stopRecordingPlayback(); } catch { /* status remains safe */ }
  };
  const commitRange = (): void => {
    if (rangePreview !== null) {
      seekTo(rangePreview);
      setRangePreview(null);
    }
  };
  const playing = state.state === "playing";
  const canPause = playing;
  const rangeValue = rangePreview ?? state.currentTimeSeconds;
  const canSeekProgress = state.durationSeconds !== null && state.durationSeconds >= 0 && state.state !== "failed";
  return <section className="recording-player" aria-labelledby="recording-player-title">
    <h4 id="recording-player-title">Audio Recording</h4>
    {state.hasGaps && <p className="playback-gap">Recording contains capture gaps.</p>}
    <p aria-live="polite">{state.state === "failed" ? GENERIC_FAILURE : `Playback: ${state.state}`}</p>
    <div className="playback-controls">
      <button type="button" disabled={playing || state.state === "loading"} onClick={() => void play()}>{state.state === "paused" ? "Resume" : "Play"}</button>
      <button type="button" disabled={!canPause} onClick={() => void pause()}>Pause</button>
      <button type="button" disabled={state.state === "idle" || state.state === "stopped"} onClick={() => void stop()}>Stop</button>
    </div>
    <p className="playback-time">{timeLabel(rangeValue)} / {timeLabel(state.durationSeconds)}</p>
    <input
      className="playback-progress"
      type="range"
      min="0"
      max={state.durationSeconds ?? 0}
      step="0.1"
      value={Math.min(rangeValue, state.durationSeconds ?? 0)}
      disabled={!canSeekProgress}
      aria-label="Seek playback"
      onChange={(event) => setRangePreview(Number(event.currentTarget.value))}
      onPointerUp={commitRange}
      onKeyUp={(event) => {
        if (["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(event.key)) commitRange();
      }}
      onBlur={commitRange}
    />
  </section>;
});
