import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import type { PlaybackChunkEvent, PlaybackTerminalEvent } from "./types";

function isGeneration(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function chunkFrom(value: unknown): PlaybackChunkEvent | null {
  if (value === null || typeof value !== "object") return null;
  const event = value as Record<string, unknown>;
  if (!isGeneration(event.generation) || typeof event.segmentIndex !== "number" ||
    !Number.isInteger(event.segmentIndex) || event.segmentIndex < 0 ||
    typeof event.wavPayloadBase64 !== "string" || event.wavPayloadBase64.length === 0) return null;
  return { generation: event.generation, segmentIndex: event.segmentIndex, wavPayloadBase64: event.wavPayloadBase64 };
}

function terminalFrom(value: unknown): PlaybackTerminalEvent | null {
  if (value === null || typeof value !== "object") return null;
  const event = value as Record<string, unknown>;
  return isGeneration(event.generation) ? { generation: event.generation } : null;
}

export async function subscribeToPlaybackEvents(callbacks: {
  onChunk: (event: PlaybackChunkEvent) => void;
  onEnded: (event: PlaybackTerminalEvent) => void;
  onFailed: (event: PlaybackTerminalEvent) => void;
}): Promise<() => void> {
  let active = true;
  const listeners = await Promise.all([
    listen("playback://chunk", (event) => { const payload = chunkFrom(event.payload); if (active && payload) callbacks.onChunk(payload); }),
    listen("playback://ended", (event) => { const payload = terminalFrom(event.payload); if (active && payload) callbacks.onEnded(payload); }),
    listen("playback://failed", (event) => { const payload = terminalFrom(event.payload); if (active && payload) callbacks.onFailed(payload); }),
  ]);
  return () => { active = false; listeners.forEach((unlisten: UnlistenFn) => unlisten()); };
}
