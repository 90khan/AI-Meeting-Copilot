export interface ReviewActionItem { text: string; owner: string | null; dueDate: string | null; }
export interface ReviewOpenQuestion { question: string; }
export interface ReviewTechnicalTerm { term: string; explanation: string; }
export interface ReviewInterviewQuestion {
  question: string;
  answerSummary: string | null;
  evaluation: string | null;
  improvementSuggestion: string | null;
}
export interface ReviewFeedback { strengths: string[]; improvementAreas: string[]; overallFeedback: string; }
export interface MeetingReviewContent {
  summary: string;
  keyDecisions: string[];
  actionItems: ReviewActionItem[];
  openQuestions: ReviewOpenQuestion[];
  technicalQuestions: ReviewInterviewQuestion[];
  technicalTerms: ReviewTechnicalTerm[];
  feedback: ReviewFeedback | null;
}
export interface MeetingReviewArtifact {
  artifactId: string;
  meetingId: string;
  version: number;
  reviewType: "interview_review";
  status: "completed";
  createdAt: string;
  completedAt: string | null;
  sourceTranscriptCount: number;
  content: MeetingReviewContent;
}
export interface GeneratedMeetingReviewArtifact extends MeetingReviewArtifact { reusedExisting: boolean; }
