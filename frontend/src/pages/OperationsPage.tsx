import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { NavLink } from "react-router";
import {
  CalendarClock,
  CirclePause,
  History,
  Loader2,
  MessageSquare,
  Play,
  Radio,
  RefreshCw,
  RotateCcw,
  Square,
  Trash2,
  Webhook,
} from "lucide-react";
import {
  cancelRun,
  createAgentWebhook,
  createSchedule,
  deleteAgentWebhook,
  deleteSchedule,
  loadActiveRuns,
  loadAgents,
  loadAgentWebhooks,
  loadBrowserAccounts,
  loadChats,
  loadRun,
  loadRunEvents,
  loadRuns,
  loadSchedules,
  pauseSchedule,
  resumeSchedule,
  rotateAgentWebhookToken,
  retryRun,
  runScheduleNow,
  streamRunEvents,
  updateAgentWebhook,
  type Agent,
  type AgentWebhook,
  type AgentWebhookCredential,
  type AgentRunDetail,
  type AgentRunEvent,
  type AgentRunStatus,
  type AgentRunSummary,
  type AgentSchedule,
  type BrowserAccount,
  type ChatSummary,
  type ScheduleKind,
} from "../api";
import type { LogEntry } from "../appTypes";
import { authErrorMessage } from "../appUtils";
import { operationsTabPaths, type OperationsTab } from "../routing";

type OperationsPageProps = {
  activeTab: OperationsTab;
  canEdit: boolean;
  onLog: (message: string, level?: LogEntry["level"]) => void;
};

type ScheduleFormState = {
  agentId: string;
  chatId: string;
  browserAccountId: string;
  name: string;
  kind: ScheduleKind;
  runDate: string;
  runHour: string;
  runMinute: string;
  intervalSeconds: string;
  prompt: string;
};

type WebhookFormState = {
  agentId: string;
  name: string;
  instruction: string;
};

const scheduleHours = Array.from({ length: 24 }, (_, hour) => twoDigits(hour));
const scheduleMinutes = Array.from({ length: 60 }, (_, minute) => twoDigits(minute));

const terminalStatuses: readonly AgentRunStatus[] = [
  "succeeded",
  "failed",
  "cancelled",
  "lost",
];

