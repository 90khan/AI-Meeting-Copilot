import { invoke } from "@tauri-apps/api/core";

import type { RecordingPlaybackInfo } from "./types";

function validInfo(value: unknown): value is RecordingPlaybackInfo {
  if (value === null || typeof value !== "object") return false;
  const info = value as Record<string, unknown>;
  return typeof info.meetingId === "string" &&
    info.format === "wav_pcm16_mono_16khz_segmented_v1" &&
    (info.durationSeconds === null || (typeof info.durationSeconds === "number" && Number.isFinite(info.durationSeconds) && info.durationSeconds >= 0)) &&
    typeof info.segmentCount === "number" && Number.isInteger(info.segmentCount) && info.segmentCount >= 0 &&
    typeof info.hasGaps === "boolean";
}

export async function prepareRecordingPlayback(meetingId: string): Promise<RecordingPlaybackInfo> {
  try {
    const status = await invoke<unknown>("prepare_recording_playback", { meetingId });
    if (status === null || typeof status !== "object") throw new Error();
    const info = (status as Record<string, unknown>).info;
    if (!validInfo(info) || info.meetingId !== meetingId) throw new Error();
    return info;
  } catch {
    throw new Error("Playback is unavailable.");
  }
}

export async function startRecordingPlaybackStream(): Promise<number> {
  try {
    const generation = await invoke<unknown>("start_recording_playback_stream");
    if (typeof generation !== "number" || !Number.isSafeInteger(generation) || generation < 0) throw new Error();
    return generation;
  } catch {
    throw new Error("Playback is unavailable.");
  }
}

export async function activateRecordingPlaybackEvents(generation: number): Promise<void> {
  try {
    await invoke("activate_recording_playback_events", { generation });
  } catch {
    throw new Error("Playback is unavailable.");
  }
}

export async function stopRecordingPlayback(): Promise<void> {
  try {
    await invoke("stop_recording_playback");
  } catch {
    throw new Error("Playback is unavailable.");
  }
}
