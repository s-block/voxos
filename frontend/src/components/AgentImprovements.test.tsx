import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadAgentImprovements, loadLearningSettings, saveLearningSettings, reviewAgentImprovement, loadImprovementEvidence, type AgentImprovement } from "../api";
import { AgentImprovements } from "./AgentImprovements";

vi.mock("../api", () => ({ loadAgentImprovements: vi.fn(), loadLearningSettings: vi.fn(), saveLearningSettings: vi.fn(), reviewAgentImprovement: vi.fn(), loadImprovementEvidence: vi.fn() }));

const proposal: AgentImprovement = {
  id: "proposal", status: "proposed", content_hash: "a".repeat(64), candidate: { payload: { kind: "memory", memory_kind: "preference", text: "Prefer concise answers", sensitivity: "low", target: null }, evidence: [{ kind: "transcript", key: "source" }], reason: "Explicit preference", confidence: 0.99 }, policy_version: "tenant-learning-v1", decision_mode: "policy_review", reasons: ["account_review_only"], assessment: null, before: {}, after: {}, created_at: "2026-10-05T00:00:00Z", reviewed_at: null,
};

describe("AgentImprovements", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(loadAgentImprovements).mockResolvedValue([proposal]);
    vi.mocked(loadLearningSettings).mockResolvedValue({ automatic: false, chosen_at: null });
    vi.mocked(loadImprovementEvidence).mockResolvedValue({ items: [{ reference: { kind: "transcript", key: "source" }, conversation: "chat", category: "transcript", summary: "I prefer concise answers", created_at: "2026-10-05T00:00:00Z", context_withheld: false }], unavailable: 0, truncated: false, coverage: "Retained evidence" });
  });

  it("saves standing consent, reviews exact content, reads evidence and undoes", async () => {
    const user = userEvent.setup();
    vi.mocked(saveLearningSettings).mockResolvedValue({ automatic: true, chosen_at: "2026-10-05T00:00:00Z" });
    vi.mocked(reviewAgentImprovement).mockResolvedValueOnce({ ...proposal, status: "applied", decision_mode: "user_review" }).mockResolvedValueOnce({ ...proposal, status: "archived" });
    render(<AgentImprovements canEdit />);
    await screen.findByText("Explicit preference");
    await user.click(screen.getByRole("checkbox"));
    await waitFor(() => expect(saveLearningSettings).toHaveBeenCalledWith(true));
    await user.click(screen.getByRole("button", { name: "Read cited evidence" }));
    expect(await screen.findByText("I prefer concise answers")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await screen.findByRole("button", { name: "Undo" });
    expect(reviewAgentImprovement).toHaveBeenCalledWith(proposal, "approve");
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(await screen.findByText("memory · archived")).toBeInTheDocument();
  });

  it("keeps a stale review visible and shows its conflict", async () => {
    const user = userEvent.setup();
    vi.mocked(reviewAgentImprovement).mockRejectedValue(new Error("Knowledge changed since this proposal"));
    render(<AgentImprovements canEdit />);
    await screen.findByText("Explicit preference");
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Knowledge changed since this proposal");
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("allows reading but hides writes on a read-only connection", async () => {
    render(<AgentImprovements canEdit={false} />);
    await screen.findByText("Explicit preference");
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Read cited evidence" })).toBeEnabled();
  });
});
