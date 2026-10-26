import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createAgentWebhook,
  createSchedule,
  loadActiveRuns,
  loadAgents,
  loadAgentWebhooks,
  loadBrowserAccounts,
  loadChats,
  loadRuns,
  loadSchedules,
  streamRunEvents,
  type AgentRunSummary,
  type AgentSchedule,
  type BrowserAccount,
} from "../api";
import { OperationsPage } from "./OperationsPage";

vi.mock("../api", () => ({
  cancelRun: vi.fn(),
  createAgentWebhook: vi.fn(),
  createSchedule: vi.fn(),
  deleteAgentWebhook: vi.fn(),
  deleteSchedule: vi.fn(),
  loadActiveRuns: vi.fn(),
  loadAgents: vi.fn(),
  loadAgentWebhooks: vi.fn(),
  loadBrowserAccounts: vi.fn(),
  loadChats: vi.fn(),
  loadRun: vi.fn(),
  loadRunEvents: vi.fn(),
  loadRuns: vi.fn(),
  loadSchedules: vi.fn(),
  pauseSchedule: vi.fn(),
  resumeSchedule: vi.fn(),
  retryRun: vi.fn(),
  rotateAgentWebhookToken: vi.fn(),
  runScheduleNow: vi.fn(),
  streamRunEvents: vi.fn(),
  updateAgentWebhook: vi.fn(),
}));

describe("OperationsPage schedules", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(loadAgents).mockResolvedValue([
      {
        created_at: "2026-07-29T10:00:00Z",
        description: null,
        display_name: "Lars",
        id: "agent-1",
        is_default: true,
        owner_user_id: "user-1",
        slug: "lars",
        status: "active",
        updated_at: "2026-07-29T10:00:00Z",
      },
    ]);
    vi.mocked(loadActiveRuns).mockResolvedValue([]);
    vi.mocked(loadRuns).mockResolvedValue([]);
    vi.mocked(loadSchedules).mockResolvedValue([]);
    vi.mocked(loadAgentWebhooks).mockResolvedValue([]);
    vi.mocked(loadBrowserAccounts).mockResolvedValue([]);
    vi.mocked(loadChats).mockResolvedValue([]);
    vi.mocked(streamRunEvents).mockResolvedValue();
    vi.mocked(createSchedule).mockResolvedValue(createdSchedule);
    vi.mocked(createAgentWebhook).mockResolvedValue({
      agent_id: "agent-1",
      created_at: "2026-08-25T12:00:00Z",
      enabled: true,
      id: "webhook-1",
      instruction: "Qualify the incoming lead.",
      name: "CRM lead",
      token: "issued-value-123",
      trigger_path: "/api/agent-webhooks/webhook-1/deliver",
      trigger_url: "https://voxos.example/api/agent-webhooks/webhook-1/deliver",
      updated_at: "2026-08-25T12:00:00Z",
      user_id: "user-1",
    });
  });

  it("selects and submits a one-shot time in 24-hour format", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <MemoryRouter initialEntries={["/operations/scheduled"]}>
        <OperationsPage
          activeTab="scheduled"
          canEdit
          onLog={vi.fn()}
        />
      </MemoryRouter>,
    );

    await screen.findByRole("option", { name: "Lars" });

    const hourSelect = screen.getByLabelText("Run hour (24-hour)");
    const minuteSelect = screen.getByLabelText("Run minute");
    expect(within(hourSelect).getAllByRole("option")).toHaveLength(24);
    expect(within(hourSelect).getByRole("option", { name: "23" })).toBeInTheDocument();
    expect(within(minuteSelect).getAllByRole("option")).toHaveLength(60);
    expect(container.querySelector('input[type="datetime-local"]')).toBeNull();

    fireEvent.change(screen.getByLabelText("Run date"), {
      target: { value: "2030-01-02" },
    });
    await user.selectOptions(hourSelect, "23");
    await user.selectOptions(minuteSelect, "45");
    await user.type(screen.getByPlaceholderText("Schedule name"), "Late task");
    await user.type(
      screen.getByPlaceholderText("Scheduled instruction"),
      "Prepare the daily report",
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(createSchedule).toHaveBeenCalledWith({
        agent_id: "agent-1",
        interval_seconds: null,
        kind: "one_shot",
        name: "Late task",
        payload_json: { text: "Prepare the daily report" },
        run_at: new Date("2030-01-02T23:45").toISOString(),
      });
    });
  });

  it("binds an assigned ready Browserless account to a schedule", async () => {
    const user = userEvent.setup();
    vi.mocked(loadBrowserAccounts).mockResolvedValue([scheduledBrowserAccount]);
    render(
      <MemoryRouter initialEntries={["/operations/scheduled"]}>
        <OperationsPage activeTab="scheduled" canEdit onLog={vi.fn()} />
      </MemoryRouter>,
    );

    await screen.findByRole("option", { name: scheduledBrowserAccount.label });
    await user.selectOptions(
      screen.getByLabelText("Browser Account (optional)"),
      scheduledBrowserAccount.id,
    );
    await user.type(screen.getByPlaceholderText("Schedule name"), "Bill check");
    await user.type(
      screen.getByPlaceholderText("Scheduled instruction"),
      "Read my current bill",
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(createSchedule).toHaveBeenCalledWith(
      expect.objectContaining({
        browser_account_id: scheduledBrowserAccount.id,
        payload_json: { text: "Read my current bill" },
      }),
    );
  });

  it("describes Codex cancellation as stopping local tracking", async () => {
    vi.mocked(loadActiveRuns).mockResolvedValue([codexCloudRun]);
    render(
      <MemoryRouter initialEntries={["/operations"]}>
        <OperationsPage activeTab="active" canEdit onLog={vi.fn()} />
      </MemoryRouter>,
    );

    expect(await screen.findByTitle("Stop tracking")).toBeInTheDocument();
    expect(screen.queryByTitle("Cancel run")).not.toBeInTheDocument();
  });

  it("creates a webhook and shows its one-time curl command", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/operations/webhooks"]}>
        <OperationsPage activeTab="webhooks" canEdit onLog={vi.fn()} />
      </MemoryRouter>,
    );

    await screen.findByRole("option", { name: "Lars" });
    await user.type(screen.getByPlaceholderText("CRM lead received"), "CRM lead");
    await user.type(
      screen.getByPlaceholderText(
        "Qualify the lead using the JSON event and record the next action.",
      ),
      "Qualify the incoming lead.",
    );
    await user.click(screen.getByRole("button", { name: "Create webhook" }));

    await waitFor(() => {
      expect(createAgentWebhook).toHaveBeenCalledWith({
        agent_id: "agent-1",
        instruction: "Qualify the incoming lead.",
        name: "CRM lead",
      });
    });
    expect(await screen.findByText("Copy this credential now")).toBeInTheDocument();
    expect(screen.getByText(/Authorization: Bearer issued-value-123/)).toBeInTheDocument();
    expect(screen.getByText(/Idempotency-Key: replace-with-source-event-id/)).toBeInTheDocument();
  });
});

