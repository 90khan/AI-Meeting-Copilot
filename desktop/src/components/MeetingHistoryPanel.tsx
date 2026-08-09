import { useCallback, useEffect, useState } from "react";

import { getMeetingDetail, listMeetings } from "../history/client";
import type { MeetingDetail, MeetingHistoryItem } from "../history/types";

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
  return (
    <article className="meeting-detail" aria-labelledby="meeting-detail-title">
      <h3 id="meeting-detail-title">{detail.title}</h3>
      <p>Status: {detail.status}</p>
      <p>Started: {displayTimestamp(detail.startedAt)}</p>
      <p>Ended: {displayTimestamp(detail.endedAt)}</p>
      <div className="meeting-transcript" aria-label="Original Meeting transcript">
        {detail.transcript.length === 0 && <p className="meeting-transcript-empty">No transcript entries.</p>}
        {detail.transcript.map((entry) => (
          <article className="meeting-transcript-row" key={entry.transcriptId}>
            <header>
              <time dateTime={entry.timestamp}>{displayTimestamp(entry.timestamp)}</time>
              <span>{entry.speaker}</span>
            </header>
            <p>{entry.text}</p>
          </article>
        ))}
      </div>
    </article>
  );
}
