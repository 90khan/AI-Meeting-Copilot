export type BackendLifecycleStatus =
  | "stopped"
  | "starting"
  | "ready"
  | "failed"
  | "stopping";

/** Public, non-sensitive state returned by the Tauri sidecar manager. */
export interface BackendStatus {
  status: BackendLifecycleStatus;
  host: string | null;
  port: number | null;
  message: string | null;
}

export type LiveTranscriptionLifecycleStatus =
  | "disconnected"
  | "connecting"
  | "connected"
  | "session_active"
  | "stopping"
  | "failed";

export interface LiveTranscriptionStatus {
  status: LiveTranscriptionLifecycleStatus;
  message: string | null;
}

export interface AssistModeSessionConfiguration {
  enabled: boolean;
  translationEnabled: boolean;
  simplificationEnabled: boolean;
  simplificationLevel: "b1" | "b2" | null;
  replyCoachingEnabled: boolean;
}

export interface StartLiveTranscriptionSessionInput {
  meetingId: string;
  languageHint: string | null;
  source: "mixed" | "microphone" | "system_audio";
  assistMode: AssistModeSessionConfiguration;
}

export type CaptureAuthorizationState =
  | "not_determined"
  | "authorized"
  | "denied"
  | "restricted";

export interface CaptureAuthorization {
  screenCapture: CaptureAuthorizationState;
  microphone: CaptureAuthorizationState;
}

export interface CaptureDisplaySource {
  id: number;
  width: number;
  height: number;
  isPrimary: boolean;
}

export interface CaptureMicrophoneSource {
  id: string;
  isDefault: boolean;
}

export type AudioCaptureState =
  | "stopped"
  | "starting"
  | "capturing"
  | "stopping"
  | "failed";

export interface AudioCaptureStatus {
  state: AudioCaptureState;
  message: string | null;
}

export interface StartAudioCaptureInput {
  displayId: number;
  microphoneDeviceId: string | null;
  includeSystemAudio: boolean;
  includeMicrophone: boolean;
  excludeCurrentProcessAudio: boolean;
}
