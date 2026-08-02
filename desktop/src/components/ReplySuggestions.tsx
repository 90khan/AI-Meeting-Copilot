import type { AsyncValue, ReplySuggestion } from "../assist/types";

export function ReplySuggestions({ value }: { value: AsyncValue<ReplySuggestion[]> }) {
  return <section className="reply-suggestions" aria-labelledby="reply-suggestions-title">
    <h3 id="reply-suggestions-title">Reply suggestions</h3>
    {value.state === "processing" && <p aria-live="polite">Preparing suggestions…</p>}
    {(value.state === "failed" || value.state === "unavailable") && <p>Reply suggestions are unavailable.</p>}
    {value.state === "ready" && <ol>{value.value.map((suggestion, index) => <li key={`${suggestion.text}-${index}`}><span>{suggestion.text}</span><small>{suggestion.tone}</small></li>)}</ol>}
  </section>;
}
