import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createBrowserAccount,
  loadAgents,
  loadBrowserAccounts,
  loadMcpServers,
  startBrowserAccountSetup,
  updateBrowserAccount,
  updateBrowserAccountCredentials,
  type Agent,
  type BrowserAccount,
  type McpServer,
} from "../api";
import { BrowserAccountsPage } from "./BrowserAccountsPage";

vi.mock("../api", () => ({
  cancelBrowserAccountSetup: vi.fn(),
  completeBrowserAccountSetup: vi.fn(),
  createBrowserAccount: vi.fn(),
  deleteBrowserAccount: vi.fn(),
  loadAgents: vi.fn(),
  loadBrowserAccounts: vi.fn(),
  loadMcpServers: vi.fn(),
  reauthenticateBrowserAccount: vi.fn(),
  revokeBrowserAccount: vi.fn(),
  setBrowserAccountAgents: vi.fn(),
  startBrowserAccountSetup: vi.fn(),
  updateBrowserAccount: vi.fn(),
  updateBrowserAccountCredentials: vi.fn(),
}));

describe("BrowserAccountsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(loadBrowserAccounts).mockResolvedValue([]);
    vi.mocked(loadAgents).mockResolvedValue([agent]);
    vi.mocked(loadMcpServers).mockResolvedValue([browserUseConnection]);
  });

  it("creates a local Browser Account without an MCP connection", async () => {
    const user = userEvent.setup();
    vi.mocked(loadMcpServers).mockResolvedValue([]);
    vi.mocked(createBrowserAccount).mockResolvedValue(localBrowserAccount);

    render(<BrowserAccountsPage canEdit onLog={vi.fn()} />);

    await screen.findByRole("option", {
      name: "Voxos Local Browser (recommended)",
    });
    await user.type(screen.getByLabelText("Account label"), "Local bills");
    await user.type(
      screen.getByLabelText("Exact website origin"),
      "https://billing.example.com",
    );
    await user.type(
      screen.getByLabelText("Website username or email"),
      "billing@example.com",
    );
    await user.type(screen.getByLabelText("Website password"), "password-123");
    await user.click(
      screen.getByRole("button", { name: "Create Browser Account" }),
    );

    expect(createBrowserAccount).toHaveBeenCalledWith({
      agent_ids: [],
      authentication_origins: [],
      browser_country_code: "GB",
      label: "Local bills",
      password: "password-123", // pragma: allowlist secret
      primary_origin: "https://billing.example.com",
      provider: "stagehand_local",
      username: "billing@example.com",
    });
  });

  it("creates an account with assignments off by default and no password field", async () => {
    const user = userEvent.setup();
    vi.mocked(createBrowserAccount).mockResolvedValue(browserAccount);

    render(<BrowserAccountsPage canEdit onLog={vi.fn()} />);

    await screen.findByRole("option", { name: "Browser Use Cloud" });
    await user.selectOptions(
      screen.getByLabelText("Browser runtime"),
      browserUseConnection.id,
    );
    expect(screen.getByText("Browser country")).toBeInTheDocument();
    expect(screen.getByLabelText(agent.display_name)).not.toBeChecked();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Account label"), "Work account");
    await user.type(
      screen.getByLabelText("Exact website origin"),
      "https://example.com",
    );
    await user.type(
      screen.getByLabelText("Login and SSO origins"),
      "https://login.example.com",
    );
    await user.click(
      screen.getByRole("button", { name: "Create Browser Account" }),
    );

    expect(createBrowserAccount).toHaveBeenCalledWith({
      label: "Work account",
      primary_origin: "https://example.com",
      authentication_origins: ["https://login.example.com"],
      browser_country_code: "GB",
      provider: "browser_use_cloud",
      provider_connection_id: browserUseConnection.id,
      agent_ids: [],
    });
    expect(
      await screen.findByRole("heading", { name: browserAccount.label }),
    ).toBeInTheDocument();
  });

  it("keeps the short-lived setup URL out of rendered state", async () => {
    const user = userEvent.setup();
    const replace = vi.fn();
    const close = vi.fn();
    const setupWindow = {
      close,
      location: { replace },
      opener: window,
    };
    const open = vi
      .spyOn(window, "open")
      .mockReturnValue(setupWindow as unknown as Window);
    vi.mocked(loadBrowserAccounts).mockResolvedValue([browserAccount]);
    vi.mocked(startBrowserAccountSetup).mockResolvedValue({
      account: { ...browserAccount, status: "setup_in_progress" },
      expires_at: "2026-08-07T12:15:00Z",
      live_url: "https://live.browser-use.com/private-session-token",
    });

    render(<BrowserAccountsPage canEdit onLog={vi.fn()} />);
    await user.click(
      await screen.findByRole("button", { name: "Start secure sign-in" }),
    );

    await waitFor(() => {
      expect(open).toHaveBeenCalledWith("about:blank", "_blank");
      expect(setupWindow.opener).toBeNull();
      expect(replace).toHaveBeenCalledWith(
        "https://live.browser-use.com/private-session-token",
      );
    });
    expect(
      screen.queryByText("https://live.browser-use.com/private-session-token"),
    ).not.toBeInTheDocument();
    expect(close).not.toHaveBeenCalled();
    open.mockRestore();
  });

  it("creates a ready Browserless account with website credentials", async () => {
    const user = userEvent.setup();
    vi.mocked(loadMcpServers).mockResolvedValue([browserlessConnection]);
    vi.mocked(createBrowserAccount).mockResolvedValue(browserlessAccount);

    render(<BrowserAccountsPage canEdit onLog={vi.fn()} />);

    await screen.findByRole("option", { name: "Browserless" });
    await user.selectOptions(
      screen.getByLabelText("Browser runtime"),
      browserlessConnection.id,
    );
    expect(screen.queryByText("Browser country")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Account label"), "Bills");
    await user.type(
      screen.getByLabelText("Exact website origin"),
      "https://billing.example.com",
    );
    await user.type(
      screen.getByLabelText("Website username or email"),
      "billing@example.com",
    );
    await user.type(screen.getByLabelText("Website password"), "password-123");
    await user.click(
      screen.getByRole("button", { name: "Create Browser Account" }),
    );

    expect(createBrowserAccount).toHaveBeenCalledWith({
      agent_ids: [],
      authentication_origins: [],
      browser_country_code: "GB",
      label: "Bills",
      password: "password-123", // pragma: allowlist secret
      primary_origin: "https://billing.example.com",
      provider: "browserless",
      provider_connection_id: browserlessConnection.id,
      username: "billing@example.com",
    });
    expect(
      await screen.findByRole("heading", { name: browserlessAccount.label }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Start secure sign-in" }),
    ).not.toBeInTheDocument();
    expect(updateBrowserAccountCredentials).not.toHaveBeenCalled();
  });

  it("updates an existing account label and sign-in origins", async () => {
    const user = userEvent.setup();
    const updatedAccount: BrowserAccount = {
      ...browserlessAccount,
      authentication_origins: ["https://auth.eonnext.com"],
      browser_country_code: "GB",
      label: "Eon Next",
      primary_origin: "https://eonnext.com",
    };
    vi.mocked(loadBrowserAccounts).mockResolvedValue([browserlessAccount]);
    vi.mocked(loadMcpServers).mockResolvedValue([browserlessConnection]);
    vi.mocked(updateBrowserAccount).mockResolvedValue(updatedAccount);

    render(<BrowserAccountsPage canEdit onLog={vi.fn()} />);

    await user.click(
      await screen.findByRole("button", { name: "Edit details" }),
    );
    expect(
      screen.queryByLabelText(
        `Edit browser country for ${browserlessAccount.label}`,
      ),
    ).not.toBeInTheDocument();
    const label = screen.getByLabelText(
      `Edit account label for ${browserlessAccount.label}`,
    );
    await user.clear(label);
    await user.type(label, "Eon Next");
    const primaryOrigin = screen.getByLabelText(
      `Edit website origin for ${browserlessAccount.label}`,
    );
    await user.clear(primaryOrigin);
    await user.type(primaryOrigin, "https://eonnext.com");
    await user.type(
      screen.getByLabelText(
        `Edit login and SSO origins for ${browserlessAccount.label}`,
      ),
      "https://auth.eonnext.com",
    );
    await user.click(screen.getByRole("button", { name: "Save details" }));

    expect(updateBrowserAccount).toHaveBeenCalledWith(browserlessAccount.id, {
      authentication_origins: ["https://auth.eonnext.com"],
      browser_country_code: "GB",
      label: "Eon Next",
      primary_origin: "https://eonnext.com",
    });
    expect(
      await screen.findByRole("heading", { name: "Eon Next" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Sign-in origins: https://auth.eonnext.com"),
    ).toBeInTheDocument();
  });

  it("blocks Browserless setup until its execution tool is allowed", async () => {
    vi.mocked(loadMcpServers).mockResolvedValue([
      {
        ...browserlessConnection,
        allowed_tools: ["browserless_search"],
        discovered_tools: ["browserless_search", "browserless_agent"],
      },
    ]);

    render(<BrowserAccountsPage canEdit onLog={vi.fn()} />);

    expect(
      await screen.findByText(/Browserless \(browserless_agent\)/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "MCP page" })).toHaveAttribute(
      "href",
      "/mcp",
    );
    expect(
      screen.queryByRole("option", { name: "Browserless" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create Browser Account" }),
    ).toBeDisabled();
    expect(createBrowserAccount).not.toHaveBeenCalled();
  });
});

const agent: Agent = {
  created_at: "2026-08-07T12:00:00Z",
  description: null,
  display_name: "Agent One",
  id: "agent-1",
  is_default: true,
  owner_user_id: "user-1",
  slug: "agent-one",
  status: "active",
  updated_at: "2026-08-07T12:00:00Z",
};

const browserAccount: BrowserAccount = {
  agent_ids: [],
  has_credentials: false,
  authentication_mode: "managed_profile",
  authentication_origins: ["https://login.example.com"],
  browser_country_code: "GB",
  created_at: "2026-08-07T12:00:00Z",
  error_category: null,
  id: "browser-account-1",
  label: "Work account",
  last_used_at: null,
  last_verified_at: null,
  primary_origin: "https://example.com",
  provider: "browser_use_cloud",
  provider_connection_id: "mcp-browser-use-1",
  status: "setup_required",
  updated_at: "2026-08-07T12:00:00Z",
};

const browserUseConnection: McpServer = {
  allowed_tools: ["run_session"],
  auth: {
    authenticated: true,
    expires_at: null,
    needs_reauth: false,
    scopes: [],
  },
  auth_type: "api_key_header",
  created_at: "2026-08-07T12:00:00Z",
  discovered_tools: ["run_session"],
  enabled: true,
  id: "mcp-browser-use-1",
  label: "Browser Use Cloud",
  logo: null,
  non_admin_tools_enabled: false,
  optional_auth: null,
  optional_auth_configured: false,
  provider_auth: null,
  safe_metadata: {
    catalog_version: "2026-08-07",
    category: "browser_automation",
    default_allowed_tools: [],
    docs_urls: [],
    logo: null,
    platform_default: false,
    preset_id: "browser-use-cloud",
    preset_label: "Browser Use Cloud",
    preset_variant_id: "hosted",
    researched_at: "2026-08-07",
    tool_definitions: [],
    tool_guidance: [],
    warnings: [],
  },
  status: "connected",
  status_message: null,
  transport: "streamable_http",
  trust_state: "trusted",
  updated_at: "2026-08-07T12:00:00Z",
  url: "https://api.browser-use.com/mcp",
};

const browserlessConnection: McpServer = {
  ...browserUseConnection,
  allowed_tools: ["browserless_agent"],
  auth_type: "static_bearer",
  discovered_tools: ["browserless_agent"],
  id: "mcp-browserless-1",
  label: "Browserless",
  safe_metadata: {
    ...browserUseConnection.safe_metadata,
    preset_id: "browserless",
    preset_label: "Browserless",
  },
  url: "https://mcp.browserless.io/mcp",
};

const browserlessAccount: BrowserAccount = {
  ...browserAccount,
  authentication_mode: "stored_credentials",
  authentication_origins: [],
  has_credentials: true,
  id: "browser-account-browserless",
  label: "Bills",
  last_verified_at: "2026-08-07T12:00:00Z",
  primary_origin: "https://billing.example.com",
  provider: "browserless",
  provider_connection_id: browserlessConnection.id,
  status: "ready",
};

const localBrowserAccount: BrowserAccount = {
  ...browserlessAccount,
  id: "browser-account-local",
  label: "Local bills",
  provider: "stagehand_local",
  provider_connection_id: null,
};
