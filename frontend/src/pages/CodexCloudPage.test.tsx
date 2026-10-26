import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createCodexCloudTask,
  loadAgents,
  loadCodexAccount,
  loadCodexCloudTasks,
  loadChats,
  type Agent,
  type CodexCloudTask,
} from "../api";
import { CodexCloudPage } from "./CodexCloudPage";

vi.mock("../api", () => ({
  cancelCodexLogin: vi.fn(),
  createCodexCloudTask: vi.fn(),
  disconnectCodexAccount: vi.fn(),
  loadAgents: vi.fn(),
  loadCodexAccount: vi.fn(),
  loadCodexAuthSession: vi.fn(),
  loadCodexCloudTasks: vi.fn(),
  loadChats: vi.fn(),
  saveCodexEnvironment: vi.fn(),
  startCodexLogin: vi.fn(),
}));

describe("CodexCloudPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(loadAgents).mockResolvedValue([agent]);
    vi.mocked(loadCodexCloudTasks).mockResolvedValue([]);
    vi.mocked(loadChats).mockResolvedValue([]);
    vi.mocked(loadCodexAccount).mockResolvedValue({
      connected: true,
      id: "account-1",
      status: "connected",
      email: "coder@example.com",
      plan_type: "plus",
      environment_id: "env-1",
      environment_label: "Demo",
      authenticated_at: "2026-08-20T12:00:00Z",
      updated_at: "2026-08-20T12:00:00Z",
    });
    vi.mocked(createCodexCloudTask).mockResolvedValue(task);
  });

  it("queues a task against the connected subscription account", async () => {
    const user = userEvent.setup();
    render(<CodexCloudPage canEdit onLog={vi.fn()} />);

    await screen.findByText("coder@example.com");
    await user.type(screen.getByLabelText("Task"), "Fix tenant isolation");
    await user.type(screen.getByLabelText("Branch (optional)"), "main");
    await user.selectOptions(screen.getByLabelText("Attempts"), "2");
    await user.click(screen.getByRole("button", { name: "Queue Codex Cloud task" }));

    await waitFor(() => {
      expect(createCodexCloudTask).toHaveBeenCalledWith(
        expect.objectContaining({
          agent_id: agent.id,
          attempts: 2,
          branch: "main",
          prompt: "Fix tenant isolation",
        }),
      );
    });
    expect(await screen.findByText("Queued task")).toBeInTheDocument();
  });
});

const agent: Agent = {
  id: "agent-1",
  owner_user_id: "user-1",
  slug: "default",
  display_name: "Default Agent",
  description: null,
  status: "active",
  is_default: true,
  created_at: "2026-08-20T12:00:00Z",
  updated_at: "2026-08-20T12:00:00Z",
};

const task = {
  id: "task-1",
  environment_id: "env-1",
  branch: "main",
  attempts: 2,
  submission_state: "pending",
  remote_task_id: null,
  remote_task_url: null,
  remote_status: "pending",
  summary: "Queued task",
  created_at: "2026-08-20T12:00:00Z",
  updated_at: "2026-08-20T12:00:00Z",
  run: {
    id: "run-1",
    user_id: "user-1",
    agent_id: "agent-1",
    kind: "codex_cloud_task",
    status: "queued",
    queue: "codex-cloud",
    priority: 0,
    session_id: null,
    chat_id: null,
    parent_run_id: null,
    root_run_id: "run-1",
    schedule_id: null,
    webhook_id: null,
    task_plan_id: null,
    attempt: 0,
    max_attempts: 3,
    created_at: "2026-08-20T12:00:00Z",
    updated_at: "2026-08-20T12:00:00Z",
    next_attempt_at: null,
    started_at: null,
    completed_at: null,
    heartbeat_at: null,
    lease_owner: null,
    lease_expires_at: null,
    cancel_requested_at: null,
    cancel_reason: null,
    error_type: null,
    error_message: null,
    child_run_count: 0,
    payload_json: {},
    result_json: {},
  },
} satisfies CodexCloudTask;
