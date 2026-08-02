import { invoke } from "@tauri-apps/api/core";

import type { BackendStatus } from "./types";

export function startBackend(): Promise<BackendStatus> {
  return invoke<BackendStatus>("start_backend");
}

export function stopBackend(): Promise<BackendStatus> {
  return invoke<BackendStatus>("stop_backend");
}

export function getBackendStatus(): Promise<BackendStatus> {
  return invoke<BackendStatus>("get_backend_status");
}
