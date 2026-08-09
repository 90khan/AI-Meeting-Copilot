export interface TranslationArtifactSegment {
  transcriptId: string;
  sourceText: string;
  translatedText: string;
}

export interface MeetingTranslationArtifact {
  artifactId: string;
  meetingId: string;
  version: number;
  targetLanguage: "tr";
  status: "completed";
  createdAt: string;
  completedAt: string | null;
  sourceTranscriptCount: number;
  segments: TranslationArtifactSegment[];
}

export interface GeneratedMeetingTranslationArtifact extends MeetingTranslationArtifact {
  reusedExisting: boolean;
}
