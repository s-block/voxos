import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useToolGrants } from "../toolGrants";
import { useToolRoutes } from "../toolRoutes";
import { ToolsSettingsPage } from "./ToolsSettingsPage";

vi.mock("../toolGrants", () => ({
  isToolAllowed: vi.fn(() => false),
  useToolGrants: vi.fn(),
}));
vi.mock("../toolRoutes", () => ({
  useToolRoutes: vi.fn(),
}));

describe("ToolsSettingsPage", () => {
  const setRoute = vi.fn(async () => undefined);

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useToolGrants).mockReturnValue({
      error: null,
      grants: [],
      internalTools: [],
      isLoading: false,
      isSaving: false,
      refreshGrants: vi.fn(async () => undefined),
      setGrant: vi.fn(async () => undefined),
      setGrantsForAllAgents: vi.fn(async () => undefined),
    });
    vi.mocked(useToolRoutes).mockReturnValue({
      candidates: [
        {
          action: "web.search",
          automatic_priority: 10,
          provider_label: "Exa",
          reference: {
            integration_id: null,
            mcp_server_id: "exa-server",
            risk: "low",
            tool_name: "web_search_exa",
            tool_source: "mcp_server",
          },
        },
        {
          action: "web.search",
          automatic_priority: 100,
          provider_label: "Browserless",
          reference: {
            integration_id: null,
            mcp_server_id: "browserless-server",
            risk: "medium",
            tool_name: "browserless_search",
            tool_source: "mcp_server",
          },
        },
      ],
      definitions: [
        {
          action: "web.search",
          description: "Provider used for general web searches.",
          label: "Web search",
        },
      ],
      error: null,
      isLoading: false,
      isSaving: false,
      preferences: [
        {
          action: "web.search",
          agent_id: "agent-123",
          id: null,
          mode: "automatic",
          pinned_tool: null,
          user_id: "user-123",
        },
      ],
      refreshRoutes: vi.fn(async () => undefined),
      setRoute,
    });
  });

  it("shows the automatic provider and can pin an alternate implementation", async () => {
    const user = userEvent.setup();
    render(
      <ToolsSettingsPage
        canEdit
        onLog={vi.fn()}
      />,
    );

    const select = screen.getByRole("combobox", {
      name: "Web search provider",
    });
    expect(screen.getByRole("option", { name: "Automatic (Exa)" })).toBeVisible();

    await user.selectOptions(
      select,
      "mcp_server:browserless-server::browserless_search",
    );

    expect(setRoute).toHaveBeenCalledWith({
      action: "web.search",
      mode: "pinned",
      pinned_tool: {
        integration_id: null,
        mcp_server_id: "browserless-server",
        tool_name: "browserless_search",
        tool_source: "mcp_server",
      },
    });
  });
});
