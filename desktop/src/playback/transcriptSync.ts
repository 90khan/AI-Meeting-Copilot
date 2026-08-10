import type { TranscriptReadItem } from "../history/types";

export type TranscriptTimelineEntry = Readonly<{
  transcriptId: string;
  relativeSeconds: number;
}>;

const UTC_TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z$/;

/**
 * Prepares a capture-timeline-relative transcript index. A malformed or
 * non-monotonic persisted timestamp makes exact synchronization unavailable;
 * this function never substitutes Meeting.startedAt or another approximation.
 */
export function buildTranscriptTimeline(
  transcript: readonly TranscriptReadItem[],
  captureAnchorUtc: string,
): readonly TranscriptTimelineEntry[] | null {
  const anchorMilliseconds = parseUtcMilliseconds(captureAnchorUtc);
  if (anchorMilliseconds === null) return null;
  const timeline: TranscriptTimelineEntry[] = [];
  let previous = Number.NEGATIVE_INFINITY;
  for (const entry of transcript) {
    const timestampMilliseconds = parseUtcMilliseconds(entry.timestamp);
    if (timestampMilliseconds === null) return null;
    const relativeSeconds = (timestampMilliseconds - anchorMilliseconds) / 1000;
    if (!Number.isFinite(relativeSeconds) || relativeSeconds < previous) return null;
    timeline.push({ transcriptId: entry.transcriptId, relativeSeconds });
    previous = relativeSeconds;
  }
  return timeline;
}

/** Returns the latest row at or before playback position in O(log n). */
export function findActiveTranscriptId(
  timeline: readonly TranscriptTimelineEntry[],
  currentTimeSeconds: number,
): string | null {
  if (!Number.isFinite(currentTimeSeconds) || timeline.length === 0) return null;
  let low = 0;
  let high = timeline.length - 1;
  let result = -1;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    if (timeline[middle].relativeSeconds <= currentTimeSeconds) {
      result = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  return result === -1 ? null : timeline[result].transcriptId;
}

function parseUtcMilliseconds(value: string): number | null {
  const match = UTC_TIMESTAMP.exec(value);
  if (match === null) return null;
  const [, year, month, day, hour, minute, second] = match;
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds)) return null;
  const timestamp = new Date(milliseconds);
  return timestamp.getUTCFullYear() === Number(year) &&
    timestamp.getUTCMonth() + 1 === Number(month) &&
    timestamp.getUTCDate() === Number(day) &&
    timestamp.getUTCHours() === Number(hour) &&
    timestamp.getUTCMinutes() === Number(minute) &&
    timestamp.getUTCSeconds() === Number(second)
    ? milliseconds
    : null;
}
