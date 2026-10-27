import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  ChevronRight,
  Copy,
  ExternalLink,
  KeyRound,
  Loader2,
  PlayCircle,
  RefreshCw,
  Save,
  Search,
  ShieldAlert,
  Trash2,
  Wrench,
} from "lucide-react";
import {
  createMcpServer,
  createMcpServerFromPreset,
  deleteMcpServer,
  loadMcpPresets,
  loadMcpServers,
  mcpOAuthRedirectUri,
  revokeMcpAuth,
  saveMcpStaticAuth,
  startMcpOAuth,
  testMcpServer,
  updateMcpServer,
  type CreateMcpServerFromPresetRequest,
  type CreateMcpServerRequest,
  type McpAuthType,
  type McpDiscoveredToolDefinition,
  type McpPresetCategory,
  type McpPresetCompatibility,
  type McpPresetToolGuide,
  type McpPresetToolRisk,
  type McpServer,
  type McpServicePreset,
  type McpServicePresetVariant,
  type McpTransportType,
  type ToolGrant,
  type ToolGrantUpdate,
  type ToolRisk,
} from "./api";
import { ServiceLogo } from "./components/ServiceLogo";

type LogLevel = "info" | "error";

type McpSettingsPanelProps = {
  canEdit: boolean;
  onLog: (message: string, level?: LogLevel) => void;
  onSetToolGrants: (grants: ToolGrantUpdate[]) => Promise<void>;
  toolGrants: ToolGrant[];
};

type AddServerForm = {
  label: string;
  url: string;
  transport: McpTransportType;
  authType: McpAuthType;
  enabled: boolean;
  allowedTools: string;
  apiKeyHeaderName: string;
  secret: string;
};

type PresetDraft = {
  presetId: string | null;
  variantId: string | null;
  label: string;
  remoteUrl: string;
  secret: string;
  oauthClientId: string;
  oauthClientSecret: string;
  enabled: boolean;
};

type CatalogFilter = McpPresetCategory | "all";

const initialForm: AddServerForm = {
  label: "",
  url: "",
  transport: "streamable_http",
  authType: "none",
  enabled: false,
  allowedTools: "",
  apiKeyHeaderName: "",
  secret: "",
};

const initialPresetDraft: PresetDraft = {
  presetId: null,
  variantId: null,
  label: "",
  remoteUrl: "",
  secret: "",
  oauthClientId: "",
  oauthClientSecret: "",
  enabled: true,
};

const authLabels: Record<McpAuthType, string> = {
  none: "No auth",
  static_bearer: "Bearer token",
  api_key_header: "API key header",
  oauth_authorization_code: "OAuth",
  oauth_client_credentials: "Client credentials",
  private_key_jwt: "Private key JWT",
};

const categoryLabels: Record<McpPresetCategory, string> = {
  mcp_gateways: "Gateways",
  travel_lifestyle: "Travel",
  payments_commerce: "Payments",
  productivity_collaboration: "Productivity",
  web_search_research: "Search",
  browser_automation: "Browser",
  scraping_actor_platforms: "Scraping",
  developer_operations: "DevOps",
  business_systems: "Business",
  knowledge_documents: "Knowledge",
  data_analytics: "Data",
  smart_home_environment: "Home",
};

const statusLabels: Record<McpServer["status"], string> = {
  disabled: "disabled",
  untested: "untested",
  needs_auth: "needs auth",
  connected: "connected",
  failed: "failed",
};

const secretQueryParamNames = new Set([
  "apikey",
  "api_key",
  "key",
  "secret",
  "token",
  "access_token",
  "auth",
]);

