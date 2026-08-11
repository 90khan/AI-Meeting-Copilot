import { useEffect, useReducer, useState } from "react";

import { subscribeToAssistEvents } from "./assist/events";
import { assistReducer, initialAssistState } from "./assist/state";
import { AssistModePanel } from "./components/AssistModePanel";
import { MeetingHistoryPanel } from "./components/MeetingHistoryPanel";

import {
  getAudioCaptureStatus,
  getBackendStatus,
  getCaptureAuthorization,
  getLiveTranscriptionStatus,
  listCaptureDisplays,
  listCaptureMicrophones,
  requestCaptureAuthorization,
  startAudioCapture,
  startBackend,
  connectLiveTranscription,
  createMeeting,
  disconnectLiveTranscription,
  endLiveTranscriptionSession,
  startLiveTranscriptionSession,
  startMeeting,
  stopAudioCapture,
  stopBackend,
} from "./backend/client";
import type {
  AudioCaptureStatus,
  BackendStatus,
  CaptureAuthorization,
  CaptureDisplaySource,
  CaptureMicrophoneSource,
  LiveTranscriptionStatus,
  AssistModeSessionConfiguration,
} from "./backend/types";

const GENERIC_STATUS_ERROR = "The backend status is unavailable.";
const GENERIC_START_ERROR = "The backend could not be started.";
const GENERIC_STOP_ERROR = "The backend could not be stopped.";
const GENERIC_CAPTURE_ERROR = "Audio capture is unavailable.";
const LIVE_TRANSCRIPTION_CONNECT_STAGES = new Set([
  "sidecar_connection",
  "websocket_open",
  "hello_serialize",
  "hello_send",
  "hello_ack_receive",
  "hello_ack_parse",
]);
const LIVE_TRANSCRIPTION_SESSION_START_STAGES = new Set([
  "session_meeting_validation",
  "session_factory",
  "session_assist_initialization",
  "session_started_send",
]);

function connectionFailureMessage(error: unknown): string {
  const message = typeof error === "string"
    ? error
    : error instanceof Error
      ? error.message
      : "";
  const matched = /^The live transcription connection could not be established \(([^)]+)\)\.$/.exec(message);
  const stage = matched?.[1];
  return stage !== undefined && LIVE_TRANSCRIPTION_CONNECT_STAGES.has(stage)
    ? `The live transcription connection could not be established (${stage}).`
    : "The live transcription connection could not be established.";
}

function sessionStartFailureMessage(error: unknown): string {
  const message = typeof error === "string"
    ? error
    : error instanceof Error
      ? error.message
      : "";
  const matched = /^The live transcription session could not be started \(([^)]+)\)\.$/.exec(message);
  const stage = matched?.[1];
  return stage !== undefined && LIVE_TRANSCRIPTION_SESSION_START_STAGES.has(stage)
    ? `The live transcription session could not be started (${stage}).`
    : "The live transcription session could not be started.";
}

function withoutStaleEndpoint(status: BackendStatus): BackendStatus {
  return status.status === "ready" ? status : { ...status, host: null, port: null };
}

function statusLabel(status: string | null): string {
  if (status === null) return "Loading";
  return status
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter: string) => letter.toUpperCase());
}

