import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  generateMeetingTranslation,
  getMeetingDetail,
  getMeetingTranslation,
  listMeetings,
} from "../history/client";
import type { MeetingDetail, MeetingHistoryItem } from "../history/types";
import type { MeetingTranslationArtifact } from "../history/translationTypes";
import { buildTranscriptTimeline, findActiveTranscriptId } from "../playback/transcriptSync";
import type { PlaybackState } from "../playback/types";
import { MeetingReviewPanel } from "./MeetingReviewPanel";
import { RecordingPlayer, type RecordingPlayerHandle } from "./RecordingPlayer";

const HISTORY_LIMIT = 100;

function displayTimestamp(value: string | null): string {
  if (value === null) return "Not available";
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.valueOf())
    ? "Not available"
    : timestamp.toLocaleString();
}

function recordingLabel(item: MeetingHistoryItem): string {
  return item.recordingAvailable ? "Available" : "Not available";
}

export function MeetingHistoryPanel({ backendReady }: { backendReady: boolean }) {
  const [meetings, setMeetings] = useState<MeetingHistoryItem[]>([]);
  const [detail, setDetail] = useState<MeetingDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [failed, setFailed] = useState(false);

  const loadHistory = useCallback(async () => {
    if (!backendReady) return;
    setLoading(true);
    setFailed(false);
    try {
      const response = await listMeetings(HISTORY_LIMIT, 0);
      setMeetings(response.meetings);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [backendReady]);

  useEffect(() => {
    let active = true;
    if (!backendReady) {
      setMeetings([]);
      setDetail(null);
      setFailed(false);
      return () => {
        active = false;
      };
    }
    setLoading(true);
    setFailed(false);
    void listMeetings(HISTORY_LIMIT, 0)
      .then((response) => {
        if (active) setMeetings(response.meetings);
      })
      .catch(() => {
        if (active) setFailed(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [backendReady]);

  const selectMeeting = async (meetingId: string): Promise<void> => {
    setLoadingDetail(true);
    setFailed(false);
    try {
      setDetail(await getMeetingDetail(meetingId));
    } catch {
      setFailed(true);
    } finally {
      setLoadingDetail(false);
    }
  };

  if (!backendReady) {
    return (
      <section className="history-panel" aria-labelledby="history-title">
        <h2 id="history-title">Meeting History</h2>
        <p aria-live="polite">Start the backend to view persisted Meetings.</p>
      </section>
    );
  }

  return (
    <section className="history-panel" aria-labelledby="history-title">
      <header className="history-header">
        <h2 id="history-title">Meeting History</h2>
        <button type="button" disabled={loading} onClick={() => void loadHistory()}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </header>
      {failed && (
        <p className="backend-error" role="alert">
          Meeting History is unavailable.
        </p>
      )}
      {!loading && !failed && meetings.length === 0 && <p>No persisted Meetings yet.</p>}
      <div className="history-content">
        <ol className="meeting-history-list" aria-label="Persisted Meetings">
          {meetings.map((meeting) => (
            <li key={meeting.meetingId}>
              <button
                type="button"
                className="meeting-history-item"
                aria-pressed={detail?.meetingId === meeting.meetingId}
                onClick={() => void selectMeeting(meeting.meetingId)}
              >
                <strong>{meeting.title}</strong>
                <span>Created: {displayTimestamp(meeting.createdAt)}</span>
                <span>Started: {displayTimestamp(meeting.startedAt)}</span>
                <span>Ended: {displayTimestamp(meeting.endedAt)}</span>
                <span>Status: {meeting.status}</span>
                <span>Transcript: {meeting.transcriptCount}</span>
                <span>Recording: {recordingLabel(meeting)}</span>
              </button>
            </li>
          ))}
        </ol>
        <MeetingDetailPanel detail={detail} loading={loadingDetail} />
      </div>
    </section>
  );
}

function MeetingDetailPanel({
  detail,
  loading,
}: {
  detail: MeetingDetail | null;
  loading: boolean;
}) {
  if (loading) return <p aria-live="polite">Loading Meeting transcript…</p>;
  if (detail === null) return <p>Select a Meeting to view its original transcript.</p>;
  return <MeetingDetailContent key={detail.meetingId} detail={detail} />;
}

function MeetingDetailContent({ detail }: { detail: MeetingDetail }) {
  const playbackRef = useRef<RecordingPlayerHandle>(null);
  const [timing, setTiming] = useState<{ captureAnchorUtc: string; hasGaps: boolean } | null>(null);
  const [positionSeconds, setPositionSeconds] = useState(0);
  const [syncActive, setSyncActive] = useState(false);
  const [playbackState, setPlaybackState] = useState<PlaybackState>("idle");
  const timeline = useMemo(
    () => timing === null || timing.hasGaps
      ? null
      : buildTranscriptTimeline(detail.transcript, timing.captureAnchorUtc),
    [detail.transcript, timing],
  );
  const activeTranscriptId = useMemo(
    () => syncActive && timeline !== null
      ? findActiveTranscriptId(timeline, positionSeconds)
      : null,
    [positionSeconds, syncActive, timeline],
  );
  const synchronizationUnavailable = detail.audioHasGaps || timing?.hasGaps === true || (timing !== null && timeline === null);
  const timelineByTranscriptId = useMemo(
    () => timeline === null ? new Map<string, number>() : new Map(
      timeline.map((entry) => [entry.transcriptId, entry.relativeSeconds]),
    ),
    [timeline],
  );
  const canSeekTranscript = detail.recordingAvailable && !synchronizationUnavailable && timeline !== null;
  const seekTranscript = (transcriptId: string): void => {
    if (!canSeekTranscript) return;
    const target = timelineByTranscriptId.get(transcriptId);
    if (target === undefined || !Number.isFinite(target) || target < 0) return;
    playbackRef.current?.seekTo(target);
  };
  const handlePlaybackStateChange = (state: PlaybackState): void => {
    setPlaybackState(state);
    if (state === "stopped" || state === "failed" || state === "loading") {
      setSyncActive(false);
      if (state !== "loading") setPositionSeconds(0);
      return;
    }
    if (state === "playing" || state === "paused" || state === "ended") setSyncActive(true);
  };
  return (
    <article className="meeting-detail" aria-labelledby="meeting-detail-title">
      <h3 id="meeting-detail-title">{detail.title}</h3>
      <p>Status: {detail.status}</p>
      <p>Started: {displayTimestamp(detail.startedAt)}</p>
      <p>Ended: {displayTimestamp(detail.endedAt)}</p>
      {detail.recordingAvailable && (
        <RecordingPlayer
          key={detail.meetingId}
          ref={playbackRef}
          meetingId={detail.meetingId}
          durationSeconds={detail.recordingDurationSeconds}
          hasGaps={detail.audioHasGaps}
          onPositionChange={setPositionSeconds}
          onPlaybackStateChange={handlePlaybackStateChange}
          onTimingAvailable={setTiming}
        />
      )}
      <MeetingTranslationPanel
        key={detail.meetingId}
        detail={detail}
        activeTranscriptId={activeTranscriptId}
        synchronizationUnavailable={synchronizationUnavailable}
        canSeekTranscript={canSeekTranscript}
        onSeekTranscript={seekTranscript}
        playbackState={playbackState}
      />
      <MeetingReviewPanel key={detail.meetingId} meetingId={detail.meetingId} />
    </article>
  );
}

type TranslationState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; artifact: MeetingTranslationArtifact }
  | { kind: "generating" }
  | { kind: "unavailable" }
  | { kind: "failed" };

type TranscriptDisplay = "original" | "turkish" | "side-by-side";

function MeetingTranslationPanel({
  detail,
  activeTranscriptId,
  synchronizationUnavailable,
  canSeekTranscript,
  onSeekTranscript,
  playbackState,
}: {
  detail: MeetingDetail;
  activeTranscriptId: string | null;
  synchronizationUnavailable: boolean;
  canSeekTranscript: boolean;
  onSeekTranscript: (transcriptId: string) => void;
  playbackState: PlaybackState;
}) {
  const [translation, setTranslation] = useState<TranslationState>({ kind: "idle" });
  const [display, setDisplay] = useState<TranscriptDisplay>("original");
  const [autoFollowEnabled, setAutoFollowEnabled] = useState(true);
  const mounted = useRef(true);
  const transcriptContainerRef = useRef<HTMLDivElement>(null);
  const transcriptRowRefs = useRef(new Map<string, HTMLElement>());
  const lastAutoScrolledTranscriptId = useRef<string | null>(null);
  const manualScrollIntentUntil = useRef(0);

  useEffect(() => {
    mounted.current = true;
    setTranslation({ kind: "loading" });
    setDisplay("original");
    void getMeetingTranslation(detail.meetingId)
      .then((artifact) => {
        if (!mounted.current) return;
        if (artifact === null) {
          setTranslation({ kind: "unavailable" });
        } else if (artifact.meetingId === detail.meetingId) {
          setTranslation({ kind: "ready", artifact });
        } else {
          setTranslation({ kind: "failed" });
        }
      })
      .catch(() => {
        if (mounted.current) setTranslation({ kind: "failed" });
      });
    return () => {
      mounted.current = false;
    };
  }, [detail.meetingId]);

  useEffect(() => {
    transcriptRowRefs.current.clear();
    lastAutoScrolledTranscriptId.current = null;
    manualScrollIntentUntil.current = 0;
    setAutoFollowEnabled(true);
  }, [detail.meetingId]);

  useEffect(() => {
    if (playbackState === "stopped" || playbackState === "failed") {
      lastAutoScrolledTranscriptId.current = null;
      setAutoFollowEnabled(true);
    }
  }, [playbackState]);

  const translationsByTranscriptId = useMemo(() => {
    if (translation.kind !== "ready") return new Map<string, string>();
    return new Map(
      translation.artifact.segments.map((segment) => [
        segment.transcriptId,
        segment.translatedText,
      ]),
    );
  }, [translation]);

  const generate = async (): Promise<void> => {
    if (translation.kind === "loading" || translation.kind === "generating") return;
    const forceRegenerate = translation.kind === "ready";
    setTranslation({ kind: "generating" });
    try {
      const artifact = await generateMeetingTranslation(
        detail.meetingId,
        forceRegenerate,
      );
      if (!mounted.current) return;
      setTranslation(
        artifact.meetingId === detail.meetingId
          ? { kind: "ready", artifact }
          : { kind: "failed" },
      );
    } catch {
      if (mounted.current) setTranslation({ kind: "failed" });
    }
  };

  const pending = translation.kind === "loading" || translation.kind === "generating";
  const translationReady = translation.kind === "ready";
  const activeDisplay = translation.kind === "generating" ? "original" : display;
  const scrollToTranscript = (transcriptId: string): void => {
    const row = transcriptRowRefs.current.get(transcriptId);
    if (row === undefined) return;
    row.scrollIntoView({ block: "center", behavior: "smooth" });
    lastAutoScrolledTranscriptId.current = transcriptId;
  };
  const activeRowIsMeaningfullyAway = (): boolean => {
    if (activeTranscriptId === null) return false;
    const container = transcriptContainerRef.current;
    const row = transcriptRowRefs.current.get(activeTranscriptId);
    if (container === null || row === undefined) return false;
    const containerBounds = container.getBoundingClientRect();
    const rowBounds = row.getBoundingClientRect();
    const margin = 48;
    return rowBounds.bottom < containerBounds.top + margin || rowBounds.top > containerBounds.bottom - margin;
  };
  const markManualScrollIntent = (): void => {
    manualScrollIntentUntil.current = Date.now() + 750;
  };
  const handleTranscriptScroll = (): void => {
    if (!autoFollowEnabled || Date.now() > manualScrollIntentUntil.current) return;
    if (activeRowIsMeaningfullyAway()) setAutoFollowEnabled(false);
  };
  const followPlayback = (): void => {
    if (activeTranscriptId === null) return;
    setAutoFollowEnabled(true);
    scrollToTranscript(activeTranscriptId);
  };
  const handleTranscriptSeek = (transcriptId: string): void => {
    setAutoFollowEnabled(true);
    lastAutoScrolledTranscriptId.current = null;
    onSeekTranscript(transcriptId);
  };

  useEffect(() => {
    lastAutoScrolledTranscriptId.current = null;
  }, [activeDisplay]);

  useEffect(() => {
    if (
      synchronizationUnavailable ||
      !autoFollowEnabled ||
      activeTranscriptId === null ||
      activeTranscriptId === lastAutoScrolledTranscriptId.current
    ) return;
    scrollToTranscript(activeTranscriptId);
  }, [activeDisplay, activeTranscriptId, autoFollowEnabled, synchronizationUnavailable]);

  return (
    <section className="meeting-translation" aria-labelledby="translation-title">
      <header className="translation-header">
        <h4 id="translation-title">Turkish Translation</h4>
        <button type="button" disabled={pending} onClick={() => void generate()}>
          {translation.kind === "generating"
            ? "Generating…"
            : translationReady
              ? "Regenerate Translation"
              : "Generate Turkish Translation"}
        </button>
      </header>
      <p className="translation-status" aria-live="polite">
        {translation.kind === "loading" && "Checking for a Turkish translation…"}
        {translation.kind === "generating" && "Generating Turkish translation…"}
        {translation.kind === "unavailable" && "No Turkish translation yet."}
        {translation.kind === "failed" && "Translation is temporarily unavailable."}
        {translationReady && (
          <>
            Translation version {translation.artifact.version} · {translation.artifact.completedAt === null
              ? "Completed time unavailable"
              : `Completed ${displayTimestamp(translation.artifact.completedAt)}`}
          </>
        )}
      </p>
      {synchronizationUnavailable && (
        <p className="transcript-sync-status" aria-live="polite">
          {detail.audioHasGaps
            ? "Transcript synchronization is unavailable because this recording contains capture gaps."
            : "Transcript synchronization is unavailable."}
        </p>
      )}
      <fieldset className="transcript-display-toggle" disabled={translation.kind === "generating"}>
        <legend>Transcript display</legend>
        <label>
          <input
            type="radio"
            name={`transcript-display-${detail.meetingId}`}
            checked={activeDisplay === "original"}
            onChange={() => setDisplay("original")}
          />
          Original
        </label>
        <label>
          <input
            type="radio"
            name={`transcript-display-${detail.meetingId}`}
            checked={activeDisplay === "turkish"}
            onChange={() => setDisplay("turkish")}
          />
          Turkish
        </label>
        <label>
          <input
            type="radio"
            name={`transcript-display-${detail.meetingId}`}
            checked={activeDisplay === "side-by-side"}
            onChange={() => setDisplay("side-by-side")}
          />
          Side by side
        </label>
      </fieldset>
      {!synchronizationUnavailable && !autoFollowEnabled && activeTranscriptId !== null && (
        <button className="follow-playback" type="button" onClick={followPlayback}>
          Follow playback
        </button>
      )}
      <div
        className="meeting-transcript"
        ref={transcriptContainerRef}
        aria-label="Meeting transcript"
        onScroll={handleTranscriptScroll}
        onWheel={markManualScrollIntent}
        onTouchStart={markManualScrollIntent}
        onPointerDown={markManualScrollIntent}
        onKeyDown={(event) => {
          if (["ArrowDown", "ArrowUp", "PageDown", "PageUp", "Home", "End"].includes(event.key)) {
            markManualScrollIntent();
          }
        }}
      >
        {detail.transcript.length === 0 && (
          <p className="meeting-transcript-empty">No transcript entries.</p>
        )}
        {detail.transcript.map((entry) => {
          const translatedText = translationsByTranscriptId.get(entry.transcriptId);
          const missingTranslation = translationReady && translatedText === undefined;
          return (
            <article
              className={`meeting-transcript-row ${activeDisplay === "side-by-side" ? "side-by-side" : ""} ${entry.transcriptId === activeTranscriptId ? "transcript-row--active" : ""} ${canSeekTranscript ? "meeting-transcript-row--seekable" : ""}`}
              key={entry.transcriptId}
              ref={(node) => {
                if (node === null) transcriptRowRefs.current.delete(entry.transcriptId);
                else transcriptRowRefs.current.set(entry.transcriptId, node);
              }}
              aria-current={entry.transcriptId === activeTranscriptId ? "true" : undefined}
              role={canSeekTranscript ? "button" : undefined}
              tabIndex={canSeekTranscript ? 0 : undefined}
              onClick={canSeekTranscript ? () => handleTranscriptSeek(entry.transcriptId) : undefined}
              onKeyDown={canSeekTranscript ? (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  handleTranscriptSeek(entry.transcriptId);
                }
              } : undefined}
            >
              <header>
                <time dateTime={entry.timestamp}>{displayTimestamp(entry.timestamp)}</time>
                <span>{entry.speaker}</span>
              </header>
              {activeDisplay !== "turkish" && <p>{entry.text}</p>}
              {activeDisplay !== "original" && translationReady && (
                <p className="meeting-translated-text">
                  {missingTranslation ? "Turkish translation unavailable." : translatedText}
                </p>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