export function McpSettingsPanel({
  canEdit,
  onLog,
  onSetToolGrants,
  toolGrants,
}: McpSettingsPanelProps) {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [presets, setPresets] = useState<McpServicePreset[]>([]);
  const [form, setForm] = useState<AddServerForm>(initialForm);
  const [presetDraft, setPresetDraft] = useState<PresetDraft>(initialPresetDraft);
  const [catalogQuery, setCatalogQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<CatalogFilter>("all");
  const catalogQueryRef = useRef(catalogQuery);
  const categoryFilterRef = useRef<CatalogFilter>(categoryFilter);
  const [isLoading, setIsLoading] = useState(false);
  const [workingServerId, setWorkingServerId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [secretDrafts, setSecretDrafts] = useState<Record<string, string>>({});
  const [headerDrafts, setHeaderDrafts] = useState<Record<string, string>>({});
  const [allowedToolDrafts, setAllowedToolDrafts] = useState<Record<string, string>>({});
  const [oauthScopeDrafts, setOauthScopeDrafts] = useState<Record<string, string>>({});
  const [oauthClientIdDrafts, setOauthClientIdDrafts] = useState<Record<string, string>>({});
  const [oauthClientSecretDrafts, setOauthClientSecretDrafts] = useState<Record<string, string>>({});
  const [pendingOAuthServerIds, setPendingOAuthServerIds] = useState<string[]>([]);
  const [serverActionErrors, setServerActionErrors] = useState<Record<string, string>>({});

  const selectedPreset = useMemo(
    () => presets.find((preset) => preset.id === presetDraft.presetId) ?? null,
    [presetDraft.presetId, presets],
  );
  const selectedVariant = useMemo(
    () =>
      selectedPreset?.variants.find(
        (variant) => variant.id === presetDraft.variantId,
      ) ??
      selectedPreset?.variants[0] ??
      null,
    [presetDraft.variantId, selectedPreset],
  );

  const filteredPresets = useMemo(
    () => filterPresets(presets, catalogQuery, categoryFilter),
    [catalogQuery, categoryFilter, presets],
  );

  const syncPresetDraftForFilters = useCallback(
    (query: string, category: CatalogFilter) => {
      const matchingPresets = filterPresets(presets, query, category);
      setPresetDraft((current) => {
        if (matchingPresets.length === 0) {
          return current.presetId === null ? current : initialPresetDraft;
        }
        if (
          current.presetId !== null &&
          matchingPresets.some((preset) => preset.id === current.presetId)
        ) {
          return current;
        }
        return draftForPreset(matchingPresets[0], servers);
      });
    },
    [presets, servers],
  );

  const refreshSettings = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [loadedServers, catalog] = await Promise.all([
        loadMcpServers(),
        loadMcpPresets(),
      ]);
      setServers(loadedServers);
      setPresets(catalog.presets);
      setAllowedToolDrafts((current) => mergeToolDrafts(current, loadedServers));
      setPendingOAuthServerIds((current) =>
        current.filter((serverId) =>
          loadedServers.some(
            (server) =>
              server.id === serverId &&
              (!oauthConnectionAuthenticated(server) ||
                server.status !== "connected"),
          ),
        ),
      );
      const matchingPresets = filterPresets(
        catalog.presets,
        catalogQueryRef.current,
        categoryFilterRef.current,
      );
      setPresetDraft((current) => {
        if (matchingPresets.length === 0) {
          return current.presetId === null ? current : initialPresetDraft;
        }
        if (
          current.presetId !== null &&
          matchingPresets.some((preset) => preset.id === current.presetId)
        ) {
          return current;
        }
        return draftForPreset(matchingPresets[0], loadedServers);
      });
    } catch (loadError) {
      const message = errorMessage(loadError);
      setError(message);
      onLog(`MCP settings failed: ${message}`, "error");
    } finally {
      setIsLoading(false);
    }
  }, [onLog]);

  const finalizePendingOAuth = useCallback(async () => {
    if (pendingOAuthServerIds.length === 0) {
      return;
    }

    const pendingServerIds = new Set(pendingOAuthServerIds);
    setIsLoading(true);
    setError(null);
    try {
      const loadedServers = await loadMcpServers();
      const finalizedServerIds = new Set<string>();
      const resolvedServers: McpServer[] = [];
      let firstTestError: unknown = null;

      for (const server of loadedServers) {
        if (
          !pendingServerIds.has(server.id) ||
          !oauthConnectionAuthenticated(server)
        ) {
          resolvedServers.push(server);
          continue;
        }
        try {
          const testedServer = await testMcpServer(server.id);
          resolvedServers.push(testedServer);
          finalizedServerIds.add(server.id);
        } catch (testError) {
          resolvedServers.push(server);
          firstTestError ??= testError;
        }
      }

      setServers(resolvedServers);
      setAllowedToolDrafts((current) => {
        const next = mergeToolDrafts(current, resolvedServers);
        for (const server of resolvedServers) {
          if (finalizedServerIds.has(server.id)) {
            next[server.id] = preferredAllowedToolDraft(server);
          }
        }
        return next;
      });
      setPendingOAuthServerIds((current) =>
        current.filter((serverId) => {
          const server = resolvedServers.find((candidate) => candidate.id === serverId);
          return (
            server !== undefined &&
            (!oauthConnectionAuthenticated(server) ||
              server.status !== "connected")
          );
        }),
      );

      if (finalizedServerIds.size > 0) {
        onLog(
          "MCP OAuth completed and tools discovered. Review the suggested tools before granting access.",
        );
      }
      if (firstTestError !== null) {
        throw firstTestError;
      }
    } catch (finalizeError) {
      const message = errorMessage(finalizeError);
      setError(message);
      onLog(`MCP OAuth finalization failed: ${message}`, "error");
    } finally {
      setIsLoading(false);
    }
  }, [onLog, pendingOAuthServerIds]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void refreshSettings();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [refreshSettings]);

  useEffect(() => {
    if (pendingOAuthServerIds.length === 0) {
      return;
    }

    const handleFocus = () => {
      void finalizePendingOAuth();
    };

    window.addEventListener("focus", handleFocus);
    return () => window.removeEventListener("focus", handleFocus);
  }, [finalizePendingOAuth, pendingOAuthServerIds.length]);

  const runServerAction = async (
    serverId: string,
    action: () => Promise<McpServer | null>,
    successMessage: string,
    onUpdated?: (server: McpServer) => void,
  ) => {
    setWorkingServerId(serverId);
    setError(null);
    setServerActionErrors((current) => {
      const next = { ...current };
      delete next[serverId];
      return next;
    });
    try {
      const updatedServer = await action();
      if (updatedServer !== null) {
        replaceServer(updatedServer);
        onUpdated?.(updatedServer);
      }
      onLog(successMessage);
    } catch (actionError) {
      const message = errorMessage(actionError);
      setServerActionErrors((current) => ({
        ...current,
        [serverId]: message,
      }));
      onLog(`MCP action failed: ${message}`, "error");
    } finally {
      setWorkingServerId(null);
    }
  };

  const grantAllowedServerTools = async (
    server: McpServer,
    toolNames: string[] = server.allowed_tools,
  ) => {
    const grants = grantUpdatesForServer(server, toolNames);
    if (grants.length > 0) {
      await onSetToolGrants(grants);
    }
  };

  const testAndGrantServer = async (
    server: McpServer,
    requestedAllowedTools: string[] = server.allowed_tools,
  ): Promise<McpServer> => {
    let readyServer = server.enabled ? await testMcpServer(server.id) : server;
    const allowedTools =
      requestedAllowedTools.length > 0
        ? requestedAllowedTools
        : automaticAllowedTools(readyServer, "");
    if (
      readyServer.enabled &&
      readyServer.allowed_tools.join(",") !== allowedTools.join(",")
    ) {
      readyServer = await updateMcpServer(readyServer.id, {
        allowed_tools: allowedTools,
      });
    }
    await grantAllowedServerTools(readyServer, allowedTools);
    return readyServer;
  };

  const submitServer = async () => {
    const request: CreateMcpServerRequest = {
      label: form.label.trim(),
      url: form.url.trim(),
      transport: form.transport,
      auth_type: form.authType,
      enabled: form.enabled,
      allowed_tools: splitList(form.allowedTools),
      api_key_header_name:
        form.authType === "api_key_header" ? form.apiKeyHeaderName.trim() : null,
    };
    setWorkingServerId("new");
    setError(null);
    try {
      let createdServer = await createMcpServer(request);
      const secret = form.secret.trim();
      if (
        secret !== "" &&
        (form.authType === "static_bearer" || form.authType === "api_key_header")
      ) {
        createdServer = await saveMcpStaticAuth(createdServer.id, {
          secret,
          api_key_header_name:
            form.authType === "api_key_header" ? form.apiKeyHeaderName.trim() : null,
        });
      }
      if (createdServer.enabled && createdServer.auth.authenticated) {
        createdServer = await testAndGrantServer(createdServer);
      }
      setServers((current) => [createdServer, ...current]);
      setAllowedToolDrafts((current) => ({
        ...current,
        [createdServer.id]: createdServer.allowed_tools.join(", "),
      }));
      setForm(initialForm);
      onLog(
        createdServer.status === "connected"
          ? "MCP server saved, tested, and granted."
          : "MCP server saved.",
      );
    } catch (submitError) {
      const message = errorMessage(submitError);
      setError(message);
      onLog(`MCP setup failed: ${message}`, "error");
    } finally {
      setWorkingServerId(null);
    }
  };

  const submitPreset = async () => {
    if (selectedPreset === null || selectedVariant === null) {
      return;
    }
    const createRequest: CreateMcpServerFromPresetRequest = {
      label: presetDraft.label.trim() || null,
      variant_id: selectedVariant.id,
      enabled: presetDraft.enabled,
      allowed_tools: selectedVariant.starter_allowed_tools,
      url:
        selectedVariant.compatibility === "direct_remote"
          ? null
          : presetDraft.remoteUrl.trim(),
    };
    const workingId = `preset:${selectedPreset.id}`;
    setWorkingServerId(workingId);
    setError(null);
    try {
      let createdServer = await createMcpServerFromPreset(
        selectedPreset.id,
        createRequest,
      );
      const isGenericGatewayPreset = selectedPreset.id === "mcp-gateway";
      if (selectedVariant.auth.requires_secret) {
        createdServer = await saveMcpStaticAuth(createdServer.id, {
          secret: presetDraft.secret.trim(),
          api_key_header_name: selectedVariant.auth.api_key_header_name,
        });
      }
      if (
        selectedVariant.auth.auth_type === "oauth_authorization_code" ||
        selectedVariant.auth.requires_provider_oauth
      ) {
        const started = await startMcpOAuth(createdServer.id, {
          client_id: selectedVariant.auth.requires_oauth_client
            ? presetDraft.oauthClientId.trim()
            : null,
          client_secret: selectedVariant.auth.requires_oauth_client
            ? presetDraft.oauthClientSecret.trim()
            : null,
          redirect_uri: mcpOAuthRedirectUri(),
          scopes: selectedVariant.auth.oauth_scopes,
          token_endpoint_auth_method:
            selectedVariant.auth.oauth_token_endpoint_auth_method,
        });
        window.open(started.authorization_url, "_blank", "noopener");
        setPendingOAuthServerIds((current) =>
          current.includes(createdServer.id) ? current : [...current, createdServer.id],
        );
      } else if (isGenericGatewayPreset) {
        createdServer = createdServer.enabled
          ? await testMcpServer(createdServer.id)
          : createdServer;
      } else {
        createdServer = await testAndGrantServer(createdServer);
      }
      setServers((current) => [createdServer, ...current]);
      setAllowedToolDrafts((current) => ({
        ...current,
        [createdServer.id]: createdServer.allowed_tools.join(", "),
      }));
      setPresetDraft(draftForPreset(selectedPreset, [createdServer, ...servers]));
      let successMessage = "MCP server saved, tested, and granted.";
      if (
        selectedVariant.auth.auth_type === "oauth_authorization_code" ||
        selectedVariant.auth.requires_provider_oauth
      ) {
        successMessage = "MCP server saved and OAuth started. Return here after provider approval to finalize connection.";
      } else if (isGenericGatewayPreset) {
        successMessage = "MCP gateway saved and tested. Choose allowed tools after discovery.";
      }
      onLog(successMessage);
    } catch (submitError) {
      const message = errorMessage(submitError);
      setError(message);
      onLog(`MCP catalog setup failed: ${message}`, "error");
    } finally {
      setWorkingServerId(null);
    }
  };

  const replaceServer = (updatedServer: McpServer) => {
    setServers((current) =>
      current.map((server) =>
        server.id === updatedServer.id ? updatedServer : server,
      ),
    );
    setAllowedToolDrafts((current) => ({
      ...current,
      [updatedServer.id]:
        current[updatedServer.id] ?? updatedServer.allowed_tools.join(", "),
    }));
  };

  const removeServer = (serverId: string) => {
    setServers((current) => current.filter((server) => server.id !== serverId));
  };

  const manualSecretWarning = secretLikeUrlWarning(form.url);

  return (
    <section className="mcp-setup">
      <div className="section-heading-row">
        <div className="section-title">
          <p className="eyebrow">MCP</p>
          <h2>Your servers</h2>
        </div>
        <button
          aria-busy={isLoading}
          className="mcp-refresh-button"
          disabled={isLoading}
          onClick={() => void refreshSettings()}
          type="button"
        >
          {isLoading ? <Loader2 aria-hidden="true" size={16} /> : <RefreshCw aria-hidden="true" size={16} />}
          Refresh
        </button>
      </div>

      {error !== null ? <p className="error-text">{error}</p> : null}

      <div className="mcp-list">
        {servers.length > 0 ? (
          servers.map((server) => (
            <ConfiguredServerCard
              actionError={serverActionErrors[server.id] ?? null}
              allowedToolDraft={allowedToolDrafts[server.id] ?? ""}
              canEdit={canEdit}
              headerDraft={headerDrafts[server.id] ?? ""}
              key={server.id}
              onAllowedToolDraftChange={(value) =>
                setAllowedToolDrafts((current) => ({
                  ...current,
                  [server.id]: value,
                }))
              }
              isOAuthPending={pendingOAuthServerIds.includes(server.id)}
              oauthClientIdDraft={oauthClientIdDrafts[server.id] ?? ""}
              oauthClientSecretDraft={oauthClientSecretDrafts[server.id] ?? ""}
              onDelete={() => {
                if (!window.confirm(`Delete MCP server "${server.label}"?`)) {
                  return;
                }
                void runServerAction(
                  server.id,
                  async () => {
                    await deleteMcpServer(server.id);
                    removeServer(server.id);
                    return null;
                  },
                  "MCP server deleted.",
                );
              }}
              onHeaderDraftChange={(value) =>
                setHeaderDrafts((current) => ({
                  ...current,
                  [server.id]: value,
                }))
              }
              onOauthScopesChange={(value) =>
                setOauthScopeDrafts((current) => ({
                  ...current,
                  [server.id]: value,
                }))
              }
              onOauthClientIdChange={(value) =>
                setOauthClientIdDrafts((current) => ({
                  ...current,
                  [server.id]: value,
                }))
              }
              onOauthClientSecretChange={(value) =>
                setOauthClientSecretDrafts((current) => ({
                  ...current,
                  [server.id]: value,
                }))
              }
              onRevoke={() =>
                void runServerAction(
                  server.id,
                  async () => {
                    let updated = await revokeMcpAuth(server.id);
                    if (
                      server.optional_auth !== null &&
                      updated.enabled &&
                      updated.allowed_tools.length > 0
                    ) {
                      updated = await testAndGrantServer(updated);
                    }
                    return updated;
                  },
                  server.optional_auth === null
                    ? "MCP auth revoked."
                    : "Optional MCP auth removed.",
                )
              }
              onSaveSecret={() =>
                void runServerAction(
                  server.id,
                  async () => {
                    let updated = await saveMcpStaticAuth(server.id, {
                      secret: (secretDrafts[server.id] ?? "").trim(),
                      api_key_header_name:
                        server.auth_type === "api_key_header"
                          ? (headerDrafts[server.id] ?? "").trim() || null
                          : server.optional_auth?.auth_type === "api_key_header"
                            ? server.optional_auth.api_key_header_name
                            : null,
                    });
                    setSecretDrafts((current) => ({
                      ...current,
                      [server.id]: "",
                    }));
                    if (updated.enabled && updated.allowed_tools.length > 0) {
                      updated = await testAndGrantServer(updated);
                    }
                    return updated;
                  },
                  server.optional_auth === null
                    ? "MCP auth saved."
                    : "Optional MCP auth saved.",
                )
              }
              onSaveTools={() =>
                void runServerAction(
                  server.id,
                  async () => {
                    const allowedTools = automaticAllowedTools(
                      server,
                      allowedToolDrafts[server.id],
                    );
                    const updated = await updateMcpServer(server.id, {
                      allowed_tools: allowedTools,
                    });
                    if (!updated.enabled) {
                      return updated;
                    }
                    return await testAndGrantServer(updated, allowedTools);
                  },
                  "MCP tools saved.",
                )
              }
              onSecretDraftChange={(value) =>
                setSecretDrafts((current) => ({
                  ...current,
                  [server.id]: value,
                }))
              }
              onStartOAuth={() =>
                void runServerAction(
                  server.id,
                  async () => {
                    const clientId = (oauthClientIdDrafts[server.id] ?? "").trim();
                    const clientSecret = (
                      oauthClientSecretDrafts[server.id] ?? ""
                    ).trim();
                    const authorizationWindow = window.open("", "_blank");
                    if (authorizationWindow !== null) {
                      authorizationWindow.opener = null;
                    }
                    let started: Awaited<ReturnType<typeof startMcpOAuth>>;
                    try {
                      started = await startMcpOAuth(server.id, {
                        client_id: clientId || null,
                        client_secret: clientSecret || null,
                        redirect_uri: mcpOAuthRedirectUri(),
                        scopes: splitList(oauthScopeDrafts[server.id] ?? ""),
                      });
                    } catch (startError) {
                      authorizationWindow?.close();
                      throw startError;
                    }
                    if (authorizationWindow === null) {
                      throw new Error(
                        "OAuth sign-in could not open. Allow pop-ups for VoxOS and try again.",
                      );
                    }
                    setOauthClientIdDrafts((current) => ({
                      ...current,
                      [server.id]: "",
                    }));
                    setOauthClientSecretDrafts((current) => ({
                      ...current,
                      [server.id]: "",
                    }));
                    authorizationWindow.location.replace(
                      started.authorization_url,
                    );
                    setPendingOAuthServerIds((current) =>
                      current.includes(server.id) ? current : [...current, server.id],
                    );
                    return null;
                  },
                  "MCP OAuth started. Return here after provider approval to finalize connection.",
                )
              }
              onTest={() =>
                void runServerAction(
                  server.id,
                  () => testMcpServer(server.id),
                  "MCP server tested.",
                  (updatedServer) =>
                    setAllowedToolDrafts((current) => ({
                      ...current,
                      [updatedServer.id]: preferredAllowedToolDraft(updatedServer),
                    })),
                )
              }
              onToggleEnabled={() =>
                void runServerAction(
                  server.id,
                  async () => {
                    if (server.enabled) {
                      return await updateMcpServer(server.id, {
                        enabled: false,
                      });
                    }
                    const allowedTools = automaticAllowedTools(
                      server,
                      allowedToolDrafts[server.id],
                    );
                    const updated = await updateMcpServer(server.id, {
                      enabled: true,
                      allowed_tools: allowedTools,
                    });
                    return await testAndGrantServer(updated, allowedTools);
                  },
                  server.enabled
                    ? "MCP server disabled."
                    : "MCP server enabled, tested, and granted.",
                )
              }
              onToggleNonAdminTools={() =>
                void runServerAction(
                  server.id,
                  () =>
                    updateMcpServer(server.id, {
                      non_admin_tools_enabled: !server.non_admin_tools_enabled,
                    }),
                  server.non_admin_tools_enabled
                    ? "MCP tools are no longer available to non-admin callers."
                    : "MCP tools are available to non-admin callers.",
                )
              }
              oauthScopeDraft={oauthScopeDrafts[server.id] ?? ""}
              secretDraft={secretDrafts[server.id] ?? ""}
              server={server}
              toolGrants={toolGrants}
              workingServerId={workingServerId}
            />
          ))
        ) : (
          <p className="muted">
            No servers connected yet. Pick a service from the catalog below to get
            started.
          </p>
        )}
      </div>

      <div className="section-title">
        <p className="eyebrow">Catalog</p>
        <h2>Add a server</h2>
      </div>

      <div className="mcp-layout">
        <section className="mcp-catalog-panel">
          <div className="mcp-panel-heading">
            <h3>Common MCPs</h3>
            <span>
              {filteredPresets.length === presets.length
                ? `${presets.length} services`
                : `${filteredPresets.length}/${presets.length} services`}
            </span>
          </div>

          <div className="mcp-search-row">
            <Search aria-hidden="true" size={16} />
            <input
              aria-label="Search MCP catalog"
              disabled={isLoading}
              onChange={(event) => {
                const nextQuery = event.currentTarget.value;
                catalogQueryRef.current = nextQuery;
                setCatalogQuery(nextQuery);
                syncPresetDraftForFilters(nextQuery, categoryFilter);
              }}
              placeholder="Search catalog"
              value={catalogQuery}
            />
          </div>

          <div className="mcp-category-tabs">
            <button
              aria-pressed={categoryFilter === "all"}
              className={categoryFilter === "all" ? "active" : ""}
              onClick={() => {
                categoryFilterRef.current = "all";
                setCategoryFilter("all");
                syncPresetDraftForFilters(catalogQuery, "all");
              }}
              type="button"
            >
              All
            </button>
            {Object.entries(categoryLabels).map(([category, label]) => (
              <button
                aria-pressed={categoryFilter === category}
                className={categoryFilter === category ? "active" : ""}
                key={category}
                onClick={() => {
                  const nextCategory = category as McpPresetCategory;
                  categoryFilterRef.current = nextCategory;
                  setCategoryFilter(nextCategory);
                  syncPresetDraftForFilters(catalogQuery, nextCategory);
                }}
                type="button"
              >
                {label}
              </button>
            ))}
          </div>

          <div className="mcp-preset-list">
            {filteredPresets.length > 0 ? (
              filteredPresets.map((preset) => (
                <button
                  aria-pressed={presetDraft.presetId === preset.id}
                  className={
                    presetDraft.presetId === preset.id
                      ? "mcp-preset-card active"
                      : "mcp-preset-card"
                  }
                  key={preset.id}
                  onClick={() => setPresetDraft(draftForPreset(preset, servers))}
                  type="button"
                >
                  <span className="mcp-preset-card-main">
                    <ServiceLogo
                      decorative
                      label={preset.label}
                      logo={preset.logo}
                      size="sm"
                    />
                    <span>
                      <strong>{preset.label}</strong>
                      <small>{categoryLabels[preset.category]}</small>
                    </span>
                  </span>
                  <small>{preset.free_available ? "Free tier" : "Account required"}</small>
                </button>
              ))
            ) : (
              <div className="mcp-empty-state">
                <strong>No matching MCPs</strong>
                <span>Try another service name, provider, tool, or category.</span>
              </div>
            )}
          </div>
        </section>

        <section className="mcp-preset-setup-panel">
          {selectedPreset !== null && selectedVariant !== null ? (
            <PresetSetup
              canEdit={canEdit}
              draft={presetDraft}
              isWorking={workingServerId?.startsWith("preset:") ?? false}
              onChange={setPresetDraft}
              onSubmit={() => void submitPreset()}
              preset={selectedPreset}
              variant={selectedVariant}
            />
          ) : (
            <div className="mcp-empty-state">
              <strong>No service selected</strong>
              <span>Search or choose a catalog result to see setup details.</span>
            </div>
          )}
        </section>
      </div>

      <details className="mcp-add-details">
        <summary>Custom server</summary>
        <form
          className="settings-form"
          onSubmit={(event: FormEvent<HTMLFormElement>) => {
            event.preventDefault();
            if (canEdit && canSubmit(form)) {
              void submitServer();
            }
          }}
        >
          <label className="field">
            <span>Label</span>
            <input
              disabled={!canEdit || workingServerId !== null}
              onChange={(event) =>
                setForm({ ...form, label: event.currentTarget.value })
              }
              placeholder="GitHub"
              value={form.label}
            />
          </label>
          <label className="field">
            <span>URL</span>
            <input
              disabled={!canEdit || workingServerId !== null}
              onChange={(event) => setForm({ ...form, url: event.currentTarget.value })}
              placeholder="https://mcp.example.com/mcp"
              value={form.url}
            />
          </label>
          {manualSecretWarning !== null ? (
            <p className="mcp-warning-text">
              <ShieldAlert aria-hidden="true" size={14} />
              {manualSecretWarning}
            </p>
          ) : null}
          <div className="two-col">
            <label className="field">
              <span>Transport</span>
              <select
                disabled={!canEdit || workingServerId !== null}
                onChange={(event) =>
                  setForm({
                    ...form,
                    transport: event.currentTarget.value as McpTransportType,
                  })
                }
                value={form.transport}
              >
                <option value="streamable_http">HTTP</option>
                <option value="sse">SSE</option>
              </select>
            </label>
            <label className="field">
              <span>Auth</span>
              <select
                disabled={!canEdit || workingServerId !== null}
                onChange={(event) =>
                  setForm({
                    ...form,
                    authType: event.currentTarget.value as McpAuthType,
                  })
                }
                value={form.authType}
              >
                {Object.entries(authLabels).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {form.authType === "api_key_header" ? (
            <label className="field">
              <span>Header</span>
              <input
                disabled={!canEdit || workingServerId !== null}
                onChange={(event) =>
                  setForm({ ...form, apiKeyHeaderName: event.currentTarget.value })
                }
                placeholder="PRIVATE-TOKEN"
                value={form.apiKeyHeaderName}
              />
            </label>
          ) : null}

          {form.authType === "static_bearer" || form.authType === "api_key_header" ? (
            <label className="field">
              <span>Secret</span>
              <input
                disabled={!canEdit || workingServerId !== null}
                onChange={(event) =>
                  setForm({ ...form, secret: event.currentTarget.value })
                }
                placeholder="Token"
                type="password"
                value={form.secret}
              />
            </label>
          ) : null}

          <label className="field">
            <span>Allowed tools</span>
            <input
              disabled={!canEdit || workingServerId !== null}
              onChange={(event) =>
                setForm({ ...form, allowedTools: event.currentTarget.value })
              }
              placeholder="issues.list, merge_requests.list"
              value={form.allowedTools}
            />
          </label>

          <label className="check-row">
            <input
              checked={form.enabled}
              disabled={!canEdit || workingServerId !== null}
              onChange={(event) =>
                setForm({ ...form, enabled: event.currentTarget.checked })
              }
              type="checkbox"
            />
            <span>Enabled</span>
          </label>

          <button
            className="primary"
            disabled={!canEdit || workingServerId !== null || !canSubmit(form)}
            type="submit"
          >
            {workingServerId === "new" ? "Saving" : "Add server"}
          </button>
        </form>
      </details>
    </section>
  );
}

