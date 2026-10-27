import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createMcpServerFromPreset,
  deleteMcpServer,
  loadMcpPresets,
  loadMcpServers,
  saveMcpStaticAuth,
  startMcpOAuth,
  testMcpServer,
  updateMcpServer,
  type McpPresetListResponse,
  type McpServer,
  type McpServicePreset,
  type ToolGrant,
  type ToolGrantUpdate,
} from "./api";
import { McpSettingsPanel } from "./McpSettingsPanel";

vi.mock("./api", () => ({
  createMcpServer: vi.fn(),
  createMcpServerFromPreset: vi.fn(),
  deleteMcpServer: vi.fn(),
  loadMcpPresets: vi.fn(),
  loadMcpServers: vi.fn(),
  mcpOAuthRedirectUri: vi.fn(() => "/oauth/callback"),
  revokeMcpAuth: vi.fn(),
  saveMcpStaticAuth: vi.fn(),
  startMcpOAuth: vi.fn(),
  testMcpServer: vi.fn(),
  updateMcpServer: vi.fn(),
}));

describe("McpSettingsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(loadMcpPresets).mockResolvedValue(emptyPresetCatalog);
    vi.mocked(loadMcpServers).mockResolvedValue([exaServer]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders configured MCP entries as compact collapsible cards", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();

    await screen.findByText("Exa 3");
    expect(screen.getAllByRole("img", { name: "Exa logo" })).not.toHaveLength(0);

    const card = configuredServerCard(container);
    expect(card).not.toHaveAttribute("open");

    await user.click(configuredServerSummary(container));

    expect(card).toHaveAttribute("open");
  });

  it("stores an optional API key for a configured keyless server", async () => {
    const user = userEvent.setup();
    const apiKey = "test-exa-api-key"; // pragma: allowlist secret
    const configuredServer = {
      ...exaServer,
      optional_auth_configured: true,
    };
    vi.mocked(saveMcpStaticAuth).mockResolvedValue(configuredServer);
    vi.mocked(testMcpServer).mockResolvedValue(configuredServer);
    const { container } = renderPanel();

    await screen.findByText("API key optional");
    await user.click(configuredServerSummary(container));
    await user.click(screen.getByRole("button", { name: "Auth" }));

    expect(screen.getByText("Authentication is optional")).toBeInTheDocument();
    expect(screen.getByLabelText("Header")).toHaveValue("x-api-key");
    await user.type(screen.getByPlaceholderText("Optional API key"), apiKey);
    await user.click(screen.getByRole("button", { name: "Save API key" }));

    await waitFor(() =>
      expect(saveMcpStaticAuth).toHaveBeenCalledWith(exaServer.id, {
        api_key_header_name: "x-api-key", // pragma: allowlist secret
        secret: apiKey,
      }),
    );
    expect(testMcpServer).toHaveBeenCalledWith(exaServer.id);
    expect(await screen.findAllByText("optional auth saved")).not.toHaveLength(0);
  });

  it("combines tool approval and agent access in one tool list", async () => {
    const { container } = renderPanel({ toolGrants: exaToolGrants });

    await screen.findByText("Tools and agent access");

    const accessRows = container.querySelectorAll(
      ".mcp-tool-selection .mcp-tool-access-row",
    );
    expect(accessRows).toHaveLength(2);
    expect(accessRows[0]).toHaveTextContent("web_fetch_exa");
    expect(accessRows[0]).toHaveTextContent("granted");
  });

  it("allows selected MCP tools for non-admin callers", async () => {
    const user = userEvent.setup();
    vi.mocked(updateMcpServer).mockResolvedValue({
      ...exaServer,
      non_admin_tools_enabled: true,
    });
    const { container } = renderPanel();

    await screen.findByText("Exa 3");
    await user.click(configuredServerSummary(container));
    await user.click(screen.getByRole("button", { name: "Advanced" }));
    await user.click(
      screen.getByLabelText("Allow selected tools for non-admin callers"),
    );

    await waitFor(() =>
      expect(updateMcpServer).toHaveBeenCalledWith(exaServer.id, {
        non_admin_tools_enabled: true,
      }),
    );
    expect(
      screen.getByLabelText("Allow selected tools for non-admin callers"),
    ).toBeChecked();
  });

  it("keeps the catalog detail panel synced to search results", async () => {
    const user = userEvent.setup();
    vi.mocked(loadMcpPresets).mockResolvedValue(catalogWithExaAndSlack);
    vi.mocked(loadMcpServers).mockResolvedValue([]);

    renderPanel();

    await screen.findByRole("heading", { name: "Exa" });

    await user.type(screen.getByLabelText("Search MCP catalog"), "slack");

    expect(
      await screen.findByRole("heading", { name: "Slack" }),
    ).toBeInTheDocument();
    expect(
      screen
        .getAllByRole("button", { name: /Slack/ })
        .some((button) => button.getAttribute("aria-pressed") === "true"),
    ).toBe(true);
    expect(
      screen.queryByRole("button", { name: "Add Exa" }),
    ).not.toBeInTheDocument();
  });

  it("explains missing OAuth client details before provider sign-in", async () => {
    vi.mocked(loadMcpPresets).mockResolvedValue(catalogWithGoogle);
    vi.mocked(loadMcpServers).mockResolvedValue([]);

    renderPanel();

    expect(
      await screen.findByText(
        "Enter the OAuth client ID and secret to start provider sign-in.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Connect Google Workspace" }),
    ).toBeDisabled();
  });

  it("discovers OAuth tools on return without granting PayPal actions", async () => {
    const user = userEvent.setup();
    const onSetToolGrants = vi.fn(async (): Promise<void> => undefined);
    vi.mocked(loadMcpPresets).mockResolvedValue(catalogWithPayPal);
    vi.mocked(loadMcpServers)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([authenticatedPayPalServer]);
    vi.mocked(createMcpServerFromPreset).mockResolvedValue(pendingPayPalServer);
    vi.mocked(startMcpOAuth).mockResolvedValue({
      authorization_url: "https://mcp.paypal.com/authorize",
      expires_at: "2026-07-22T12:00:00Z",
    });
    vi.mocked(testMcpServer).mockResolvedValue(connectedPayPalServer);
    vi.spyOn(window, "open").mockReturnValue(oauthPopupStub().popup);

    renderPanel({ onSetToolGrants });

    await user.click(await screen.findByRole("button", { name: "Connect PayPal" }));
    await waitFor(() => expect(startMcpOAuth).toHaveBeenCalledOnce());

    window.dispatchEvent(new Event("focus"));

    await waitFor(() =>
      expect(testMcpServer).toHaveBeenCalledWith(pendingPayPalServer.id),
    );
    expect(onSetToolGrants).not.toHaveBeenCalled();
    expect(await screen.findByText("connected")).toBeInTheDocument();
  });

  it("shows local and deployed Flightradar24 endpoint choices", async () => {
    const user = userEvent.setup();
    vi.mocked(loadMcpPresets).mockResolvedValue(catalogWithFlightradar24);
    vi.mocked(loadMcpServers).mockResolvedValue([]);

    renderPanel();

    await user.type(screen.getByLabelText("Search MCP catalog"), "flight");
    expect(
      await screen.findByRole("heading", { name: "Flightradar24" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Endpoint")).toHaveValue(
      "http://flightradar24-mcp:8000/mcp",
    );

    await user.selectOptions(
      screen.getByLabelText("Variant"),
      "deployed-https",
    );
    expect(screen.getByLabelText("Server URL")).toHaveAttribute(
      "placeholder",
      "https://your-flightradar24-mcp.example.com/mcp",
    );
  });

  it("stores the Monzo endpoint bearer before starting provider OAuth", async () => {
    const user = userEvent.setup();
    const onSetToolGrants = vi.fn(async (): Promise<void> => undefined);
    vi.mocked(loadMcpPresets).mockResolvedValue(catalogWithMonzo);
    vi.mocked(loadMcpServers).mockResolvedValue([]);
    vi.mocked(createMcpServerFromPreset).mockResolvedValue(pendingMonzoServer);
    vi.mocked(saveMcpStaticAuth).mockResolvedValue(authenticatedMonzoServer);
    vi.mocked(startMcpOAuth).mockResolvedValue({
      authorization_url: "https://auth.monzo.com/",
      expires_at: "2026-07-23T18:00:00Z",
    });

    renderPanel({ onSetToolGrants });

    await user.type(
      await screen.findByLabelText("MCP endpoint token"),
      "local-mcp-endpoint-token",
    );
    await user.type(screen.getByLabelText("OAuth client ID"), "monzo-client-id");
    await user.type(
      screen.getByLabelText("OAuth client secret"),
      "monzo-client-secret",
    );
    await user.click(screen.getByRole("button", { name: "Connect Monzo" }));

    await waitFor(() =>
      expect(saveMcpStaticAuth).toHaveBeenCalledWith(pendingMonzoServer.id, {
        api_key_header_name: null,
        secret: "local-mcp-endpoint-token", // pragma: allowlist secret
      }),
    );
    expect(startMcpOAuth).toHaveBeenCalledWith(pendingMonzoServer.id, {
      client_id: "monzo-client-id",
      client_secret: "monzo-client-secret", // pragma: allowlist secret
      redirect_uri: "/oauth/callback",
      scopes: [],
      token_endpoint_auth_method: null,
    });
    expect(createMcpServerFromPreset).toHaveBeenCalledWith("monzo", {
      allowed_tools: [],
      enabled: true,
      label: "Monzo",
      url: null,
      variant_id: "local-compose",
    });
    expect(testMcpServer).not.toHaveBeenCalled();
    expect(onSetToolGrants).not.toHaveBeenCalled();
  });

  it("replaces provider OAuth client credentials on an existing Monzo server", async () => {
    const user = userEvent.setup();
    vi.mocked(loadMcpServers).mockResolvedValue([pendingMonzoServer]);
    vi.mocked(startMcpOAuth).mockResolvedValue({
      authorization_url: "https://auth.monzo.com/",
      expires_at: "2026-07-24T14:00:00Z",
    });
    vi.spyOn(window, "open").mockReturnValue(oauthPopupStub().popup);
    const { container } = renderPanel();

    expect((await screen.findAllByText("Monzo")).length).toBeGreaterThan(0);
    await user.click(configuredServerSummary(container));
    await user.click(screen.getByRole("button", { name: "Auth" }));
    await user.type(
      screen.getByLabelText("OAuth client ID"),
      "oauthclient_0000000000000000000000",
    );
    await user.type(
      screen.getByLabelText("OAuth client secret"),
      "replacement-client-secret",
    );
    await user.click(
      screen.getByRole("button", { name: "Save client & start sign-in" }),
    );

    await waitFor(() =>
      expect(startMcpOAuth).toHaveBeenCalledWith(pendingMonzoServer.id, {
        client_id: "oauthclient_0000000000000000000000",
        client_secret: "replacement-client-secret", // pragma: allowlist secret
        redirect_uri: "/oauth/callback",
        scopes: [],
      }),
    );
  });

  it("reconnects Google using the stored OAuth client", async () => {
    const user = userEvent.setup();
    const popup = oauthPopupStub();
    vi.mocked(loadMcpServers).mockResolvedValue([expiredGoogleServer]);
    vi.mocked(startMcpOAuth).mockResolvedValue({
      authorization_url: "https://accounts.google.com/o/oauth2/v2/auth",
      expires_at: "2026-07-30T10:00:00Z",
    });
    vi.spyOn(window, "open").mockReturnValue(popup.popup);
    const { container } = renderPanel();

    expect(
      (await screen.findAllByText("Google Workspace")).length,
    ).toBeGreaterThan(0);
    await user.click(configuredServerSummary(container));
    await user.click(screen.getByRole("button", { name: "Auth" }));
    await user.click(
      screen.getByRole("button", { name: "Reconnect using stored client" }),
    );

    await waitFor(() =>
      expect(startMcpOAuth).toHaveBeenCalledWith(expiredGoogleServer.id, {
        client_id: null,
        client_secret: null,
        redirect_uri: "/oauth/callback",
        scopes: [],
      }),
    );
    expect(popup.replace).toHaveBeenCalledWith(
      "https://accounts.google.com/o/oauth2/v2/auth",
    );
  });

  it("shows Google reconnect failures inside the server card", async () => {
    const user = userEvent.setup();
    const popup = oauthPopupStub();
    vi.mocked(loadMcpServers).mockResolvedValue([expiredGoogleServer]);
    vi.mocked(startMcpOAuth).mockRejectedValue(
      new Error("Stored OAuth client could not be loaded."),
    );
    vi.spyOn(window, "open").mockReturnValue(popup.popup);
    const { container } = renderPanel();

    await screen.findAllByText("Google Workspace");
    const card = configuredServerCard(container);
    await user.click(configuredServerSummary(container));
    await user.click(screen.getByRole("button", { name: "Auth" }));
    await user.click(
      screen.getByRole("button", { name: "Reconnect using stored client" }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Stored OAuth client could not be loaded.");
    expect(card).toContainElement(alert);
    expect(popup.close).toHaveBeenCalledOnce();
  });

  it("rejects API Playground credentials and exposes server removal in Auth", async () => {
    const user = userEvent.setup();
    vi.mocked(loadMcpServers).mockResolvedValue([pendingMonzoServer]);
    vi.mocked(deleteMcpServer).mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { container } = renderPanel();

    expect((await screen.findAllByText("Monzo")).length).toBeGreaterThan(0);
    await user.click(configuredServerSummary(container));
    await user.click(screen.getByRole("button", { name: "Auth" }));
    await user.type(
      screen.getByLabelText("OAuth client ID"),
      "user_0000000000000000000000",
    );
    await user.type(
      screen.getByLabelText("OAuth client secret"),
      "eyJ.invalid.token",
    );

    expect(
      screen.getByText(
        "Use the Monzo OAuth client ID, not the API Playground user ID.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save client & start sign-in" }),
    ).toBeDisabled();

    await user.click(
      screen.getByRole("button", { name: "Delete Monzo server" }),
    );
    await waitFor(() =>
      expect(deleteMcpServer).toHaveBeenCalledWith(pendingMonzoServer.id),
    );
  });

});

function renderPanel({
  onSetToolGrants,
  toolGrants = [],
}: {
  onSetToolGrants?: (grants: ToolGrantUpdate[]) => Promise<void>;
  toolGrants?: ToolGrant[];
} = {}) {
  const handleSetToolGrants =
    onSetToolGrants ??
    vi.fn(async (): Promise<void> => undefined);
  return render(
    <McpSettingsPanel
      canEdit={true}
      onLog={vi.fn()}
      onSetToolGrants={handleSetToolGrants}
      toolGrants={toolGrants}
    />,
  );
}

function configuredServerCard(container: HTMLElement): HTMLElement {
  const card = container.querySelector("details.mcp-card");
  expect(card).not.toBeNull();
  return card as HTMLElement;
}

function configuredServerSummary(container: HTMLElement): HTMLElement {
  const summary = container.querySelector("summary.mcp-card-summary");
  expect(summary).not.toBeNull();
  return summary as HTMLElement;
}

function oauthPopupStub(): {
  close: ReturnType<typeof vi.fn>;
  popup: Window;
  replace: ReturnType<typeof vi.fn>;
} {
  const close = vi.fn();
  const replace = vi.fn();
  return {
    close,
    popup: {
      close,
      location: { replace },
      opener: window,
    } as unknown as Window,
    replace,
  };
}

const emptyPresetCatalog: McpPresetListResponse = {
  catalog_version: "2026-07-01",
  presets: [],
  researched_at: "2026-06-30",
};

const catalogWithExaAndSlack: McpPresetListResponse = {
  catalog_version: "2026-07-01",
  presets: [
    catalogPreset({
      category: "web_search_research",
      description: "Web search and page fetch.",
      id: "exa",
      label: "Exa",
    }),
    catalogPreset({
      category: "productivity_collaboration",
      description: "Team messages and channels.",
      id: "slack",
      label: "Slack",
    }),
  ],
  researched_at: "2026-06-30",
};

const catalogWithGoogle: McpPresetListResponse = {
  catalog_version: "2026-07-01",
  presets: [
    catalogPreset({
      authLabel: "OAuth client",
      authType: "oauth_authorization_code",
      category: "productivity_collaboration",
      description: "Google Workspace tools.",
      id: "google-workspace",
      label: "Google Workspace",
      requiresOAuthClient: true,
    }),
  ],
  researched_at: "2026-06-30",
};

const catalogWithPayPal: McpPresetListResponse = {
  catalog_version: "2026-07-22",
  presets: [
    catalogPreset({
      authLabel: "OAuth",
      authType: "oauth_authorization_code",
      category: "payments_commerce",
      description: "PayPal merchant tools.",
      id: "paypal",
      label: "PayPal",
    }),
  ],
  researched_at: "2026-07-22",
};

const flightradar24BasePreset = catalogPreset({
  category: "travel_lifestyle",
  description: "Live and historical flight information.",
  id: "flightradar24",
  label: "Flightradar24",
});

const catalogWithFlightradar24: McpPresetListResponse = {
  catalog_version: "2026-07-22.1",
  presets: [
    {
      ...flightradar24BasePreset,
      variants: [
        {
          ...flightradar24BasePreset.variants[0],
          id: "local-compose",
          label: "Local Compose",
          starter_allowed_tools: ["get_live_flights_count"],
          trusted_private_host: "flightradar24-mcp",
          url: "http://flightradar24-mcp:8000/mcp",
        },
        {
          ...flightradar24BasePreset.variants[0],
          compatibility: "custom_remote",
          id: "deployed-https",
          label: "Deployed HTTPS endpoint",
          trusted_private_host: null,
          url: null,
          url_placeholder:
            "https://your-flightradar24-mcp.example.com/mcp",
        },
      ],
    },
  ],
  researched_at: "2026-07-22",
};

const monzoBasePreset = catalogPreset({
  authLabel: "MCP endpoint bearer",
  authType: "static_bearer",
  category: "payments_commerce",
  description: "Personal Monzo account data.",
  id: "monzo",
  label: "Monzo",
});

const catalogWithMonzo: McpPresetListResponse = {
  catalog_version: "2026-07-23.1",
  presets: [
    {
      ...monzoBasePreset,
      variants: [
        {
          ...monzoBasePreset.variants[0],
          auth: {
            ...monzoBasePreset.variants[0].auth,
            description: "Store the separate MCP endpoint bearer.",
            provider_oauth_description:
              "VoxOS stores and refreshes the Monzo authorization.",
            provider_oauth_label: "Connect Monzo",
            requires_oauth_client: true,
            requires_provider_oauth: true,
            requires_secret: true,
          },
          id: "local-compose",
          label: "Local Compose",
          trusted_private_host: "monzo-mcp",
          url: "http://monzo-mcp:8000/mcp",
        },
      ],
    },
  ],
  researched_at: "2026-07-23",
};

function catalogPreset({
  authLabel = "No auth",
  authType = "none",
  category,
  description,
  id,
  label,
  requiresOAuthClient = false,
}: {
  authLabel?: McpServicePreset["variants"][number]["auth"]["label"];
  authType?: McpServicePreset["variants"][number]["auth"]["auth_type"];
  category: McpServicePreset["category"];
  description: string;
  id: string;
  label: string;
  requiresOAuthClient?: boolean;
}): McpServicePreset {
  return {
    catalog_version: "2026-07-01",
    category,
    cost_posture: "Free tier available.",
    description,
    docs_urls: [`https://${id}.example.com/docs`],
    free_available: true,
    id,
    label,
    logo: {
      domain: `${id}.example.com`,
      label,
      source: "domain",
    },
    researched_at: "2026-06-30",
    setup_steps: [`Set up ${label}.`],
    tool_guidance: [],
    variants: [
      {
        auth: {
          api_key_header_name: null,
          auth_type: authType,
          description:
            authType === "oauth_authorization_code"
              ? "Complete provider OAuth in the browser."
              : "Use without external authentication.",
          label: authLabel,
          oauth_scopes: [],
          oauth_token_endpoint_auth_method: null,
          provider_oauth_description: null,
          provider_oauth_label: null,
          requires_oauth_client: requiresOAuthClient,
          requires_provider_oauth: false,
          requires_secret: false,
        },
        compatibility: "direct_remote",
        id: "default",
        label: "Default",
        optional_auth: null,
        risky_tools: [],
        starter_allowed_tools: [],
        transport: "streamable_http",
        trusted_private_host: null,
        url: `https://${id}.example.com/mcp`,
        url_placeholder: null,
        warnings: [],
      },
    ],
    warnings: [],
  };
}

const exaServer: McpServer = {
  allowed_tools: ["web_fetch_exa", "web_search_exa"],
  auth: {
    authenticated: true,
    expires_at: null,
    needs_reauth: false,
    scopes: [],
  },
  auth_type: "none",
  created_at: "2026-07-02T07:00:00Z",
  discovered_tools: ["web_fetch_exa", "web_search_exa"],
  enabled: true,
  id: "mcp-server-1",
  label: "Exa 3",
  logo: {
    domain: "exa.ai",
    label: "Exa",
    source: "domain",
  },
  optional_auth: {
    api_key_header_name: "x-api-key", // pragma: allowlist secret
    auth_type: "api_key_header",
    description:
      "Optionally store an Exa API key for authenticated production limits.",
    label: "Exa API key",
    oauth_scopes: [],
    oauth_token_endpoint_auth_method: null,
    provider_oauth_description: null,
    provider_oauth_label: null,
    requires_oauth_client: false,
    requires_provider_oauth: false,
    requires_secret: true,
  },
  optional_auth_configured: false,
  provider_auth: null,
  non_admin_tools_enabled: false,
  safe_metadata: {
    catalog_version: "2026-07-01",
    category: "web_search_research",
    docs_urls: [],
    logo: {
      domain: "exa.ai",
      label: "Exa",
      source: "domain",
    },
    preset_id: "exa",
    preset_label: "Exa",
    preset_variant_id: "direct-http",
    researched_at: "2026-06-30",
    tool_definitions: [
      {
        description: "Read a webpage's full content as clean markdown.",
        guidance: {
          description: "Read webpages.",
          disabled_by_default: false,
          label: null,
          name: "web_fetch_exa",
          read_only: true,
          recommended: true,
          risk: "low",
        },
        input_schema: {},
        name: "web_fetch_exa",
      },
      {
        description: "Search the web for any topic.",
        guidance: {
          description: "Search the web.",
          disabled_by_default: false,
          label: null,
          name: "web_search_exa",
          read_only: true,
          recommended: true,
          risk: "low",
        },
        input_schema: {},
        name: "web_search_exa",
      },
    ],
  tool_guidance: [],
  platform_default: false,
  default_allowed_tools: [],
  warnings: [],
  },
  status: "connected",
  status_message: null,
  transport: "streamable_http",
  trust_state: "trusted",
  updated_at: "2026-07-02T07:00:00Z",
  url: "https://mcp.exa.ai/mcp",
};

const exaToolGrants: ToolGrant[] = exaServer.allowed_tools.map((toolName) => ({
  agent_id: "default-agent",
  id: `grant-${toolName}`,
  integration_id: null,
  mcp_server_id: exaServer.id,
  risk: "low",
  status: "allowed",
  tool_name: toolName,
  tool_source: "mcp_server",
  user_id: "user-1",
}));

const pendingPayPalServer: McpServer = {
  ...exaServer,
  allowed_tools: [],
  auth: {
    authenticated: false,
    expires_at: null,
    needs_reauth: true,
    scopes: [],
  },
  auth_type: "oauth_authorization_code",
  discovered_tools: [],
  id: "paypal-server-1",
  label: "PayPal",
  safe_metadata: {
    ...exaServer.safe_metadata,
    category: "payments_commerce",
    preset_id: "paypal",
    preset_label: "PayPal",
    preset_variant_id: "oauth",
    tool_definitions: [],
    tool_guidance: [],
  },
  status: "needs_auth",
  trust_state: "untrusted",
  url: "https://mcp.paypal.com/mcp",
};

const authenticatedPayPalServer: McpServer = {
  ...pendingPayPalServer,
  auth: {
    authenticated: true,
    expires_at: "2026-07-22T20:00:00Z",
    needs_reauth: false,
    scopes: [],
  },
  status: "untested",
};

const connectedPayPalServer: McpServer = {
  ...authenticatedPayPalServer,
  discovered_tools: ["list_invoices", "pay_order"],
  safe_metadata: {
    ...authenticatedPayPalServer.safe_metadata,
    tool_definitions: [
      {
        description: "List PayPal invoices.",
        guidance: {
          description: null,
          disabled_by_default: false,
          label: null,
          name: "list_invoices",
          read_only: true,
          recommended: true,
          risk: "low",
        },
        input_schema: {},
        name: "list_invoices",
      },
      {
        description: "Pay an authorized order.",
        guidance: {
          description: null,
          disabled_by_default: true,
          label: null,
          name: "pay_order",
          read_only: false,
          recommended: false,
          risk: "high",
        },
        input_schema: {},
        name: "pay_order",
      },
    ],
  },
  status: "connected",
  trust_state: "trusted",
};

const expiredGoogleServer: McpServer = {
  ...exaServer,
  allowed_tools: ["gmail.search_threads"],
  auth: {
    authenticated: true,
    expires_at: "2026-07-28T13:48:00Z",
    needs_reauth: true,
    scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
  },
  auth_type: "oauth_authorization_code",
  discovered_tools: ["gmail.search_threads"],
  id: "google-workspace-server-1",
  label: "Google Workspace",
  provider_auth: null,
  safe_metadata: {
    ...exaServer.safe_metadata,
    category: "productivity_collaboration",
    preset_id: "google-workspace",
    preset_label: "Google Workspace",
    preset_variant_id: "gmail",
    tool_definitions: [],
    tool_guidance: [],
  },
  status: "needs_auth",
  trust_state: "trusted",
  url: "https://gmailmcp.googleapis.com/mcp/v1",
};

const pendingMonzoServer: McpServer = {
  ...exaServer,
  allowed_tools: [],
  auth: {
    authenticated: false,
    expires_at: null,
    needs_reauth: true,
    scopes: [],
  },
  auth_type: "static_bearer",
  discovered_tools: [],
  id: "monzo-server-1",
  label: "Monzo",
  provider_auth: {
    authenticated: false,
    expires_at: null,
    needs_reauth: true,
    scopes: [],
  },
  safe_metadata: {
    ...exaServer.safe_metadata,
    category: "payments_commerce",
    preset_id: "monzo",
    preset_label: "Monzo",
    preset_variant_id: "local-compose",
    tool_definitions: [],
    tool_guidance: [],
  },
  status: "needs_auth",
  trust_state: "untrusted",
  url: "http://monzo-mcp:8000/mcp",
};

const authenticatedMonzoServer: McpServer = {
  ...pendingMonzoServer,
  auth: {
    authenticated: true,
    expires_at: null,
    needs_reauth: false,
    scopes: [],
  },
  status: "needs_auth",
};
