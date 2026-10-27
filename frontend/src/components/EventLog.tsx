import type { LogEntry } from "../appTypes";

type EventLogProps = {
  logs: LogEntry[];
};

export function EventLog({ logs }: EventLogProps) {
  return (
    <section className="event-log" aria-label="Recent session events">
      <div className="section-title">
        <p className="eyebrow">Recent</p>
        <h2>Events</h2>
      </div>
      <div className="event-list">
        {logs.length > 0 ? (
          logs.map((entry) => (
            <article className={`event-entry ${entry.level}`} key={entry.id}>
              {entry.message}
            </article>
          ))
        ) : (
          <p className="muted">Events will appear here.</p>
        )}
      </div>
    </section>
  );
}