function PresetSetup({
  canEdit,
  draft,
  isWorking,
  onChange,
  onSubmit,
  preset,
  variant,
}: {
  canEdit: boolean;
  draft: PresetDraft;
  isWorking: boolean;
  onChange: (draft: PresetDraft) => void;
  onSubmit: () => void;
  preset: McpServicePreset;
  variant: McpServicePresetVariant;
}) {
  const [copiedRedirectUri, setCopiedRedirectUri] = useState<string | null>(null);
  const needsRemoteUrl = variant.compatibility !== "direct_remote";
  const disabledReason = presetSubmitDisabledReason(draft, preset, variant);
  const canSubmit = disabledReason === null;
  const isOAuth =
    variant.auth.auth_type === "oauth_authorization_code" ||
    variant.auth.requires_provider_oauth;
  const gatewayWarning = variant.compatibility === "gateway_required"
    ? "Use a public HTTPS MCP gateway. Local stdio and localhost URLs are rejected."
    : null;
  const urlLabel =
    variant.compatibility === "gateway_required" ? "Gateway URL" : "Server URL";
  const urlPlaceholder =
    variant.url_placeholder ?? "https://mcp-gateway.example.com/mcp";
  const oauthRedirectUri = variant.auth.requires_oauth_client
    ? mcpOAuthRedirectUri()
    : null;
  const hasCopiedRedirectUri =
    oauthRedirectUri !== null && copiedRedirectUri === oauthRedirectUri;
  const submitLabel = isWorking
    ? isOAuth
      ? "Starting sign-in"
      : "Adding server"
    : presetSubmitLabel(preset, variant);

  return (
    <div className="mcp-preset-setup">
      <div className="mcp-panel-heading">
        <div className="mcp-heading-with-logo">
          <ServiceLogo label={preset.label} logo={preset.logo} size="md" />
          <div>
            <p className="eyebrow">{categoryLabels[preset.category]}</p>
            <h3>{preset.label}</h3>
          </div>
        </div>
        <span>{preset.free_available ? "Free tier" : "Account required"}</span>
      </div>

      <p>{preset.description}</p>
      <div className="mcp-meta">
        <span>{variant.transport === "sse" ? "SSE" : "HTTP"}</span>
        <span>{compatibilityLabel(variant.compatibility)}</span>
        <span>{variant.auth.label}</span>
      </div>

      <div className="mcp-auth-callout">
        <strong>{isOAuth ? "Provider sign-in" : "Authentication"}</strong>
        <span>{variant.auth.description}</span>
        {variant.auth.provider_oauth_description !== null ? (
          <span>{variant.auth.provider_oauth_description}</span>
        ) : null}
        {isOAuth ? (
          <span>
            VoxOS stores the returned token in the encrypted user vault. After
            approving access, return here to test and grant tools.
          </span>
        ) : null}
      </div>

      <div className="mcp-setup-steps">
        {preset.setup_steps.map((step) => (
          <p key={step}>{step}</p>
        ))}
      </div>

      <label className="field">
        <span>Variant</span>
        <select
          disabled={!canEdit || isWorking}
          onChange={(event) => {
            const nextVariantId = event.currentTarget.value;
            const nextVariant =
              preset.variants.find((candidate) => candidate.id === nextVariantId) ??
              preset.variants[0];
            onChange({
              ...draft,
              variantId: nextVariant.id,
              remoteUrl: "",
              secret: "",
              oauthClientId: "",
              oauthClientSecret: "",
            });
          }}
          value={variant.id}
        >
          {preset.variants.map((candidate) => (
            <option key={candidate.id} value={candidate.id}>
              {candidate.label}
            </option>
          ))}
        </select>
      </label>

      <label className="field">
        <span>Label</span>
        <input
          disabled={!canEdit || isWorking}
          onChange={(event) =>
            onChange({ ...draft, label: event.currentTarget.value })
          }
          placeholder={preset.label}
          value={draft.label}
        />
      </label>

      {needsRemoteUrl ? (
        <label className="field">
          <span>{urlLabel}</span>
          <input
            disabled={!canEdit || isWorking}
            onChange={(event) =>
              onChange({ ...draft, remoteUrl: event.currentTarget.value })
            }
            placeholder={urlPlaceholder}
            value={draft.remoteUrl}
          />
        </label>
      ) : variant.url !== null ? (
        <label className="field">
          <span>Endpoint</span>
          <input readOnly value={variant.url} />
        </label>
      ) : null}
      {gatewayWarning !== null ? (
        <p className="mcp-warning-text">
          <ShieldAlert aria-hidden="true" size={14} />
          {gatewayWarning}
        </p>
      ) : null}

      {variant.auth.requires_secret ? (
        <label className="field">
          <span>
            {variant.auth.requires_provider_oauth
              ? "MCP endpoint token"
              : variant.auth.auth_type === "api_key_header"
                ? "API key"
                : "Token"}
          </span>
          <input
            disabled={!canEdit || isWorking}
            onChange={(event) =>
              onChange({ ...draft, secret: event.currentTarget.value })
            }
            placeholder={
              variant.auth.api_key_header_name !== null
                ? variant.auth.api_key_header_name
                : "Bearer token"
            }
            type="password"
            value={draft.secret}
          />
        </label>
      ) : null}

      {variant.auth.requires_oauth_client ? (
        <>
          <label className="field">
            <span>OAuth redirect URI</span>
            <span className="mcp-copy-field">
              <input readOnly value={oauthRedirectUri ?? ""} />
              <button
                disabled={oauthRedirectUri === null}
                onClick={() => {
                  if (oauthRedirectUri === null) {
                    return;
                  }
                  void copyTextToClipboard(oauthRedirectUri).then(() =>
                    setCopiedRedirectUri(oauthRedirectUri),
                  );
                }}
                type="button"
              >
                <Copy aria-hidden="true" size={14} />
                {hasCopiedRedirectUri ? "Copied" : "Copy"}
              </button>
            </span>
          </label>
          <label className="field">
            <span>OAuth client ID</span>
            <input
              disabled={!canEdit || isWorking}
              onChange={(event) =>
                onChange({ ...draft, oauthClientId: event.currentTarget.value })
              }
              placeholder="Client ID"
              value={draft.oauthClientId}
            />
          </label>
          <label className="field">
            <span>OAuth client secret</span>
            <input
              disabled={!canEdit || isWorking}
              onChange={(event) =>
                onChange({
                  ...draft,
                  oauthClientSecret: event.currentTarget.value,
                })
              }
              placeholder="Client secret"
              type="password"
              value={draft.oauthClientSecret}
            />
          </label>
        </>
      ) : null}

      <label className="check-row">
        <input
          checked={draft.enabled}
          disabled={!canEdit || isWorking}
          onChange={(event) =>
            onChange({ ...draft, enabled: event.currentTarget.checked })
          }
          type="checkbox"
        />
        <span>Enabled after setup</span>
      </label>

      <div className="mcp-doc-links">
        {preset.docs_urls.map((url) => (
          <a href={url} key={url} rel="noreferrer" target="_blank">
            <ExternalLink aria-hidden="true" size={14} />
            {docsLinkLabel(url)}
          </a>
        ))}
      </div>

      {combinedWarnings(preset, variant).map((warning) => (
        <p className="mcp-warning-text" key={warning}>
          <ShieldAlert aria-hidden="true" size={14} />
          {warning}
        </p>
      ))}

      <button
        className="primary"
        disabled={!canEdit || isWorking || !canSubmit}
        onClick={onSubmit}
        type="button"
      >
        {isWorking ? (
          <Loader2 aria-hidden="true" size={16} />
        ) : isOAuth ? (
          <ExternalLink aria-hidden="true" size={16} />
        ) : (
          <Wrench aria-hidden="true" size={16} />
        )}
        {submitLabel}
      </button>
      {disabledReason !== null ? (
        <p className="mcp-helper-text">{disabledReason}</p>
      ) : null}
    </div>
  );
}