export function OperationsPage({
  activeTab,
  canEdit,
  onLog,
}: OperationsPageProps) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [activeRuns, setActiveRuns] = useState<AgentRunSummary[]>([]);
  const [historyRuns, setHistoryRuns] = useState<AgentRunSummary[]>([]);
  const [schedules, setSchedules] = useState<AgentSchedule[]>([]);
  const [webhooks, setWebhooks] = useState<AgentWebhook[]>([]);
  const [issuedWebhook, setIssuedWebhook] = useState<AgentWebhookCredential | null>(null);
  const [browserAccounts, setBrowserAccounts] = useState<BrowserAccount[]>([]);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [events, setEvents] = useState<AgentRunEvent[]>([]);
  const [selectedRun, setSelectedRun] = useState<AgentRunDetail | null>(null);
  const [selectedRunEvents, setSelectedRunEvents] = useState<AgentRunEvent[]>([]);
  const [focusedRunId, setFocusedRunId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isStreamingConnected, setIsStreamingConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<ScheduleFormState>({
    ...defaultRunAtParts(),
    agentId: "",
    chatId: "",
    browserAccountId: "",
    name: "",
    kind: "one_shot",
    intervalSeconds: "3600",
    prompt: "",
  });
  const [webhookForm, setWebhookForm] = useState<WebhookFormState>({
    agentId: "",
    name: "",
    instruction: "",
  });

  const agentNames = useMemo(
    () => new Map(agents.map((agent) => [agent.id, agent.display_name])),
    [agents],
  );
  const visibleEvents = useMemo(
    () =>
      focusedRunId === null
        ? events
        : events.filter((event) => event.run_id === focusedRunId),
    [events, focusedRunId],
  );
  const failedRuns = historyRuns.filter((run) =>
    ["failed", "lost"].includes(run.status),
  );

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [
        loadedAgents,
        loadedActiveRuns,
        loadedHistoryRuns,
        loadedSchedules,
        loadedWebhooks,
        loadedBrowserAccounts,
        loadedChats,
      ] =
        await Promise.all([
          loadAgents(),
          loadActiveRuns(),
          loadRuns({ statuses: terminalStatuses, limit: 100 }),
          loadSchedules(),
          loadAgentWebhooks(),
          loadBrowserAccounts(),
          loadChats(),
        ]);
      setAgents(loadedAgents);
      setActiveRuns(loadedActiveRuns);
      setHistoryRuns(loadedHistoryRuns);
      setSchedules(loadedSchedules);
      setWebhooks(loadedWebhooks);
      setBrowserAccounts(loadedBrowserAccounts);
      setChats(loadedChats);
      setForm((current) => ({
        ...current,
        agentId: current.agentId || loadedAgents[0]?.id || "",
      }));
      setWebhookForm((current) => ({
        ...current,
        agentId: current.agentId || loadedAgents[0]?.id || "",
      }));
    } catch (loadError) {
      setError(authErrorMessage(loadError));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      void refresh();
    }, 0);
    return () => {
      window.clearTimeout(timeout);
    };
  }, [refresh]);

  useEffect(() => {
    if (activeTab === "webhooks") {
      return undefined;
    }
    const timeout = window.setTimeout(() => setIssuedWebhook(null), 0);
    return () => window.clearTimeout(timeout);
  }, [activeTab]);

  useEffect(() => {
    const controller = new AbortController();
    void streamRunEvents({
      signal: controller.signal,
      onEvent: (event) => {
        setIsStreamingConnected(true);
        setEvents((current) => prependEvent(current, event));
        if (selectedRun?.id === event.run_id) {
          setSelectedRunEvents((current) => prependEvent(current, event));
        }
      },
    }).catch((streamError) => {
      if (streamError instanceof DOMException && streamError.name === "AbortError") {
        return;
      }
      setIsStreamingConnected(false);
      setError(authErrorMessage(streamError));
    });
    return () => {
      controller.abort();
    };
  }, [selectedRun?.id]);

  const openRun = async (runId: string) => {
    setError(null);
    try {
      const [run, runEvents] = await Promise.all([
        loadRun(runId),
        loadRunEvents(runId),
      ]);
      setSelectedRun(run);
      setSelectedRunEvents(runEvents.slice().reverse());
      setFocusedRunId(runId);
    } catch (runError) {
      setError(authErrorMessage(runError));
    }
  };

  const cancelSelectedRun = async (runId: string) => {
    setIsSaving(true);
    try {
      const run = await cancelRun(runId, "Cancelled from Operations");
      setSelectedRun(run);
      onLog(
        run.kind === "codex_cloud_task"
          ? "Stopped tracking the Codex Cloud task. The remote task may continue."
          : "Run cancellation requested.",
      );
      await refresh();
    } catch (cancelError) {
      setError(authErrorMessage(cancelError));
    } finally {
      setIsSaving(false);
    }
  };

  const retrySelectedRun = async (runId: string) => {
    setIsSaving(true);
    try {
      const run = await retryRun(runId);
      setSelectedRun(run);
      onLog("Run queued for retry.");
      await refresh();
    } catch (retryError) {
      setError(authErrorMessage(retryError));
    } finally {
      setIsSaving(false);
    }
  };

  const submitSchedule = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canEdit || form.agentId === "" || form.name.trim() === "" || form.prompt.trim() === "") {
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      await createSchedule({
        agent_id: form.agentId,
        ...(form.chatId === "" ? {} : { chat_id: form.chatId }),
        name: form.name.trim(),
        kind: form.kind,
        payload_json: { text: form.prompt.trim() },
        ...(form.browserAccountId === ""
          ? {}
          : { browser_account_id: form.browserAccountId }),
        run_at:
          form.kind === "one_shot"
            ? localDatetimeToIso(form.runDate, form.runHour, form.runMinute)
            : null,
        interval_seconds:
          form.kind === "interval" ? Number(form.intervalSeconds) : null,
      });
      setForm((current) => ({
        ...current,
        ...defaultRunAtParts(),
        name: "",
        prompt: "",
        browserAccountId: "",
        chatId: "",
      }));
      onLog("Schedule created.");
      await refresh();
    } catch (scheduleError) {
      setError(authErrorMessage(scheduleError));
    } finally {
      setIsSaving(false);
    }
  };

  const runScheduleAction = async (
    schedule: AgentSchedule,
    action: "pause" | "resume" | "run-now" | "delete",
  ) => {
    setIsSaving(true);
    setError(null);
    try {
      if (action === "pause") {
        await pauseSchedule(schedule.id);
      } else if (action === "resume") {
        await resumeSchedule(schedule.id);
      } else if (action === "run-now") {
        await runScheduleNow(schedule.id);
      } else {
        await deleteSchedule(schedule.id);
      }
      await refresh();
    } catch (scheduleError) {
      setError(authErrorMessage(scheduleError));
    } finally {
      setIsSaving(false);
    }
  };

  const submitWebhook = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      !canEdit ||
      webhookForm.agentId === "" ||
      webhookForm.name.trim() === "" ||
      webhookForm.instruction.trim() === ""
    ) {
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      const issued = await createAgentWebhook({
        agent_id: webhookForm.agentId,
        name: webhookForm.name.trim(),
        instruction: webhookForm.instruction.trim(),
      });
      setIssuedWebhook(issued);
      setWebhookForm((current) => ({ ...current, name: "", instruction: "" }));
      onLog("Webhook created. Copy its token before leaving this page.");
      await refresh();
    } catch (webhookError) {
      setError(authErrorMessage(webhookError));
    } finally {
      setIsSaving(false);
    }
  };

  const runWebhookAction = async (
    webhook: AgentWebhook,
    action: "toggle" | "rotate" | "delete",
  ) => {
    setIsSaving(true);
    setError(null);
    try {
      if (action === "toggle") {
        await updateAgentWebhook(webhook.id, { enabled: !webhook.enabled });
      } else if (action === "rotate") {
        const issued = await rotateAgentWebhookToken(webhook.id);
        setIssuedWebhook(issued);
        onLog("Webhook token rotated. Copy the replacement token now.");
      } else {
        await deleteAgentWebhook(webhook.id);
        if (issuedWebhook?.id === webhook.id) {
          setIssuedWebhook(null);
        }
      }
      await refresh();
    } catch (webhookError) {
      setError(authErrorMessage(webhookError));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="operations-page">
      <section className="operations-summary">
        <SummaryMetric label="Running" value={activeRuns.length} />
        <SummaryMetric
          label="Queued"
          value={activeRuns.filter((run) => run.status === "queued").length}
        />
        <SummaryMetric
          label="Scheduled"
          value={schedules.filter((schedule) => schedule.status === "active").length}
        />
        <SummaryMetric label="Attention" value={failedRuns.length} tone="danger" />
      </section>

      {error !== null ? <p className="error-text">{error}</p> : null}

      <section className="operations-toolbar">
        <div className="segmented-control">
          {(["active", "scheduled", "webhooks", "history", "events"] as const).map((item) => (
            <NavLink
              className={({ isActive }) => (isActive ? "selected" : undefined)}
              end
              key={item}
              to={operationsTabPaths[item]}
            >
              {tabIcon(item)}
              <span>{tabLabel(item)}</span>
            </NavLink>
          ))}
        </div>
        <button
          disabled={isLoading}
          onClick={() => void refresh()}
          title="Refresh"
          type="button"
        >
          <RefreshCw aria-hidden="true" size={16} />
        </button>
      </section>

      <div className={`operations-layout ${activeTab === "webhooks" ? "single" : ""}`}>
        <section className="operations-main">
          {activeTab === "active" ? (
            <RunsTable
              agentNames={agentNames}
              canCancel={canEdit}
              emptyText="No active runs."
              onCancel={(run) => void cancelSelectedRun(run.id)}
              onOpen={(run) => void openRun(run.id)}
              runs={activeRuns}
            />
          ) : null}
          {activeTab === "scheduled" ? (
            <ScheduledTab
              agents={agents}
              browserAccounts={browserAccounts}
              chats={chats}
              canEdit={canEdit}
              form={form}
              isSaving={isSaving}
              onAction={(schedule, action) => void runScheduleAction(schedule, action)}
              onFormChange={setForm}
              onSubmit={submitSchedule}
              schedules={schedules}
            />
          ) : null}
          {activeTab === "webhooks" ? (
            <WebhooksTab
              agents={agents}
              canEdit={canEdit}
              form={webhookForm}
              isSaving={isSaving}
              issuedWebhook={issuedWebhook}
              onAction={(webhook, action) => void runWebhookAction(webhook, action)}
              onDismissCredential={() => setIssuedWebhook(null)}
              onFormChange={setWebhookForm}
              onSubmit={submitWebhook}
              webhooks={webhooks}
            />
          ) : null}
          {activeTab === "history" ? (
            <RunsTable
              agentNames={agentNames}
              canRetry={canEdit}
              emptyText="No completed runs."
              onOpen={(run) => void openRun(run.id)}
              onRetry={(run) => void retrySelectedRun(run.id)}
              runs={historyRuns}
            />
          ) : null}
          {activeTab === "events" ? (
            <EventsFeed
              events={visibleEvents}
              focusedRunId={focusedRunId}
              isStreamingConnected={isStreamingConnected}
              onClearFocus={() => setFocusedRunId(null)}
              onFocusRun={setFocusedRunId}
            />
          ) : null}
        </section>

        {activeTab === "webhooks" ? null : (
          <RunDetailPanel
            canEdit={canEdit}
            events={selectedRunEvents}
            onCancel={(run) => void cancelSelectedRun(run.id)}
            onClose={() => {
              setSelectedRun(null);
              setSelectedRunEvents([]);
            }}
            onRetry={(run) => void retrySelectedRun(run.id)}
            run={selectedRun}
          />
        )}
      </div>
    </div>
  );
}

