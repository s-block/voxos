import { FormEvent, useState } from "react";
import { TextField } from "./FormFields";
import { VoxosLogoTile } from "./VoxosMark";

type ServerSelectorProps = {
  activeUrl: string | null;
  error: string | null;
  isWorking: boolean;
  onCancel?: () => void;
  onSelect: (endpoint: string) => Promise<void>;
  servers: readonly string[];
};

export function ServerSelector({
  activeUrl,
  error,
  isWorking,
  onCancel,
  onSelect,
  servers,
}: ServerSelectorProps) {
  const [endpoint, setEndpoint] = useState("");
  const [isAdding, setIsAdding] = useState(servers.length === 0);
  const canAdd = !isWorking && endpoint.trim() !== "";

  return (
    <main className="auth-shell">
      <section aria-busy={isWorking} className="auth-panel server-panel">
        <div className="auth-brand">
          <VoxosLogoTile
            ariaLabel="VoxOS"
            className="auth-logo-lockup"
            surface="light"
          />
          <div className="section-title">
            <h1>Choose a server</h1>
            <p className="muted">Select a saved deployment or add another one.</p>
          </div>
        </div>

        <div className="server-list">
          {servers.map((server) => (
            <button
              aria-current={server === activeUrl ? "true" : undefined}
              className={
                server === activeUrl ? "server-option selected" : "server-option"
              }
              disabled={isWorking}
              key={server}
              onClick={() => void onSelect(server)}
              type="button"
            >
              <span>
                <strong>{serverHost(server)}</strong>
                <small>{server}</small>
              </span>
              <span aria-hidden="true">›</span>
            </button>
          ))}
        </div>

        {isAdding ? (
          <form
            className="settings-form"
            onSubmit={(event: FormEvent<HTMLFormElement>) => {
              event.preventDefault();
              if (canAdd) {
                void onSelect(endpoint);
              }
            }}
          >
            <TextField
              disabled={isWorking}
              label="Server address"
              onChange={setEndpoint}
              placeholder="https://voxos.example.com"
              type="text"
              value={endpoint}
            />
            <div className="server-actions">
              {servers.length > 0 ? (
                <button
                  disabled={isWorking}
                  onClick={() => setIsAdding(false)}
                  type="button"
                >
                  Cancel
                </button>
              ) : null}
              <button className="primary" disabled={!canAdd} type="submit">
                {isWorking ? "Connecting…" : "Connect"}
              </button>
            </div>
          </form>
        ) : (
          <button
            disabled={isWorking}
            onClick={() => setIsAdding(true)}
            type="button"
          >
            Add server
          </button>
        )}

        {error !== null ? <p className="error-text">{error}</p> : null}
        {onCancel !== undefined ? (
          <button disabled={isWorking} onClick={onCancel} type="button">
            Back
          </button>
        ) : null}
      </section>
    </main>
  );
}

function serverHost(server: string): string {
  try {
    return new URL(server).host;
  } catch {
    return server;
  }
}