type McpManagePanel = "tools" | "auth" | "advanced";

const panelLabels: Record<McpManagePanel, string> = {
  advanced: "Advanced",
  auth: "Auth",
  tools: "Tools",
};

function ConfiguredServerCard({
  actionError,
  allowedToolDraft,
  canEdit,
  headerDraft,
  isOAuthPending,
  oauthClientIdDraft,
  oauthClientSecretDraft,
  oauthScopeDraft,
  onAllowedToolDraftChange,
  onDelete,
  onHeaderDraftChange,
  onOauthClientIdChange,
  onOauthClientSecretChange,
  onOauthScopesChange,
  onRevoke,
  onSaveSecret,
  onSaveTools,
  onSecretDraftChange,
  onStartOAuth,
  onTest,
  onToggleEnabled,
  onToggleNonAdminTools,
  secretDraft,
  server,
  toolGrants,
  workingServerId,
}: {
  actionError: string | null;
  allowedToolDraft: string;
  canEdit: boolean;
  headerDraft: string;
  isOAuthPending: boolean;
  oauthClientIdDraft: string;
  oauthClientSecretDraft: string;
  oauthScopeDraft: string;
  onAllowedToolDraftChange: (value: string) => void;
  onDelete: () => void;
  onHeaderDraftChange: (value: string) => void;
  onOauthClientIdChange: (value: string) => void;
  onOauthClientSecretChange: (value: string) => void;
  onOauthScopesChange: (value: string) => void;
  onRevoke: () => void;
  onSaveSecret: () => void;
  onSaveTools: () => void;
  onSecretDraftChange: (value: string) => void;
  onStartOAuth: () => void;
  onTest: () => void;
  onToggleEnabled: () => void;
  onToggleNonAdminTools: () => void;
  secretDraft: string;
  server: McpServer;
  toolGrants: ToolGrant[];
  workingServerId: string | null;
}) {
  const isWorking = workingServerId !== null;
  const [isExpanded, setIsExpanded] = useState(false);
  const [activePanel, setActivePanel] = useState<McpManagePanel>("tools");
  const configuredFromPreset = server.safe_metadata.preset_label;
  const allowedTools = splitList(allowedToolDraft || server.allowed_tools.join(", "));
  const serverLogo = server.logo ?? server.safe_metadata.logo;
  const grantedAllowedCount = server.allowed_tools.filter((tool) =>
    isToolAllowed(toolGrants, "mcp_server", server.id, tool),
  ).length;
  const discoveredCount = server.discovered_tools.length;

  return (
    <details
      className="mcp-card"
      onToggle={(event) => setIsExpanded(event.currentTarget.open)}
      open={isExpanded}
    >
      <summary className="mcp-card-summary">
        <ChevronRight
          aria-hidden="true"
          className="mcp-card-chevron"
          size={16}
        />
        <span className="mcp-card-summary-main">
          <span className="mcp-card-header">
            <span className="mcp-card-title-row">
              <ServiceLogo label={server.label} logo={serverLogo} size="md" />
              <span>
                <strong className="mcp-card-title">{server.label}</strong>
                <span className="mcp-card-url">{server.url}</span>
              </span>
            </span>
            <span className={`status-chip status-${server.status}`}>
              {statusLabels[server.status]}
            </span>
          </span>

          <span className="mcp-meta">
            <span>{server.transport === "sse" ? "SSE" : "HTTP"}</span>
            <span>{server.enabled ? "enabled" : "disabled"}</span>
            <span>{credentialSummaryLabel(server)}</span>
            <span>{allowedTools.length}/{discoveredCount || allowedTools.length} tools allowed</span>
            <span>{grantedAllowedCount} granted</span>
            {configuredFromPreset !== null ? <span>{configuredFromPreset}</span> : null}
          </span>
        </span>
      </summary>

      <div className="mcp-card-content">
        <div className="mcp-card-tabs" role="group" aria-label={`Manage ${server.label}`}>
          {(["tools", "auth", "advanced"] as const).map((panel) => (
            <button
              aria-pressed={activePanel === panel}
              className={activePanel === panel ? "active" : ""}
              key={panel}
              onClick={() => setActivePanel(panel)}
              type="button"
            >
              {panelLabels[panel]}
            </button>
          ))}
        </div>

        {actionError !== null ? (
          <p className="error-text" role="alert">
            {actionError}
          </p>
        ) : null}

        {activePanel === "tools" ? (
          server.discovered_tools.length === 0 ? (
            <McpToolDiscoveryEmptyState
              canEdit={canEdit && !isWorking}
              onTest={onTest}
              status={server.status}
            />
          ) : (
            <McpToolAccessList
              allowedTools={allowedTools}
              canEdit={canEdit && !isWorking}
              onChange={(tools) => onAllowedToolDraftChange(tools.join(", "))}
              onSaveTools={onSaveTools}
              onTest={onTest}
              server={server}
              toolGrants={toolGrants}
            />
          )
        ) : null}

        {activePanel === "auth" ? (
          <McpAuthPanel
            canEdit={canEdit && !isWorking}
            headerDraft={headerDraft}
            isOAuthPending={isOAuthPending}
            oauthClientIdDraft={oauthClientIdDraft}
            oauthClientSecretDraft={oauthClientSecretDraft}
            oauthScopeDraft={oauthScopeDraft}
            onDelete={onDelete}
            onHeaderDraftChange={onHeaderDraftChange}
            onOauthClientIdChange={onOauthClientIdChange}
            onOauthClientSecretChange={onOauthClientSecretChange}
            onOauthScopesChange={onOauthScopesChange}
            onRevoke={onRevoke}
            onSaveSecret={onSaveSecret}
            onSecretDraftChange={onSecretDraftChange}
            onStartOAuth={onStartOAuth}
            onTest={onTest}
            secretDraft={secretDraft}
            server={server}
          />
        ) : null}

        {activePanel === "advanced" ? (
          <McpAdvancedPanel
            allowedToolDraft={allowedToolDraft}
            canEdit={canEdit && !isWorking}
            onAllowedToolDraftChange={onAllowedToolDraftChange}
            onDelete={onDelete}
            onSaveTools={onSaveTools}
            onTest={onTest}
            onToggleEnabled={onToggleEnabled}
            onToggleNonAdminTools={onToggleNonAdminTools}
            server={server}
          />
        ) : null}
      </div>
    </details>
  );
}