const codexCloudRun: AgentRunSummary = {
  id: "run-codex-1",
  user_id: "user-1",
  agent_id: "agent-1",
  kind: "codex_cloud_task",
  status: "running",
  queue: "codex-cloud",
  priority: 0,
  session_id: null,
  chat_id: null,
  parent_run_id: null,
  root_run_id: "run-codex-1",
  schedule_id: null,
  webhook_id: null,
  task_plan_id: null,
  attempt: 1,
  max_attempts: 3,
  created_at: "2026-08-20T12:00:00Z",
  updated_at: "2026-08-20T12:00:00Z",
  next_attempt_at: null,
  started_at: "2026-08-20T12:00:00Z",
  completed_at: null,
  heartbeat_at: "2026-08-20T12:00:00Z",
  lease_owner: "codex-worker",
  lease_expires_at: "2026-08-20T12:03:00Z",
  cancel_requested_at: null,
  cancel_reason: null,
  error_type: null,
  error_message: null,
  child_run_count: 0,
};

const createdSchedule: AgentSchedule = {
  agent_id: "agent-1",
  chat_id: "chat-1",
  browser_account_id: null,
  catch_up_policy: "skip_missed",
  created_at: "2026-07-29T10:00:00Z",
  cron_expression: null,
  enabled: true,
  id: "schedule-1",
  interval_seconds: null,
  kind: "one_shot",
  last_run_at: null,
  last_run_id: null,
  last_run_status: null,
  max_concurrent_runs: 1,
  misfire_grace_seconds: 60,
  name: "Late task",
  next_run_at: "2030-01-02T23:45:00Z",
  payload_json: { text: "Prepare the daily report" },
  retry_policy_json: {},
  run_at: "2030-01-02T23:45:00Z",
  status: "active",
  timezone: "UTC",
  updated_at: "2026-07-29T10:00:00Z",
  user_id: "user-1",
};

const scheduledBrowserAccount: BrowserAccount = {
  agent_ids: ["agent-1"],
  authentication_mode: "stored_credentials",
  authentication_origins: [],
  browser_country_code: "GB",
  created_at: "2026-07-29T10:00:00Z",
  error_category: null,
  has_credentials: true,
  id: "browser-account-1",
  label: "Billing",
  last_used_at: null,
  last_verified_at: "2026-07-29T10:00:00Z",
  primary_origin: "https://billing.example.com",
  provider: "browserless",
  provider_connection_id: "browserless-connection",
  status: "ready",
  updated_at: "2026-07-29T10:00:00Z",
};
