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