function McpToolDiscoveryEmptyState({
  canEdit,
  onTest,
  status,
}: {
  canEdit: boolean;
  onTest: () => void;
  status: McpServer["status"];
}) {
  return (
    <div className="mcp-discovery-empty">
      <div>
        <strong>No tools discovered</strong>
        <span>
          Run a test to connect to the MCP server and list its available tools.
          Nothing is allowed by default.
        </span>
      </div>
      <button disabled={!canEdit} onClick={onTest} type="button">
        <PlayCircle aria-hidden="true" size={15} />
        {status === "connected" ? "Retest" : "Test to discover"}
      </button>
    </div>
  );
}

function McpToolAccessList({
  allowedTools,
  canEdit,
  onChange,
  onSaveTools,
  onTest,
  server,
  toolGrants,
}: {
  allowedTools: string[];
  canEdit: boolean;
  onChange: (tools: string[]) => void;
  onSaveTools: () => void;
  onTest: () => void;
  server: McpServer;
  toolGrants: ToolGrant[];
}) {
  const rows = toolRowsForServer(server);
  if (rows.length === 0) {
    return null;
  }
  const selectedTools = new Set(allowedTools);
  const grantedAllowedCount = server.allowed_tools.filter((tool) =>
    isToolAllowed(toolGrants, "mcp_server", server.id, tool),
  ).length;

  return (
    <section className="mcp-tool-section">
      <div className="mcp-tool-section-header">
        <div>
          <strong>Tools and agent access</strong>
          <span>
            Choose which discovered tools this server may expose. Saved tools are
            granted to active agents after testing.
          </span>
        </div>
        <small>
          {selectedTools.size} selected · {grantedAllowedCount} granted
        </small>
      </div>
      <div className="mcp-tool-selection">
        {rows.map((row) => {
          const selected = selectedTools.has(row.name);
          const granted = isToolAllowed(
            toolGrants,
            "mcp_server",
            server.id,
            row.name,
          );
          const serverAllowed = server.allowed_tools.includes(row.name);
          return (
            <label className="tool-approval-row compact mcp-tool-access-row" key={row.name}>
              <input
                checked={selected}
                disabled={!canEdit}
                onChange={(event) => {
                  const next = new Set(selectedTools);
                  if (event.currentTarget.checked) {
                    next.add(row.name);
                  } else {
                    next.delete(row.name);
                  }
                  onChange(server.discovered_tools.filter((tool) => next.has(tool)));
                }}
                type="checkbox"
              />
              <span>
                <strong>{row.name}</strong>
                <small>
                  {row.description}
                  {row.guidance !== null ? ` · ${toolGuideLabel(row.guidance)}` : ""}
                </small>
              </span>
              <span className="mcp-tool-row-chips">
                <RiskChip risk={row.guidance?.risk ?? "medium"} />
                <small className={granted ? "access-chip granted" : "access-chip"}>
                  {accessLabel(serverAllowed, granted)}
                </small>
              </span>
            </label>
          );
        })}
      </div>
      <div className="mcp-actions">
        <button disabled={!canEdit} onClick={onSaveTools} type="button">
          <Save aria-hidden="true" size={15} />
          Save tools
        </button>
        <button disabled={!canEdit} onClick={onTest} type="button">
          <PlayCircle aria-hidden="true" size={15} />
          Test and grant
        </button>
      </div>
    </section>
  );
}

