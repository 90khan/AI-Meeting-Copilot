import { useEffect, useState } from "react";

import {
  getBackendStatus,
  startBackend,
  stopBackend,
} from "./backend/client";
import type { BackendStatus } from "./backend/types";

const GENERIC_STATUS_ERROR = "The backend status is unavailable.";
const GENERIC_START_ERROR = "The backend could not be started.";
const GENERIC_STOP_ERROR = "The backend could not be stopped.";

function withoutStaleEndpoint(status: BackendStatus): BackendStatus {
  if (status.status === "ready") {
    return status;
  }

  return { ...status, host: null, port: null };
}

function statusLabel(status: BackendStatus | null): string {
  if (status === null) {
    return "Loading";
  }

  return status.status.charAt(0).toUpperCase() + status.status.slice(1);
}

export default function App() {
  const [backendStatus, setBackendStatus] = useState<BackendStatus | null>(null);
  const [pendingOperation, setPendingOperation] = useState<"start" | "stop" | null>(
    null,
  );
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    void getBackendStatus()
      .then((status) => {
        if (isMounted) {
          setBackendStatus(withoutStaleEndpoint(status));
        }
      })
      .catch(() => {
        if (isMounted) {
          setBackendStatus({
            status: "failed",
            host: null,
            port: null,
            message: null,
          });
          setErrorMessage(GENERIC_STATUS_ERROR);
        }
      });

    return () => {
      isMounted = false;
    };
  }, []);

  const handleStart = async (): Promise<void> => {
    setPendingOperation("start");
    setErrorMessage(null);

    try {
      const status = await startBackend();
      setBackendStatus(withoutStaleEndpoint(status));
    } catch {
      setBackendStatus({
        status: "failed",
        host: null,
        port: null,
        message: null,
      });
      setErrorMessage(GENERIC_START_ERROR);
    } finally {
      setPendingOperation(null);
    }
  };

  const handleStop = async (): Promise<void> => {
    setPendingOperation("stop");
    setErrorMessage(null);

    try {
      const status = await stopBackend();
      setBackendStatus(withoutStaleEndpoint(status));
    } catch {
      setBackendStatus({
        status: "failed",
        host: null,
        port: null,
        message: null,
      });
      setErrorMessage(GENERIC_STOP_ERROR);
    } finally {
      setPendingOperation(null);
    }
  };

  const isPending = pendingOperation !== null;
  const canStart =
    !isPending &&
    (backendStatus?.status === "stopped" || backendStatus?.status === "failed");
  const canStop =
    !isPending &&
    (backendStatus?.status === "ready" || backendStatus?.status === "failed");

  return (
    <main className="app-shell">
      <section className="app-panel" aria-labelledby="application-title">
        <h1 id="application-title">AI Meeting Copilot</h1>
        <p className="backend-status" aria-live="polite">
          Backend status: <strong>{statusLabel(backendStatus)}</strong>
        </p>
        {backendStatus?.status === "ready" &&
          backendStatus.host !== null &&
          backendStatus.port !== null && (
            <p className="backend-endpoint">
              Host: {backendStatus.host} · Port: {backendStatus.port}
            </p>
          )}
        {backendStatus?.status === "failed" && (
          <p className="backend-error" role="alert">
            {errorMessage ?? "The backend is unavailable."}
          </p>
        )}
        <div className="actions">
          <button type="button" disabled={!canStart} onClick={handleStart}>
            {pendingOperation === "start" ? "Starting Backend…" : "Start Backend"}
          </button>
          <button type="button" disabled={!canStop} onClick={handleStop}>
            {pendingOperation === "stop" ? "Stopping Backend…" : "Stop Backend"}
          </button>
        </div>
      </section>
    </main>
  );
}
