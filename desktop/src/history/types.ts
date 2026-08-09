/** Public Meeting History fields returned by the local backend. */
export interface MeetingHistoryItem {
  meetingId: string;
  title: string;
  status: string;
  createdAt: string;
  startedAt: string | null;
  endedAt: string | null;
  transcriptCount: number;
  recordingAvailable: boolean;
  recordingState: string | null;
  audioExpiresAt: string | null;
  audioProtected: boolean;
}

export interface MeetingHistoryResponse {
  meetings: MeetingHistoryItem[];
}

export interface TranscriptReadItem {
  transcriptId: string;
  text: string;
  timestamp: string;
  speaker: string;
  source: string;
}

export interface MeetingDetail extends MeetingHistoryItem {
  transcript: TranscriptReadItem[];
  recordingDurationSeconds: number | null;
  audioHasGaps: boolean;
}