function McpAuthPanel({
  canEdit,
  headerDraft,
  isOAuthPending,
  oauthClientIdDraft,
  oauthClientSecretDraft,
  oauthScopeDraft,
  onDelete,
  onHeaderDraftChange,
  onOauthClientIdChange,
  onOauthClientSecretChange,
  onOauthScopesChange,
  onRevoke,
  onSaveSecret,
  onSecretDraftChange,
  onStartOAuth,
  onTest,
  secretDraft,
  server,
}: {
  canEdit: boolean;
  headerDraft: string;
  isOAuthPending: boolean;
  oauthClientIdDraft: string;
  oauthClientSecretDraft: string;
  oauthScopeDraft: string;
  onDelete: () => void;
  onHeaderDraftChange: (value: string) => void;
  onOauthClientIdChange: (value: string) => void;
  onOauthClientSecretChange: (value: string) => void;
  onOauthScopesChange: (value: string) => void;
  onRevoke: () => void;
  onSaveSecret: () => void;
  onSecretDraftChange: (value: string) => void;
  onStartOAuth: () => void;
  onTest: () => void;
  secretDraft: string;
  server: McpServer;
}) {
  const optionalAuth =
    server.auth_type === "none" ? server.optional_auth : null;
  const isOAuth =
    server.auth_type === "oauth_authorization_code" ||
    server.provider_auth !== null;
  const staticAuthType = optionalAuth?.auth_type ?? server.auth_type;
  const usesStaticSecret =
    staticAuthType === "static_bearer" || staticAuthType === "api_key_header";
  const credentialConfigured =
    optionalAuth === null
      ? server.auth.authenticated
      : server.optional_auth_configured;
  const canRevoke =
    optionalAuth === null
      ? server.auth_type !== "none" &&
        (server.auth.authenticated ||
          server.provider_auth?.authenticated === true)
      : server.optional_auth_configured;
  const oauthClientError = oauthClientCredentialError(
    server.safe_metadata.preset_id,
    oauthClientIdDraft,
    oauthClientSecretDraft,
  );
  const replacingOAuthClient =
    oauthClientIdDraft.trim() !== "" &&
    oauthClientSecretDraft.trim() !== "";

  return (
    <section className="mcp-tool-section">
      <div className="mcp-tool-section-header">
        <div>
          <strong>Authentication</strong>
          <span>{authPanelDescription(server)}</span>
        </div>
        <small>{credentialSummaryLabel(server)}</small>
      </div>

      {isOAuthPending ? (
        <div className="mcp-auth-callout">
          <strong>Waiting for provider approval</strong>
          <span>
            Finish sign-in in the provider tab, return here, then finalize the
            connection. VoxOS stores the returned token in the encrypted vault.
          </span>
        </div>
      ) : null}

      {server.auth_type === "none" ? (
        <div className="mcp-auth-callout">
          <strong>
            {optionalAuth === null
              ? "No external credential required"
              : "Authentication is optional"}
          </strong>
          <span>
            {optionalAuth === null
              ? "This server can be tested and granted without provider login."
              : "Anonymous access remains available. Add a provider credential for authenticated limits and production use."}
          </span>
        </div>
      ) : null}

      {usesStaticSecret ? (
        <>
          {staticAuthType === "api_key_header" ? (
            <label className="field">
              <span>Header</span>
              <input
                disabled={!canEdit || optionalAuth !== null}
                onChange={(event) => onHeaderDraftChange(event.currentTarget.value)}
                placeholder="PRIVATE-TOKEN"
                value={optionalAuth?.api_key_header_name ?? headerDraft}
              />
            </label>
          ) : null}

          <div className="mini-form">
            <input
              disabled={!canEdit}
              onChange={(event) => onSecretDraftChange(event.currentTarget.value)}
              placeholder={
                credentialConfigured
                  ? "Replace stored secret"
                  : optionalAuth === null
                    ? "Secret"
                    : "Optional API key"
              }
              type="password"
              value={secretDraft}
            />
            <button
              disabled={!canEdit || secretDraft.trim() === ""}
              onClick={onSaveSecret}
              type="button"
            >
              <KeyRound aria-hidden="true" size={15} />
              {optionalAuth === null ? "Save secret" : "Save API key"}
            </button>
          </div>
        </>
      ) : null}

      {isOAuth ? (
        <>
          <div className="mcp-auth-callout">
            <strong>Provider OAuth client</strong>
            <span>
              Enter both fields and choose Save client &amp; start sign-in to
              replace the encrypted credentials. Leave both blank to reconnect
              using the stored client.
            </span>
          </div>
          <label className="field">
            <span>OAuth client ID</span>
            <input
              autoComplete="off"
              disabled={!canEdit}
              onChange={(event) =>
                onOauthClientIdChange(event.currentTarget.value)
              }
              placeholder="Leave blank to reuse stored client"
              value={oauthClientIdDraft}
            />
          </label>
          <label className="field">
            <span>OAuth client secret</span>
            <input
              autoComplete="new-password"
              disabled={!canEdit}
              onChange={(event) =>
                onOauthClientSecretChange(event.currentTarget.value)
              }
              placeholder="Leave blank to reuse stored secret"
              type="password"
              value={oauthClientSecretDraft}
            />
          </label>
          {oauthClientError !== null ? (
            <p className="error-text">{oauthClientError}</p>
          ) : null}
          <label className="field">
            <span>OAuth scopes</span>
            <input
              disabled={!canEdit}
              onChange={(event) => onOauthScopesChange(event.currentTarget.value)}
              placeholder="Use provider defaults"
              value={oauthScopeDraft}
            />
            <small>Optional. Leave blank to use the preset/provider scopes.</small>
          </label>
          <div className="mcp-actions">
            <button
              disabled={!canEdit || oauthClientError !== null}
              onClick={onStartOAuth}
              type="button"
            >
              <ExternalLink aria-hidden="true" size={15} />
              {replacingOAuthClient
                ? "Save client & start sign-in"
                : oauthConnectionAuthenticated(server)
                  ? "Reconnect using stored client"
                  : "Start sign-in using stored client"}
            </button>
            <button disabled={!canEdit} onClick={onTest} type="button">
              <PlayCircle aria-hidden="true" size={15} />
              Finalize connection
            </button>
          </div>
        </>
      ) : null}

      {canRevoke ? (
        <div className="mcp-actions">
          <button disabled={!canEdit} onClick={onRevoke} type="button">
            {optionalAuth === null
              ? "Revoke stored auth"
              : "Remove optional API key"}
          </button>
        </div>
      ) : null}

      <div className="mcp-actions">
        <button
          aria-label={`Delete ${server.label} server`}
          className="danger"
          disabled={!canEdit}
          onClick={onDelete}
          type="button"
        >
          <Trash2 aria-hidden="true" size={15} />
          Delete server
        </button>
      </div>
    </section>
  );
}

