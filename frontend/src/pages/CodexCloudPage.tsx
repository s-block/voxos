import { FormEvent, useCallback, useEffect, useState } from "react";
import { NavLink } from "react-router";
import { ExternalLink, Loader2, MessageSquare, RefreshCw, Square, Unplug } from "lucide-react";
import {
  cancelCodexLogin,
  createCodexCloudTask,
  disconnectCodexAccount,
  loadAgents,
  loadCodexAccount,
  loadCodexAuthSession,
  loadCodexCloudTasks,
  loadChats,
  saveCodexEnvironment,
  startCodexLogin,
  type Agent,
  type CodexAccount,
  type CodexAuthSession,
  type CodexCloudTask,
  type ChatSummary,
} from "../api";
import type { LogEntry } from "../appTypes";
import { authErrorMessage } from "../appUtils";

type CodexCloudPageProps = {
  canEdit: boolean;
  onLog: (message: string, level?: LogEntry["level"]) => void;
};

const TERMINAL_LOGIN_STATES = new Set(["succeeded", "failed", "cancelled", "expired"]);

export function CodexCloudPage({ canEdit, onLog }: CodexCloudPageProps) {
  const [account, setAccount] = useState<CodexAccount | null>(null);
  const [authSession, setAuthSession] = useState<CodexAuthSession | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tasks, setTasks] = useState<CodexCloudTask[]>([]);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [agentId, setAgentId] = useState("");
  const [chatId, setChatId] = useState("");
  const [environmentId, setEnvironmentId] = useState("");
  const [environmentLabel, setEnvironmentLabel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [branch, setBranch] = useState("");
  const [attempts, setAttempts] = useState("1");
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [loadedAccount, loadedAgents, loadedTasks, loadedChats] = await Promise.all([
        loadCodexAccount(),
        loadAgents(),
        loadCodexCloudTasks(),
        loadChats(),
      ]);
      setAccount(loadedAccount);
      setAgents(loadedAgents.filter((agent) => agent.status === "active"));
      setTasks(loadedTasks);
      setChats(loadedChats);
      setAgentId((current) => current || loadedAgents[0]?.id || "");
      setEnvironmentId(loadedAccount.environment_id ?? "");
      setEnvironmentLabel(loadedAccount.environment_label ?? "");
    } catch (loadError) {
      setError(authErrorMessage(loadError));
    }
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timeout);
  }, [refresh]);

  useEffect(() => {
    if (authSession === null || TERMINAL_LOGIN_STATES.has(authSession.status)) {
      return;
    }
    const interval = window.setInterval(() => {
      void loadCodexAuthSession(authSession.id)
        .then((next) => {
          setAuthSession(next);
          if (next.status === "succeeded") {
            onLog("Codex ChatGPT account connected.");
            void refresh();
          }
        })
        .catch((pollError: unknown) => setError(authErrorMessage(pollError)));
    }, 1500);
    return () => window.clearInterval(interval);
  }, [authSession, onLog, refresh]);

  const connect = async () => {
    setIsBusy(true);
    setError(null);
    try {
      setAuthSession(await startCodexLogin());
      onLog("Codex login queued. Keep the Codex worker running.");
    } catch (connectError) {
      setError(authErrorMessage(connectError));
    } finally {
      setIsBusy(false);
    }
  };

  const saveEnvironment = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (environmentId.trim() === "") return;
    await runBusy(async () => {
      const saved = await saveCodexEnvironment({
        environment_id: environmentId.trim(),
        environment_label: environmentLabel.trim() || null,
      });
      setAccount(saved);
      onLog("Codex Cloud environment saved.");
    });
  };

  const submitTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (agentId === "" || prompt.trim() === "") return;
    await runBusy(async () => {
      const task = await createCodexCloudTask({
        agent_id: agentId,
        chat_id: chatId || null,
        prompt: prompt.trim(),
        branch: branch.trim() || null,
        attempts: Number(attempts),
        idempotency_key: crypto.randomUUID(),
      });
      setTasks((current) => [task, ...current]);
      setPrompt("");
      onLog("Codex Cloud task queued.");
    });
  };

  const runBusy = async (operation: () => Promise<void>) => {
    if (!canEdit || isBusy) return;
    setIsBusy(true);
    setError(null);
    try {
      await operation();
    } catch (operationError) {
      setError(authErrorMessage(operationError));
    } finally {
      setIsBusy(false);
    }
  };

  const connected = account?.connected === true;
  const loginInProgress = authSession !== null && !TERMINAL_LOGIN_STATES.has(authSession.status);

  return (
    <div className="settings-page codex-cloud-page">
      {error !== null ? <p className="error-text">{error}</p> : null}
      <section className="settings-panel">
        <div className="mcp-panel-heading">
          <div>
            <h3>ChatGPT Codex account</h3>
            <span>Uses your Codex subscription. API usage billing is not configured.</span>
          </div>
          <button disabled={isBusy} onClick={() => void refresh()} title="Refresh" type="button">
            <RefreshCw aria-hidden="true" size={16} />
          </button>
        </div>

        {connected ? (
          <div className="codex-account-summary">
            <p><strong>{account.email ?? "ChatGPT account"}</strong></p>
            <p className="muted">Plan: {account.plan_type ?? "not reported"}</p>
            <button
              disabled={!canEdit || isBusy}
              onClick={() => void runBusy(async () => {
                await disconnectCodexAccount();
                setAccount(await loadCodexAccount());
                onLog("Codex account disconnected.");
              })}
              type="button"
            >
              <Unplug aria-hidden="true" size={16} /> Disconnect
            </button>
          </div>
        ) : (
          <button disabled={!canEdit || isBusy || loginInProgress} onClick={() => void connect()} type="button">
            {isBusy ? <Loader2 aria-hidden="true" className="spin" size={16} /> : null}
            Connect ChatGPT Codex
          </button>
        )}

        {authSession !== null && authSession.status !== "succeeded" ? (
          <div className="codex-login-card" aria-live="polite">
            <p>Status: {authSession.status.replaceAll("_", " ")}</p>
            {authSession.verification_url !== null && authSession.user_code !== null ? (
              <>
                <p>Open the secure OpenAI page and enter:</p>
                <code className="codex-device-code">{authSession.user_code}</code>
                <a href={authSession.verification_url} rel="noreferrer" target="_blank">
                  Open OpenAI device login <ExternalLink aria-hidden="true" size={15} />
                </a>
              </>
            ) : <p className="muted">Waiting for the local Codex worker…</p>}
            {!TERMINAL_LOGIN_STATES.has(authSession.status) ? (
              <button
                disabled={!canEdit || isBusy}
                onClick={() => void runBusy(async () => {
                  setAuthSession(await cancelCodexLogin(authSession.id));
                })}
                type="button"
              >
                <Square aria-hidden="true" size={14} /> Cancel login
              </button>
            ) : null}
            {authSession.error_message !== null ? <p className="error-text">{authSession.error_message}</p> : null}
          </div>
        ) : null}
      </section>

      {connected ? (
        <section className="settings-panel">
          <h3>Cloud environment</h3>
          <p className="muted">Copy the environment ID from Codex Cloud. The CLI cannot list environments non-interactively yet.</p>
          <form className="codex-cloud-form" onSubmit={saveEnvironment}>
            <label>Environment ID<input disabled={!canEdit || isBusy} onChange={(event) => setEnvironmentId(event.target.value)} required value={environmentId} /></label>
            <label>Label (optional)<input disabled={!canEdit || isBusy} onChange={(event) => setEnvironmentLabel(event.target.value)} value={environmentLabel} /></label>
            <button disabled={!canEdit || isBusy || environmentId.trim() === ""} type="submit">Save environment</button>
          </form>
        </section>
      ) : null}

      {connected && account.environment_id !== null ? (
        <section className="settings-panel">
          <h3>Run in Codex Cloud</h3>
          <form className="codex-cloud-form" onSubmit={submitTask}>
            <label>Agent<select disabled={!canEdit || isBusy} onChange={(event) => { setAgentId(event.target.value); setChatId(""); }} value={agentId}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.display_name}</option>)}</select></label>
            <label>Chat<select aria-label="Chat destination" disabled={!canEdit || isBusy} onChange={(event) => setChatId(event.target.value)} value={chatId}><option value="">Create a new task chat</option>{chats.filter((chat) => chat.agent_id === agentId).map((chat) => <option key={chat.id} value={chat.id}>{chat.title ?? "New chat"}</option>)}</select></label>
            <label>Task<textarea disabled={!canEdit || isBusy} onChange={(event) => setPrompt(event.target.value)} required rows={6} value={prompt} /></label>
            <label>Branch (optional)<input disabled={!canEdit || isBusy} onChange={(event) => setBranch(event.target.value)} value={branch} /></label>
            <label>Attempts<select disabled={!canEdit || isBusy} onChange={(event) => setAttempts(event.target.value)} value={attempts}>{[1, 2, 3, 4].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
            <button disabled={!canEdit || isBusy || agentId === "" || prompt.trim() === ""} type="submit">Queue Codex Cloud task</button>
          </form>
        </section>
      ) : null}

      <section className="settings-panel">
        <h3>Recent Codex Cloud tasks</h3>
        {tasks.length === 0 ? <p className="muted">No Codex Cloud tasks yet.</p> : (
          <div className="codex-task-list">{tasks.map((task) => (
            <article key={task.id}>
              <strong>{task.summary ?? task.run.kind.replaceAll("_", " ")}</strong>
              <span>{task.run.status} · {task.remote_status}</span>
              {task.run.chat_id !== null ? <NavLink to={`/chat?chat=${encodeURIComponent(task.run.chat_id)}`}>Open task chat <MessageSquare aria-hidden="true" size={14} /></NavLink> : null}
              {task.remote_task_url !== null ? <a href={task.remote_task_url} rel="noreferrer" target="_blank">Open in Codex Cloud <ExternalLink aria-hidden="true" size={14} /></a> : null}
            </article>
          ))}</div>
        )}
      </section>
    </div>
  );
}
