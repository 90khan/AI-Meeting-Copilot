export default function App() {
  return (
    <main className="app-shell">
      <section className="app-panel" aria-labelledby="application-title">
        <h1 id="application-title">AI Meeting Copilot</h1>
        <p>
          Backend status: <strong>Not started</strong>
        </p>
        <div className="actions">
          <button type="button" disabled>
            Start Meeting
          </button>
          <button type="button" disabled>
            Stop Meeting
          </button>
        </div>
      </section>
    </main>
  );
}
