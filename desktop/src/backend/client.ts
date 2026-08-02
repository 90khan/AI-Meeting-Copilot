import { invoke } from "@tauri-apps/api/core";

import type {
  AudioCaptureStatus,
  BackendStatus,
  CaptureAuthorization,
  CaptureDisplaySource,
  CaptureMicrophoneSource,
  LiveTranscriptionStatus,
  StartLiveTranscriptionSessionInput,
  StartAudioCaptureInput,
} from "./types";

export function startBackend(): Promise<BackendStatus> {
  return invoke<BackendStatus>("start_backend");
}

export function stopBackend(): Promise<BackendStatus> {
  return invoke<BackendStatus>("stop_backend");
}

export function getBackendStatus(): Promise<BackendStatus> {
  return invoke<BackendStatus>("get_backend_status");
}

export function getLiveTranscriptionStatus(): Promise<LiveTranscriptionStatus> {
  return invoke<LiveTranscriptionStatus>("get_live_transcription_status");
}

export function startLiveTranscriptionSession(
  input: StartLiveTranscriptionSessionInput,
): Promise<LiveTranscriptionStatus> {
  if (
    input.assistMode.enabled &&
    input.assistMode.simplificationEnabled &&
    input.assistMode.simplificationLevel === null
  ) {
    return Promise.reject(new Error("Invalid Assist Mode configuration."));
  }
  return invoke<LiveTranscriptionStatus>("start_live_transcription_session", { input });
}

export function getAudioCaptureStatus(): Promise<AudioCaptureStatus> {
  return invoke<AudioCaptureStatus>("get_audio_capture_status");
}

export function getCaptureAuthorization(): Promise<CaptureAuthorization> {
  return invoke<CaptureAuthorization>("get_capture_authorization");
}

export function requestCaptureAuthorization(): Promise<CaptureAuthorization> {
  return invoke<CaptureAuthorization>("request_capture_authorization");
}

export function listCaptureDisplays(): Promise<CaptureDisplaySource[]> {
  return invoke<CaptureDisplaySource[]>("list_capture_displays");
}

export function listCaptureMicrophones(): Promise<CaptureMicrophoneSource[]> {
  return invoke<CaptureMicrophoneSource[]>("list_capture_microphones");
}

export function startAudioCapture(
  input: StartAudioCaptureInput,
): Promise<AudioCaptureStatus> {
  return invoke<AudioCaptureStatus>("start_audio_capture", { input });
}

export function stopAudioCapture(): Promise<AudioCaptureStatus> {
  return invoke<AudioCaptureStatus>("stop_audio_capture");
}