export default function App() {
  const [view, setView] = useState<"live" | "history">("live");
  const [backendStatus, setBackendStatus] = useState<BackendStatus | null>(null);
  const [liveStatus, setLiveStatus] = useState<LiveTranscriptionStatus | null>(null);
  const [captureStatus, setCaptureStatus] = useState<AudioCaptureStatus | null>(null);
  const [authorization, setAuthorization] = useState<CaptureAuthorization | null>(null);
  const [displays, setDisplays] = useState<CaptureDisplaySource[]>([]);
  const [microphones, setMicrophones] = useState<CaptureMicrophoneSource[]>([]);
  const [displayId, setDisplayId] = useState<string>("");
  const [microphoneId, setMicrophoneId] = useState<string>("");
  const [includeSystemAudio, setIncludeSystemAudio] = useState(true);
  const [includeMicrophone, setIncludeMicrophone] = useState(true);
  const [pendingOperation, setPendingOperation] = useState<
    "start-backend" | "stop-backend" | "permissions" | "start-capture" | "stop-capture" | "start-session" | "end-session" | null
  >(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [assistState, dispatchAssist] = useReducer(assistReducer, initialAssistState);
  const [assistEnabled, setAssistEnabled] = useState(true);
  const [translationEnabled, setTranslationEnabled] = useState(true);
  const [simplificationEnabled, setSimplificationEnabled] = useState(false);
  const [simplificationLevel, setSimplificationLevel] = useState<"b1" | "b2">("b1");
  const [replyCoachingEnabled, setReplyCoachingEnabled] = useState(true);
  const [meetingName, setMeetingName] = useState("");
  const [activeMeetingId, setActiveMeetingId] = useState<string | null>(null);
  const assistSessionConfiguration: AssistModeSessionConfiguration = {
    enabled: assistEnabled,
    translationEnabled,
    simplificationEnabled,
    simplificationLevel: simplificationEnabled ? simplificationLevel : null,
    replyCoachingEnabled,
  };

  useEffect(() => {
    let isMounted = true;
    void getBackendStatus()
      .then((status) => isMounted && setBackendStatus(withoutStaleEndpoint(status)))
      .catch(() => {
        if (isMounted) {
          setBackendStatus({ status: "failed", host: null, port: null, message: null });
          setErrorMessage(GENERIC_STATUS_ERROR);
        }
      });
    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    let disposed = false;
    let cleanup: (() => void) | undefined;
    void subscribeToAssistEvents((action) => {
      if (!disposed) dispatchAssist(action);
    }).then((unsubscribe) => {
      if (disposed) unsubscribe();
      else cleanup = unsubscribe;
    }).catch(() => {
      if (!disposed) dispatchAssist({ type: "connection", status: "failed" });
    });
    return () => {
      disposed = true;
      cleanup?.();
    };
  }, []);

  useEffect(() => {
    if (backendStatus?.status !== "ready" || liveStatus?.status === "disconnected") {
      dispatchAssist({ type: "clear" });
      return;
    }
    dispatchAssist({ type: "connection", status: liveStatus?.status === "session_active" ? "active" : "inactive" });
  }, [backendStatus?.status, liveStatus?.status]);

  useEffect(() => {
    if (backendStatus?.status !== "ready") return;
    let isMounted = true;
    void Promise.allSettled([
      getLiveTranscriptionStatus(),
      getAudioCaptureStatus(),
      getCaptureAuthorization(),
      listCaptureDisplays(),
      listCaptureMicrophones(),
    ])
      .then(([live, capture, auth, availableDisplays, availableMicrophones]) => {
        if (!isMounted) return;
        if (live.status === "fulfilled") setLiveStatus(live.value);
        if (capture.status === "fulfilled") setCaptureStatus(capture.value);
        if (auth.status === "fulfilled") setAuthorization(auth.value);
        if (availableDisplays.status === "fulfilled") {
          setDisplays(availableDisplays.value);
          setDisplayId((current) => current || String(availableDisplays.value[0]?.id ?? ""));
        } else {
          setDisplays([]);
        }
        if (availableMicrophones.status === "fulfilled") {
          setMicrophones(availableMicrophones.value);
          setMicrophoneId((current) => current || (availableMicrophones.value[0]?.id ?? ""));
        } else {
          setMicrophones([]);
        }
        if (
          capture.status === "rejected" ||
          auth.status === "rejected" ||
          availableDisplays.status === "rejected" ||
          availableMicrophones.status === "rejected"
        ) setErrorMessage(GENERIC_CAPTURE_ERROR);
      });
    return () => {
      isMounted = false;
    };
  }, [backendStatus?.status]);

  const handleBackendStart = async (): Promise<void> => {
    setPendingOperation("start-backend");
    setErrorMessage(null);
    try {
      setBackendStatus(withoutStaleEndpoint(await startBackend()));
    } catch {
      setBackendStatus({ status: "failed", host: null, port: null, message: null });
      setErrorMessage(GENERIC_START_ERROR);
    } finally {
      setPendingOperation(null);
    }
  };

  const handleBackendStop = async (): Promise<void> => {
    setPendingOperation("stop-backend");
    setErrorMessage(null);
    try {
      setBackendStatus(withoutStaleEndpoint(await stopBackend()));
    } catch {
      setErrorMessage(GENERIC_STOP_ERROR);
    } finally {
      setPendingOperation(null);
    }
  };

  const handleRequestPermissions = async (): Promise<void> => {
    setPendingOperation("permissions");
    setErrorMessage(null);
    try {
      setAuthorization(await requestCaptureAuthorization());
    } catch {
      setErrorMessage(GENERIC_CAPTURE_ERROR);
    } finally {
      setPendingOperation(null);
    }
  };

  const handleStartCapture = async (): Promise<void> => {
    setPendingOperation("start-capture");
    setErrorMessage(null);
    try {
      setCaptureStatus(
        await startAudioCapture({
          displayId: Number(displayId),
          microphoneDeviceId: microphoneId || null,
          includeSystemAudio,
          includeMicrophone,
          excludeCurrentProcessAudio: true,
        }),
      );
    } catch {
      setCaptureStatus({ state: "failed", message: null });
      setErrorMessage(GENERIC_CAPTURE_ERROR);
    } finally {
      setPendingOperation(null);
    }
  };

  const handleStopCapture = async (): Promise<void> => {
    setPendingOperation("stop-capture");
    setErrorMessage(null);
    try {
      setCaptureStatus(await stopAudioCapture());
    } catch {
      setErrorMessage(GENERIC_CAPTURE_ERROR);
    } finally {
      setPendingOperation(null);
    }
  };

  const handleStartSession = async (): Promise<void> => {
    if (!backendReady || meetingName.trim().length === 0) {
      setErrorMessage("A Meeting name is required.");
      return;
    }
    setPendingOperation("start-session");
    setErrorMessage(null);
    try {
      let connection: LiveTranscriptionStatus;
      try {
        connection = liveStatus?.status === "connected" || liveStatus?.status === "session_active"
          ? liveStatus
          : await connectLiveTranscription();
        setLiveStatus(connection);
        if (connection.status !== "connected") throw new Error("Session unavailable");
      } catch (error) {
        setErrorMessage(connectionFailureMessage(error));
        return;
      }

      let meeting: Awaited<ReturnType<typeof createMeeting>>;
      try {
        meeting = await createMeeting(meetingName.trim());
      } catch {
        setErrorMessage("The Meeting could not be created.");
        return;
      }

      try {
        await startMeeting(meeting.meetingId);
      } catch {
        setErrorMessage("The Meeting could not be started.");
        return;
      }

      try {
        const session = await startLiveTranscriptionSession({
          meetingId: meeting.meetingId,
          languageHint: "de",
          source: "mixed",
          assistMode: assistSessionConfiguration,
        });
        setActiveMeetingId(meeting.meetingId);
        setLiveStatus(session);
        dispatchAssist({ type: "connection", status: "active" });
      } catch (error) {
        setErrorMessage(sessionStartFailureMessage(error));
      }
    } finally {
      setPendingOperation(null);
    }
  };

  const handleEndSession = async (): Promise<void> => {
    setPendingOperation("end-session");
    setErrorMessage(null);
    try {
      if (captureStatus?.state === "capturing") setCaptureStatus(await stopAudioCapture());
      if (liveStatus?.status === "session_active") setLiveStatus(await endLiveTranscriptionSession());
      setLiveStatus(await disconnectLiveTranscription());
      setActiveMeetingId(null);
      dispatchAssist({ type: "clear" });
    } catch {
      setErrorMessage("The live session could not be ended.");
    } finally {
      setPendingOperation(null);
    }
  };

  const isPending = pendingOperation !== null;
  const backendReady = backendStatus?.status === "ready";
  const liveSessionActive = liveStatus?.status === "session_active";
  const requiredPermissionsGranted =
    (!includeSystemAudio || authorization?.screenCapture === "authorized") &&
    (!includeMicrophone || authorization?.microphone === "authorized");
  const sourceSelected = displayId !== "" && (includeSystemAudio || includeMicrophone);
  const canStartCapture =
    !isPending &&
    backendReady &&
    liveSessionActive &&
    requiredPermissionsGranted &&
    sourceSelected &&
    captureStatus?.state !== "capturing";
  const canStopCapture =
    !isPending &&
    (captureStatus?.state === "capturing" || captureStatus?.state === "failed");
  const canStartBackend =
    !isPending && (backendStatus?.status === "stopped" || backendStatus?.status === "failed");
  const canStopBackend =
    !isPending && (backendStatus?.status === "ready" || backendStatus?.status === "failed");
  const controlsDisabled = isPending || !backendReady;
  const assistControlsDisabled = liveSessionActive;
  const canStartSession = !isPending && backendReady && !liveSessionActive && meetingName.trim().length > 0;
  const canEndSession = !isPending && liveSessionActive;

  return (
    <main className="app-shell">
      <section className="app-panel" aria-labelledby="application-title">
        <h1 id="application-title">AI Meeting Copilot</h1>
        <nav className="view-switch" aria-label="Application views">
          <button
            type="button"
            aria-pressed={view === "live"}
            onClick={() => setView("live")}
          >
            Live
          </button>
          <button
            type="button"
            aria-pressed={view === "history"}
            onClick={() => setView("history")}
          >
            History
          </button>
        </nav>
        {view === "history" ? (
          <MeetingHistoryPanel backendReady={backendStatus?.status === "ready"} />
        ) : (
          <>
        <p className="backend-status" aria-live="polite">
          Backend status: <strong>{statusLabel(backendStatus?.status ?? null)}</strong>
        </p>
        {backendReady && backendStatus?.host && backendStatus.port !== null && (
          <p className="backend-endpoint">Host: {backendStatus.host} · Port: {backendStatus.port}</p>
        )}
        <div className="actions">
          <button type="button" disabled={!canStartBackend} onClick={handleBackendStart}>
            {pendingOperation === "start-backend" ? "Starting Backend…" : "Start Backend"}
          </button>
          <button type="button" disabled={!canStopBackend} onClick={handleBackendStop}>
            {pendingOperation === "stop-backend" ? "Stopping Backend…" : "Stop Backend"}
          </button>
        </div>

        <section className="session-panel" aria-labelledby="session-title">
          <h2 id="session-title">Live session</h2>
          <label>
            Meeting name
            <input value={meetingName} disabled={liveSessionActive || isPending} onChange={(event) => setMeetingName(event.target.value)} />
          </label>
          <div className="actions">
            <button type="button" disabled={!canStartSession} onClick={handleStartSession}>
              {pendingOperation === "start-session" ? "Starting Session…" : "Start Session"}
            </button>
            <button type="button" disabled={!canEndSession} onClick={handleEndSession}>
              {pendingOperation === "end-session" ? "Ending Session…" : "End Session"}
            </button>
          </div>
          {activeMeetingId !== null && <p aria-live="polite">Session active.</p>}
        </section>

        <section className="capture-panel" aria-labelledby="capture-title">
          <h2 id="capture-title">Native Audio Capture</h2>
          <p aria-live="polite">Capture status: <strong>{statusLabel(captureStatus?.state ?? null)}</strong></p>
          <p>Authorization: Screen capture {statusLabel(authorization?.screenCapture ?? null)} · Microphone {statusLabel(authorization?.microphone ?? null)}</p>
          <p>Live transcription: {statusLabel(liveStatus?.status ?? null)}</p>
          <button type="button" disabled={controlsDisabled} onClick={handleRequestPermissions}>
            {pendingOperation === "permissions" ? "Requesting Permissions…" : "Request Permissions"}
          </button>
          <fieldset disabled={controlsDisabled}>
            <legend>Capture sources</legend>
            <label>
              Display
              <select value={displayId} onChange={(event) => setDisplayId(event.target.value)}>
                <option value="">Select a display</option>
                {displays.map((display) => (
                  <option key={display.id} value={display.id}>
                    Display {display.id} · {display.width}×{display.height}{display.isPrimary ? " · Primary" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Microphone
              <select value={microphoneId} onChange={(event) => setMicrophoneId(event.target.value)}>
                <option value="">System default</option>
                {microphones.map((microphone) => (
                  <option key={microphone.id} value={microphone.id}>
                    {microphone.isDefault ? "Default microphone" : "Available microphone"}
                  </option>
                ))}
              </select>
            </label>
            <label><input type="checkbox" checked={includeSystemAudio} onChange={(event) => setIncludeSystemAudio(event.target.checked)} /> Include system audio</label>
            <label><input type="checkbox" checked={includeMicrophone} onChange={(event) => setIncludeMicrophone(event.target.checked)} /> Include microphone</label>
          </fieldset>
          <div className="actions">
            <button type="button" disabled={!canStartCapture} onClick={handleStartCapture}>
              {pendingOperation === "start-capture" ? "Starting Capture…" : "Start Capture"}
            </button>
            <button type="button" disabled={!canStopCapture} onClick={handleStopCapture}>
              {pendingOperation === "stop-capture" ? "Stopping Capture…" : "Stop Capture"}
            </button>
          </div>
        </section>
        <section className="assist-controls" aria-labelledby="assist-controls-title">
          <h2 id="assist-controls-title">Assist configuration</h2>
          <label><input type="checkbox" checked={assistEnabled} disabled={assistControlsDisabled} onChange={(event) => setAssistEnabled(event.target.checked)} /> Enable Assist Mode</label>
          <label><input type="checkbox" checked={translationEnabled} disabled={!assistEnabled || assistControlsDisabled} onChange={(event) => setTranslationEnabled(event.target.checked)} /> Turkish translation</label>
          <label><input type="checkbox" checked={simplificationEnabled} disabled={!assistEnabled || assistControlsDisabled} onChange={(event) => setSimplificationEnabled(event.target.checked)} /> German simplification</label>
          <label>
            Simplification level
            <select value={simplificationLevel} disabled={!assistEnabled || !simplificationEnabled || assistControlsDisabled} onChange={(event) => setSimplificationLevel(event.target.value as "b1" | "b2")}>
              <option value="b1">B1</option><option value="b2">B2</option>
            </select>
          </label>
          <label><input type="checkbox" checked={replyCoachingEnabled} disabled={!assistEnabled || assistControlsDisabled} onChange={(event) => setReplyCoachingEnabled(event.target.checked)} /> Reply coaching</label>
        </section>
        {assistEnabled && <AssistModePanel state={assistState} />}
        {(errorMessage || backendStatus?.status === "failed" || captureStatus?.state === "failed") && (
          <p className="backend-error" role="alert">{errorMessage ?? "A local component is unavailable."}</p>
        )}
          </>
        )}
      </section>
    </main>
  );
}
