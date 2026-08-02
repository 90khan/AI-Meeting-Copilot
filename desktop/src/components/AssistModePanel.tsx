import { useEffect, useRef, useState } from "react";

import type { AssistState } from "../assist/types";
import { ReplySuggestions } from "./ReplySuggestions";
import { TranscriptSegmentRow } from "./TranscriptSegmentRow";

export function AssistModePanel({ state }: { state: AssistState }) {
  const transcriptRef = useRef<HTMLDivElement>(null);
  const [nearBottom, setNearBottom] = useState(true);
  useEffect(() => {
    if (nearBottom) transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight, behavior: "smooth" });
  }, [nearBottom, state.transcriptOrder.length]);
  return <section className="assist-panel" aria-labelledby="assist-title">
    <h2 id="assist-title">Assist Mode</h2>
    <p aria-live="polite">Assist status: <strong>{state.connectionStatus}</strong></p>
    <div className="transcript-list" ref={transcriptRef} onScroll={(event) => {
      const element = event.currentTarget;
      setNearBottom(element.scrollHeight - element.scrollTop - element.clientHeight < 48);
    }} aria-label="Finalized transcript">
      {state.transcriptOrder.map((id) => state.segments[id] && <TranscriptSegmentRow key={id} segment={state.segments[id]} />)}
    </div>
    <ReplySuggestions value={state.replySuggestions} />
  </section>;
}