function McpAdvancedPanel({
  allowedToolDraft,
  canEdit,
  onAllowedToolDraftChange,
  onDelete,
  onSaveTools,
  onTest,
  onToggleEnabled,
  onToggleNonAdminTools,
  server,
}: {
  allowedToolDraft: string;
  canEdit: boolean;
  onAllowedToolDraftChange: (value: string) => void;
  onDelete: () => void;
  onSaveTools: () => void;
  onTest: () => void;
  onToggleEnabled: () => void;
  onToggleNonAdminTools: () => void;
  server: McpServer;
}) {
  return (
    <section className="mcp-tool-section">
      <div className="mcp-tool-section-header">
        <div>
          <strong>Advanced server controls</strong>
          <span>Use these for manual allowlists, retesting, and removal.</span>
        </div>
      </div>

      <label className="field">
        <span>Allowed tools</span>
        <input
          disabled={!canEdit}
          onChange={(event) => onAllowedToolDraftChange(event.currentTarget.value)}
          placeholder="tool.one, tool.two"
          value={allowedToolDraft || server.allowed_tools.join(", ")}
        />
        <small>
          Empty means no MCP tools are allowed. Prefer the Tools panel unless you
          need to paste exact tool names.
        </small>
      </label>

      <label className="check-row">
        <input
          aria-label="Allow selected tools for non-admin callers"
          checked={server.non_admin_tools_enabled}
          disabled={!canEdit}
          onChange={onToggleNonAdminTools}
          type="checkbox"
        />
        <span>Allow selected tools for non-admin callers</span>
        <small>
          Applies to inbound phone calls, SMS, and WhatsApp. The server must still be
          connected and trusted, and each tool still needs an agent grant.
        </small>
      </label>

      <div className="mcp-actions">
        <button disabled={!canEdit} onClick={onToggleEnabled} type="button">
          <CheckCircle2 aria-hidden="true" size={15} />
          {server.enabled ? "Disable" : "Enable"}
        </button>
        <button disabled={!canEdit} onClick={onSaveTools} type="button">
          <Save aria-hidden="true" size={15} />
          Save tools
        </button>
        <button disabled={!canEdit} onClick={onTest} type="button">
          <PlayCircle aria-hidden="true" size={15} />
          Test
        </button>
        <button
          className="danger"
          disabled={!canEdit}
          onClick={onDelete}
          type="button"
        >
          <Trash2 aria-hidden="true" size={15} />
          Delete
        </button>
      </div>
    </section>
  );
}

function RiskChip({ risk }: { risk: McpPresetToolRisk }) {
  return <small className={`risk-chip risk-${risk}`}>{risk}</small>;
}

function isToolAllowed(
  grants: ToolGrant[],
  toolSource: ToolGrant["tool_source"],
  serverId: string | null,
  toolName: string,
): boolean {
  return grants.some(
    (grant) =>
      grant.tool_source === toolSource &&
      grant.mcp_server_id === serverId &&
      grant.tool_name === toolName &&
      grant.status === "allowed",
  );
}

function canSubmit(form: AddServerForm): boolean {
  if (form.label.trim() === "" || form.url.trim() === "") {
    return false;
  }
  if (form.authType === "api_key_header" && form.apiKeyHeaderName.trim() === "") {
    return false;
  }
  return true;
}

function presetSubmitDisabledReason(
  draft: PresetDraft,
  preset: McpServicePreset,
  variant: McpServicePresetVariant,
): string | null {
  if (
    variant.compatibility !== "direct_remote" &&
    draft.remoteUrl.trim() === ""
  ) {
    return "Enter the public HTTPS server URL to continue.";
  }
  if (variant.auth.requires_secret && draft.secret.trim() === "") {
    return variant.auth.auth_type === "api_key_header"
      ? "Enter the provider API key to continue."
      : "Enter the bearer token to continue.";
  }
  if (
    variant.auth.requires_oauth_client &&
    (draft.oauthClientId.trim() === "" || draft.oauthClientSecret.trim() === "")
  ) {
    return "Enter the OAuth client ID and secret to start provider sign-in.";
  }
  if (variant.auth.requires_oauth_client) {
    return oauthClientCredentialError(
      preset.id,
      draft.oauthClientId,
      draft.oauthClientSecret,
    );
  }
  return null;
}

function oauthClientCredentialError(
  presetId: string | null,
  clientIdDraft: string,
  clientSecretDraft: string,
): string | null {
  const clientId = clientIdDraft.trim();
  const clientSecret = clientSecretDraft.trim();
  if ((clientId === "") !== (clientSecret === "")) {
    return "Enter both OAuth client fields, or leave both blank to reuse the stored client.";
  }
  if (presetId !== "monzo" || clientId === "") {
    return null;
  }
  if (clientId.startsWith("user_")) {
    return "Use the Monzo OAuth client ID, not the API Playground user ID.";
  }
  if (clientSecret.startsWith("eyJ") && clientSecret.split(".").length === 3) {
    return "Use the Monzo OAuth client secret, not an API Playground access token.";
  }
  return null;
}

function presetSubmitLabel(
  preset: McpServicePreset,
  variant: McpServicePresetVariant,
): string {
  if (
    variant.auth.auth_type === "oauth_authorization_code" ||
    variant.auth.requires_provider_oauth
  ) {
    return `Connect ${preset.label}`;
  }
  return `Add ${preset.label}`;
}

