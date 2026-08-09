import { invoke } from "@tauri-apps/api/core";

import type { MeetingDetail, MeetingHistoryResponse } from "./types";

export function listMeetings(
  limit: number,
  offset: number,
): Promise<MeetingHistoryResponse> {
  return invoke<MeetingHistoryResponse>("list_meetings", { limit, offset });
}

export function getMeetingDetail(meetingId: string): Promise<MeetingDetail> {
  return invoke<MeetingDetail>("get_meeting_detail", { meetingId });
}