function SummaryMetric({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: number;
  tone?: "default" | "danger";
}) {
  return (
    <article className={`summary-metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function RunsTable({
  agentNames,
  canCancel = false,
  canRetry = false,
  emptyText,
  onCancel,
  onOpen,
  onRetry,
  runs,
}: {
  agentNames: Map<string, string>;
  canCancel?: boolean;
  canRetry?: boolean;
  emptyText: string;
  onCancel?: (run: AgentRunSummary) => void;
  onOpen: (run: AgentRunSummary) => void;
  onRetry?: (run: AgentRunSummary) => void;
  runs: AgentRunSummary[];
}) {
  if (runs.length === 0) {
    return <p className="muted">{emptyText}</p>;
  }
  return (
    <div className="operations-table-wrap">
      <table className="operations-table">
        <thead>
          <tr>
            <th>Run</th>
            <th>Agent</th>
            <th>Status</th>
            <th>Queue</th>
            <th>Attempt</th>
            <th>Heartbeat</th>
            <th>Children</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td>
                <button className="text-button" onClick={() => onOpen(run)} type="button">
                  {run.kind.replaceAll("_", " ")}
                </button>
                <small>{shortId(run.id)}</small>
              </td>
              <td>{agentNames.get(run.agent_id) ?? shortId(run.agent_id)}</td>
              <td>
                <StatusChip status={run.status} />
              </td>
              <td>{run.queue}</td>
              <td>
                {run.attempt}/{run.max_attempts}
              </td>
              <td>{relativeTime(run.heartbeat_at ?? run.started_at ?? run.created_at)}</td>
              <td>{run.child_run_count}</td>
              <td className="table-actions">
                {canCancel && ["queued", "running", "cancelling"].includes(run.status) ? (
                  <button
                    onClick={() => onCancel?.(run)}
                    title={run.kind === "codex_cloud_task" ? "Stop tracking" : "Cancel run"}
                    type="button"
                  >
                    <Square aria-hidden="true" size={15} />
                  </button>
                ) : null}
                {canRetry &&
                run.kind !== "codex_cloud_task" &&
                ["failed", "lost"].includes(run.status) ? (
                  <button onClick={() => onRetry?.(run)} title="Retry run" type="button">
                    <RotateCcw aria-hidden="true" size={15} />
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ScheduledTab({
  agents,
  browserAccounts,
  chats,
  canEdit,
  form,
  isSaving,
  onAction,
  onFormChange,
  onSubmit,
  schedules,
}: {
  agents: Agent[];
  browserAccounts: BrowserAccount[];
  chats: ChatSummary[];
  canEdit: boolean;
  form: ScheduleFormState;
  isSaving: boolean;
  onAction: (
    schedule: AgentSchedule,
    action: "pause" | "resume" | "run-now" | "delete",
  ) => void;
  onFormChange: (form: ScheduleFormState) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  schedules: AgentSchedule[];
}) {
  const availableBrowserAccounts = browserAccounts.filter(
    (account) =>
      account.provider === "browserless" &&
      account.status === "ready" &&
      account.has_credentials &&
      account.agent_ids.includes(form.agentId),
  );
  const availableChats = chats.filter((chat) => chat.agent_id === form.agentId);
  return (
    <div className="scheduled-tab">
      <form className="schedule-form" onSubmit={onSubmit}>
        <select
          disabled={!canEdit || isSaving}
          onChange={(event) =>
            onFormChange({
              ...form,
              agentId: event.currentTarget.value,
              browserAccountId: "",
              chatId: "",
            })
          }
          value={form.agentId}
        >
          {agents.map((agent) => (
            <option key={agent.id} value={agent.id}>
              {agent.display_name}
            </option>
          ))}
        </select>
        <select
          aria-label="Chat destination"
          disabled={!canEdit || isSaving}
          onChange={(event) => onFormChange({ ...form, chatId: event.currentTarget.value })}
          value={form.chatId}
        >
          <option value="">Create a new task chat</option>
          {availableChats.map((chat) => (
            <option key={chat.id} value={chat.id}>
              {chat.title ?? "New chat"}
            </option>
          ))}
        </select>
        <select
          aria-label="Browser Account (optional)"
          disabled={!canEdit || isSaving}
          onChange={(event) =>
            onFormChange({ ...form, browserAccountId: event.currentTarget.value })
          }
          value={form.browserAccountId}
        >
          <option value="">No signed-in browser</option>
          {availableBrowserAccounts.map((account) => (
            <option key={account.id} value={account.id}>
              {account.label}
            </option>
          ))}
        </select>
        <input
          disabled={!canEdit || isSaving}
          onChange={(event) => onFormChange({ ...form, name: event.currentTarget.value })}
          placeholder="Schedule name"
          value={form.name}
        />
        <select
          disabled={!canEdit || isSaving}
          onChange={(event) =>
            onFormChange({ ...form, kind: event.currentTarget.value as ScheduleKind })
          }
          value={form.kind}
        >
          <option value="one_shot">One shot</option>
          <option value="interval">Interval</option>
        </select>
        {form.kind === "one_shot" ? (
          <div
            aria-label="Run date and time"
            className="schedule-datetime-fields"
            role="group"
          >
            <input
              aria-label="Run date"
              disabled={!canEdit || isSaving}
              onChange={(event) =>
                onFormChange({ ...form, runDate: event.currentTarget.value })
              }
              required
              type="date"
              value={form.runDate}
            />
            <div className="schedule-time-fields">
              <select
                aria-label="Run hour (24-hour)"
                disabled={!canEdit || isSaving}
                onChange={(event) =>
                  onFormChange({ ...form, runHour: event.currentTarget.value })
                }
                value={form.runHour}
              >
                {scheduleHours.map((hour) => (
                  <option key={hour} value={hour}>
                    {hour}
                  </option>
                ))}
              </select>
              <span aria-hidden="true">:</span>
              <select
                aria-label="Run minute"
                disabled={!canEdit || isSaving}
                onChange={(event) =>
                  onFormChange({ ...form, runMinute: event.currentTarget.value })
                }
                value={form.runMinute}
              >
                {scheduleMinutes.map((minute) => (
                  <option key={minute} value={minute}>
                    {minute}
                  </option>
                ))}
              </select>
              <small title="24-hour time">24h</small>
            </div>
          </div>
        ) : (
          <input
            disabled={!canEdit || isSaving}
            min={60}
            onChange={(event) =>
              onFormChange({ ...form, intervalSeconds: event.currentTarget.value })
            }
            placeholder="Seconds"
            type="number"
            value={form.intervalSeconds}
          />
        )}
        <textarea
          disabled={!canEdit || isSaving}
          onChange={(event) => onFormChange({ ...form, prompt: event.currentTarget.value })}
          placeholder="Scheduled instruction"
          rows={3}
          value={form.prompt}
        />
        <button
          className="primary"
          disabled={!canEdit || isSaving || form.agentId === "" || form.prompt.trim() === ""}
          type="submit"
        >
          <CalendarClock aria-hidden="true" size={16} />
          Save
        </button>
      </form>

      {schedules.length === 0 ? <p className="muted">No schedules.</p> : null}
      <div className="schedule-list">
        {schedules.map((schedule) => (
          <article className="schedule-row" key={schedule.id}>
            <span>
              <strong>{schedule.name}</strong>
              <small>
                {schedule.kind.replaceAll("_", " ")} · next {relativeTime(schedule.next_run_at)}
              </small>
            </span>
            <StatusChip status={schedule.status} />
            <div className="table-actions">
              {schedule.chat_id !== null ? (
                <NavLink to={`/chat?chat=${encodeURIComponent(schedule.chat_id)}`} title="Open chat">
                  <MessageSquare aria-hidden="true" size={15} />
                </NavLink>
              ) : null}
              <button
                disabled={!canEdit || isSaving}
                onClick={() => onAction(schedule, "run-now")}
                title="Run now"
                type="button"
              >
                <Play aria-hidden="true" size={15} />
              </button>
              {schedule.status === "paused" ? (
                <button
                  disabled={!canEdit || isSaving}
                  onClick={() => onAction(schedule, "resume")}
                  title="Resume"
                  type="button"
                >
                  <Play aria-hidden="true" size={15} />
                </button>
              ) : (
                <button
                  disabled={!canEdit || isSaving}
                  onClick={() => onAction(schedule, "pause")}
                  title="Pause"
                  type="button"
                >
                  <CirclePause aria-hidden="true" size={15} />
                </button>
              )}
              <button
                className="danger"
                disabled={!canEdit || isSaving}
                onClick={() => onAction(schedule, "delete")}
                title="Delete"
                type="button"
              >
                <Trash2 aria-hidden="true" size={15} />
              </button>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function WebhooksTab({
  agents,
  canEdit,
  form,
  isSaving,
  issuedWebhook,
  onAction,
  onDismissCredential,
  onFormChange,
  onSubmit,
  webhooks,
}: {
  agents: Agent[];
  canEdit: boolean;
  form: WebhookFormState;
  isSaving: boolean;
  issuedWebhook: AgentWebhookCredential | null;
  onAction: (webhook: AgentWebhook, action: "toggle" | "rotate" | "delete") => void;
  onDismissCredential: () => void;
  onFormChange: (form: WebhookFormState) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  webhooks: AgentWebhook[];
}) {
  return (
    <div className="webhooks-tab">
      <div className="section-title">
        <p className="eyebrow">Inbound automation</p>
        <h2>Agent webhooks</h2>
        <p className="muted">
          Accept a JSON event and start a new isolated task with the saved agent and
          instruction. The selected agent's current tool grants apply to every delivery.
        </p>
      </div>

      <form className="webhook-form" onSubmit={onSubmit}>
        <label>
          Agent
          <select
            disabled={!canEdit || isSaving}
            onChange={(event) =>
              onFormChange({ ...form, agentId: event.currentTarget.value })
            }
            value={form.agentId}
          >
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.display_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Name
          <input
            disabled={!canEdit || isSaving}
            onChange={(event) => onFormChange({ ...form, name: event.currentTarget.value })}
            placeholder="CRM lead received"
            value={form.name}
          />
        </label>
        <label className="webhook-instruction-field">
          Instruction
          <textarea
            disabled={!canEdit || isSaving}
            onChange={(event) =>
              onFormChange({ ...form, instruction: event.currentTarget.value })
            }
            placeholder="Qualify the lead using the JSON event and record the next action."
            rows={4}
            value={form.instruction}
          />
        </label>
        <button
          className="primary"
          disabled={
            !canEdit ||
            isSaving ||
            form.agentId === "" ||
            form.name.trim() === "" ||
            form.instruction.trim() === ""
          }
          type="submit"
        >
          <Webhook aria-hidden="true" size={16} />
          Create webhook
        </button>
      </form>

      {issuedWebhook !== null ? (
        <section className="webhook-credential-panel" aria-label="New webhook credential">
          <div className="section-heading-row">
            <div>
              <strong>Copy this credential now</strong>
              <p>The token is shown only after creation or rotation.</p>
            </div>
            <button className="text-button" onClick={onDismissCredential} type="button">
              Dismiss
            </button>
          </div>
          {issuedWebhook.trigger_url === null ? (
            <p className="warning-banner">
              No public API URL is configured. Replace the relative path with the public Voxos
              API origin.
            </p>
          ) : null}
          <pre>
            <code>{webhookCurl(issuedWebhook)}</code>
          </pre>
        </section>
      ) : null}

      {webhooks.length === 0 ? <p className="muted">No agent webhooks.</p> : null}
      <div className="webhook-list">
        {webhooks.map((webhook) => (
          <article className="webhook-row" key={webhook.id}>
            <div>
              <strong>{webhook.name}</strong>
              <small>{webhook.instruction}</small>
              <code>{webhook.trigger_url ?? webhook.trigger_path}</code>
            </div>
            <StatusChip status={webhook.enabled ? "active" : "paused"} />
            <div className="webhook-actions">
              <button
                disabled={!canEdit || isSaving}
                onClick={() => onAction(webhook, "toggle")}
                type="button"
              >
                {webhook.enabled ? "Disable" : "Enable"}
              </button>
              <button
                disabled={!canEdit || isSaving}
                onClick={() => onAction(webhook, "rotate")}
                type="button"
              >
                Rotate token
              </button>
              <button
                className="danger"
                disabled={!canEdit || isSaving}
                onClick={() => onAction(webhook, "delete")}
                type="button"
              >
                <Trash2 aria-hidden="true" size={15} />
                Delete
              </button>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function EventsFeed({
  events,
  focusedRunId,
  isStreamingConnected,
  onClearFocus,
  onFocusRun,
}: {
  events: AgentRunEvent[];
  focusedRunId: string | null;
  isStreamingConnected: boolean;
  onClearFocus: () => void;
  onFocusRun: (runId: string) => void;
}) {
  return (
    <div className="events-feed">
      <div className="events-feed-header">
        <span className={`stream-state ${isStreamingConnected ? "on" : ""}`}>
          <Radio aria-hidden="true" size={15} />
          Live
        </span>
        {focusedRunId !== null ? (
          <button className="text-button" onClick={onClearFocus} type="button">
            Clear focus
          </button>
        ) : null}
      </div>
      {events.length === 0 ? <p className="muted">No live events yet.</p> : null}
      {events.map((event) => (
        <article className="event-row" key={event.id}>
          <span>
            <strong>{event.kind.replaceAll("_", " ")}</strong>
            <small>{formatDateTime(event.created_at)}</small>
          </span>
          <button
            className="text-button"
            onClick={() => onFocusRun(event.run_id)}
            type="button"
          >
            {shortId(event.run_id)}
          </button>
          <code>{eventPayloadSummary(event.payload_json)}</code>
        </article>
      ))}
    </div>
  );
}

function RunDetailPanel({
  canEdit,
  events,
  onCancel,
  onClose,
  onRetry,
  run,
}: {
  canEdit: boolean;
  events: AgentRunEvent[];
  onCancel: (run: AgentRunDetail) => void;
  onClose: () => void;
  onRetry: (run: AgentRunDetail) => void;
  run: AgentRunDetail | null;
}) {
  if (run === null) {
    return (
      <aside className="run-detail-panel empty">
        <p className="muted">Select a run to inspect timeline and metadata.</p>
      </aside>
    );
  }
  return (
    <aside className="run-detail-panel">
      <div className="section-heading-row">
        <div className="section-title">
          <p className="eyebrow">Run</p>
          <h2>{run.kind.replaceAll("_", " ")}</h2>
        </div>
        <button className="text-button" onClick={onClose} type="button">
          Close
        </button>
      </div>
      <dl className="run-metadata">
        <dt>Status</dt>
        <dd>
          <StatusChip status={run.status} />
        </dd>
        <dt>ID</dt>
        <dd>{run.id}</dd>
        <dt>Agent</dt>
        <dd>{run.agent_id}</dd>
        <dt>Queue</dt>
        <dd>{run.queue}</dd>
        <dt>Started</dt>
        <dd>{formatDateTime(run.started_at)}</dd>
        <dt>Completed</dt>
        <dd>{formatDateTime(run.completed_at)}</dd>
        <dt>Parent</dt>
        <dd>{run.parent_run_id ?? "None"}</dd>
        <dt>Schedule</dt>
        <dd>{run.schedule_id ?? "None"}</dd>
        <dt>Webhook</dt>
        <dd>{run.webhook_id ?? "None"}</dd>
        <dt>Chat</dt>
        <dd>
          {run.chat_id === null ? (
            "None"
          ) : (
            <NavLink to={`/chat?chat=${encodeURIComponent(run.chat_id)}`}>Open linked chat</NavLink>
          )}
        </dd>
      </dl>
      <div className="run-detail-actions">
        {canEdit && ["queued", "running", "cancelling"].includes(run.status) ? (
          <button onClick={() => onCancel(run)} type="button">
            <Square aria-hidden="true" size={15} />
            {run.kind === "codex_cloud_task" ? "Stop tracking" : "Cancel"}
          </button>
        ) : null}
        {run.kind === "codex_cloud_task" &&
        ["queued", "running", "cancelling"].includes(run.status) ? (
          <p className="warning-banner">
            Stopping this VoxOS run does not cancel the task already running in Codex Cloud.
          </p>
        ) : null}
        {canEdit &&
        run.kind !== "codex_cloud_task" &&
        ["failed", "lost"].includes(run.status) ? (
          <button onClick={() => onRetry(run)} type="button">
            <RotateCcw aria-hidden="true" size={15} />
            Retry
          </button>
        ) : null}
      </div>
      {run.error_message !== null ? <p className="error-text">{run.error_message}</p> : null}
      <div className="run-event-list">
        {events.map((event) => (
          <article className="run-event-item" key={event.id}>
            <strong>{event.kind.replaceAll("_", " ")}</strong>
            <small>{formatDateTime(event.created_at)}</small>
            <code>{eventPayloadSummary(event.payload_json)}</code>
          </article>
        ))}
      </div>
    </aside>
  );
}

function StatusChip({ status }: { status: string }) {
  return <span className={`status-chip ${status}`}>{status.replaceAll("_", " ")}</span>;
}

function tabIcon(tab: OperationsTab) {
  if (tab === "scheduled") {
    return <CalendarClock aria-hidden="true" size={16} />;
  }
  if (tab === "history") {
    return <History aria-hidden="true" size={16} />;
  }
  if (tab === "webhooks") {
    return <Webhook aria-hidden="true" size={16} />;
  }
  if (tab === "events") {
    return <Radio aria-hidden="true" size={16} />;
  }
  return <Loader2 aria-hidden="true" size={16} />;
}

function tabLabel(tab: OperationsTab): string {
  return tab[0].toUpperCase() + tab.slice(1);
}

function prependEvent(current: AgentRunEvent[], event: AgentRunEvent): AgentRunEvent[] {
  return [event, ...current.filter((item) => item.id !== event.id)].slice(0, 200);
}

function shortId(id: string): string {
  return id.slice(0, 8);
}

function relativeTime(value: string | null): string {
  if (value === null) {
    return "None";
  }
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return "Unknown";
  }
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) {
    return `${seconds}s ago`;
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.round(minutes / 60);
  return `${hours}h ago`;
}

function formatDateTime(value: string | null): string {
  if (value === null) {
    return "None";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function eventPayloadSummary(payload: Record<string, unknown>): string {
  const entries = Object.entries(payload).slice(0, 3);
  if (entries.length === 0) {
    return "{}";
  }
  return entries
    .map(([key, value]) => `${key}: ${payloadValue(value)}`)
    .join(", ");
}

function payloadValue(value: unknown): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (value === null) {
    return "null";
  }
  return "object";
}

function webhookCurl(webhook: AgentWebhookCredential): string {
  const continuation = "\\";
  return [
    `curl --request POST '${webhook.trigger_url ?? webhook.trigger_path}' ${continuation}`,
    `  --header 'Authorization: Bearer ${webhook.token}' ${continuation}`,
    `  --header 'Content-Type: application/json' ${continuation}`,
    `  --header 'Idempotency-Key: replace-with-source-event-id' ${continuation}`,
    "  --data '{\"event\":\"example\"}'",
  ].join("\n");
}

function localDatetimeToIso(date: string, hour: string, minute: string): string {
  return new Date(`${date}T${hour}:${minute}`).toISOString();
}

function defaultRunAtParts(): Pick<
  ScheduleFormState,
  "runDate" | "runHour" | "runMinute"
> {
  const date = new Date(Date.now() + 60 * 60 * 1000);
  date.setSeconds(0, 0);
  return {
    runDate: `${date.getFullYear()}-${twoDigits(date.getMonth() + 1)}-${twoDigits(
      date.getDate(),
    )}`,
    runHour: twoDigits(date.getHours()),
    runMinute: twoDigits(date.getMinutes()),
  };
}

function twoDigits(value: number): string {
  return String(value).padStart(2, "0");
}