async function copyTextToClipboard(value: string): Promise<void> {
  await navigator.clipboard.writeText(value);
}

function splitList(value: string): string[] {
  return value
    .split(/[,\n]/u)
    .map((entry) => entry.trim())
    .filter((entry, index, entries) => entry !== "" && entries.indexOf(entry) === index);
}

function mergeToolDrafts(
  current: Record<string, string>,
  servers: McpServer[],
): Record<string, string> {
  const next = { ...current };
  for (const server of servers) {
    next[server.id] = current[server.id] ?? server.allowed_tools.join(", ");
  }
  return next;
}

function draftForPreset(
  preset: McpServicePreset,
  servers: McpServer[],
): PresetDraft {
  return {
    presetId: preset.id,
    variantId: preset.variants[0]?.id ?? null,
    label: uniquePresetLabel(preset.label, servers),
    remoteUrl: "",
    secret: "",
    oauthClientId: "",
    oauthClientSecret: "",
    enabled: true,
  };
}

function uniquePresetLabel(baseLabel: string, servers: McpServer[]): string {
  const existingLabels = new Set(
    servers.map((server) => server.label.trim().toLowerCase()),
  );
  if (!existingLabels.has(baseLabel.trim().toLowerCase())) {
    return baseLabel;
  }
  for (let index = 2; index < 100; index += 1) {
    const candidate = `${baseLabel} ${index}`;
    if (!existingLabels.has(candidate.toLowerCase())) {
      return candidate;
    }
  }
  return `${baseLabel} ${Date.now()}`;
}

function filterPresets(
  presets: McpServicePreset[],
  query: string,
  category: CatalogFilter,
): McpServicePreset[] {
  const normalizedQuery = query.trim().toLowerCase();
  return presets.filter((preset) => {
    const matchesCategory = category === "all" || preset.category === category;
    const searchableText = presetSearchText(preset);
    const matchesQuery =
      normalizedQuery === "" || searchableText.includes(normalizedQuery);
    return matchesCategory && matchesQuery;
  });
}

function presetSearchText(preset: McpServicePreset): string {
  const values = [
    preset.label,
    preset.description,
    preset.cost_posture,
    categoryLabels[preset.category],
    ...preset.setup_steps,
    ...preset.docs_urls.map(docsLinkLabel),
    ...preset.warnings,
    ...preset.variants.flatMap((variant) => [
      variant.label,
      compatibilityLabel(variant.compatibility),
      variant.auth.label,
      variant.auth.description,
      ...variant.auth.oauth_scopes,
      ...variant.warnings,
    ]),
    ...preset.tool_guidance.flatMap((tool) => [
      tool.name,
      tool.label ?? "",
      tool.description ?? "",
      tool.risk,
      tool.recommended ? "recommended" : "",
      tool.read_only ? "read only readonly" : "",
    ]),
  ];
  return values.join(" ").toLowerCase();
}

function compatibilityLabel(compatibility: McpPresetCompatibility): string {
  if (compatibility === "direct_remote") {
    return "direct remote";
  }
  if (compatibility === "custom_remote") {
    return "custom remote";
  }
  return "gateway required";
}

function combinedWarnings(
  preset: McpServicePreset,
  variant: McpServicePresetVariant,
): string[] {
  return [...preset.warnings, ...variant.warnings].filter(
    (warning, index, warnings) => warning !== "" && warnings.indexOf(warning) === index,
  );
}

function docsLinkLabel(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "Docs";
  }
}

function secretLikeUrlWarning(value: string): string | null {
  const trimmedValue = value.trim();
  if (trimmedValue === "") {
    return null;
  }
  let url: URL;
  try {
    url = new URL(trimmedValue);
  } catch {
    return null;
  }
  for (const key of url.searchParams.keys()) {
    if (secretQueryParamNames.has(key.toLowerCase())) {
      return "This URL appears to contain a secret-like query parameter. Prefer header auth so VoxOS can encrypt the credential.";
    }
  }
  return null;
}

function automaticAllowedTools(
  server: McpServer,
  draftValue: string | undefined,
): string[] {
  if (draftValue !== undefined) {
    const draftTools = splitList(draftValue);
    if (draftTools.length > 0) {
      return draftTools;
    }
  }
  if (server.allowed_tools.length > 0) {
    return server.allowed_tools;
  }
  const preferredTools = preferredAllowedToolNames(server);
  if (preferredTools.length > 0) {
    return preferredTools;
  }
  return server.discovered_tools;
}

function toolRowsForServer(server: McpServer): McpDiscoveredToolDefinition[] {
  const definitionsByName = new Map(
    server.safe_metadata.tool_definitions.map((definition) => [
      definition.name,
      definition,
    ]),
  );
  return server.discovered_tools.map((toolName) => {
    const definition = definitionsByName.get(toolName);
    if (definition !== undefined) {
      return definition;
    }
    return {
      name: toolName,
      description: toolName,
      input_schema: {},
      guidance: guidanceForTool(server.safe_metadata.tool_guidance, toolName),
    };
  });
}

function guidanceForTool(
  guidance: McpPresetToolGuide[],
  toolName: string,
): McpPresetToolGuide | null {
  return guidance.find((item) => item.name === toolName) ?? null;
}

function preferredAllowedToolDraft(server: McpServer): string {
  const tools =
    server.allowed_tools.length > 0
      ? server.allowed_tools
      : preferredAllowedToolNames(server);
  return tools.join(", ");
}

function preferredAllowedToolNames(server: McpServer): string[] {
  return toolRowsForServer(server)
    .filter(
      (row) =>
        row.guidance?.recommended === true &&
        row.guidance.disabled_by_default === false &&
        row.guidance.risk !== "high",
    )
    .map((row) => row.name);
}

function toolGuideLabel(guidance: McpPresetToolGuide): string {
  const parts: string[] = [];
  if (guidance.recommended) {
    parts.push("recommended");
  }
  if (guidance.read_only) {
    parts.push("read-only");
  }
  if (guidance.disabled_by_default) {
    parts.push("disabled by default");
  }
  return parts.join(", ") || guidance.risk;
}

function toolRiskForGrant(risk: McpPresetToolRisk): ToolRisk {
  if (risk === "low") {
    return "low";
  }
  if (risk === "high") {
    return "high";
  }
  return "medium";
}

function grantUpdatesForServer(
  server: McpServer,
  toolNames: string[],
): ToolGrantUpdate[] {
  const allowedToolNames = new Set(server.allowed_tools);
  const rowsByName = new Map(
    toolRowsForServer(server).map((row) => [row.name, row]),
  );
  return toolNames
    .filter((toolName) => allowedToolNames.has(toolName))
    .map((toolName) => {
      const row = rowsByName.get(toolName);
      return {
        mcp_server_id: server.id,
        risk: toolRiskForGrant(row?.guidance?.risk ?? "medium"),
        status: "allowed",
        tool_name: toolName,
        tool_source: "mcp_server",
      };
    });
}

function credentialSummaryLabel(server: McpServer): string {
  if (server.auth_type === "none") {
    if (server.optional_auth === null) {
      return "no auth";
    }
    return server.optional_auth_configured
      ? "optional auth saved"
      : "API key optional";
  }
  if (server.provider_auth !== null) {
    if (!server.auth.authenticated) {
      return "endpoint auth needed";
    }
    if (server.provider_auth.authenticated) {
      return "endpoint and provider auth saved";
    }
    return "provider auth needed";
  }
  if (server.auth.authenticated) {
    return "auth saved";
  }
  if (server.auth.needs_reauth) {
    return "reauth needed";
  }
  return "auth needed";
}

function authPanelDescription(server: McpServer): string {
  if (server.auth_type === "none") {
    return server.optional_auth === null
      ? "This MCP server does not require an external account or stored credential."
      : "This MCP server works anonymously and can use an optional stored API key.";
  }
  if (server.provider_auth !== null) {
    return "Store the MCP endpoint bearer separately, then connect the provider. VoxOS keeps and refreshes the provider tokens.";
  }
  if (server.auth_type === "oauth_authorization_code") {
    return "Sign in with the external provider, then return to VoxOS to test and grant tools.";
  }
  if (server.auth_type === "api_key_header") {
    return "Store the provider API key in the encrypted user vault and send it as the configured header.";
  }
  if (server.auth_type === "static_bearer") {
    return "Store the bearer token in the encrypted user vault.";
  }
  return "Store the required provider credential in the encrypted user vault.";
}

function oauthConnectionAuthenticated(server: McpServer): boolean {
  return server.provider_auth?.authenticated ?? server.auth.authenticated;
}

function accessLabel(serverAllowed: boolean, granted: boolean): string {
  if (!serverAllowed) {
    return "not allowed";
  }
  return granted ? "granted" : "will grant";
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
