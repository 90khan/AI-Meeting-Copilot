import type { AssistTranscriptSegment } from "../assist/types";

export function TranscriptSegmentRow({ segment }: { segment: AssistTranscriptSegment }) {
  const time = new Date(segment.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return <article className="transcript-row">
    <header><time dateTime={segment.timestamp}>{time}</time><span>{segment.speaker}</span></header>
    <p className="transcript-original">{segment.text}</p>
    <Enrichment label="Turkish translation" value={segment.translation} />
    <Enrichment label="Simplified German" value={segment.simplification} />
  </article>;
}

function Enrichment({ label, value }: { label: string; value: import("../assist/types").AsyncValue<string | { text: string; targetLevel: "b1" | "b2" }> }) {
  if (value.state !== "ready") {
    if (value.state === "idle") return null;
    if (value.state === "processing") return <p className="assist-processing" aria-live="polite">{label} is processing…</p>;
    return <p className="assist-unavailable">{label} is unavailable.</p>;
  }
  const content = typeof value.value === "string" ? value.value : `${value.value.text} (${value.value.targetLevel.toUpperCase()})`;
  return <p className="assist-enrichment"><strong>{label}:</strong> {content}</p>;
}
