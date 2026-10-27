import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  ExternalLink,
  GlobeLock,
  KeyRound,
  Pencil,
  RefreshCw,
  ShieldAlert,
  Trash2,
  UserCheck,
} from "lucide-react";
import {
  cancelBrowserAccountSetup,
  completeBrowserAccountSetup,
  createBrowserAccount,
  deleteBrowserAccount,
  loadAgents,
  loadBrowserAccounts,
  loadMcpServers,
  reauthenticateBrowserAccount,
  revokeBrowserAccount,
  setBrowserAccountAgents,
  startBrowserAccountSetup,
  updateBrowserAccount,
  updateBrowserAccountCredentials,
  type Agent,
  type BrowserAccount,
  type McpServer,
} from "../api";
import type { LogEntry } from "../appTypes";
import { authErrorMessage } from "../appUtils";

type BrowserAccountsPageProps = {
  canEdit: boolean;
  onLog: (message: string, level?: LogEntry["level"]) => void;
};

type BrowserAccountForm = {
  label: string;
  primaryOrigin: string;
  authenticationOrigins: string;
  browserCountryCode: string;
  providerConnectionId: string;
  agentIds: string[];
  username: string;
  password: string;
};

type CredentialDraft = { username: string; password: string };

const LOCAL_BROWSER_PROVIDER = "stagehand_local";

type BrowserAccountDetailsDraft = {
  label: string;
  primaryOrigin: string;
  authenticationOrigins: string;
  browserCountryCode: string;
};

const EMPTY_FORM: BrowserAccountForm = {
  label: "",
  primaryOrigin: "",
  authenticationOrigins: "",
  browserCountryCode: "GB",
  providerConnectionId: LOCAL_BROWSER_PROVIDER,
  agentIds: [],
  username: "",
  password: "",
};

export function BrowserAccountsPage({
  canEdit,
  onLog,
}: BrowserAccountsPageProps) {
  const [accounts, setAccounts] = useState<BrowserAccount[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [connections, setConnections] = useState<McpServer[]>([]);
  const [form, setForm] = useState<BrowserAccountForm>(EMPTY_FORM);
  const [isLoading, setIsLoading] = useState(true);
  const [busyAccountId, setBusyAccountId] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [credentialDrafts, setCredentialDrafts] = useState<
    Record<string, CredentialDraft>
  >({});
  const [editingAccountId, setEditingAccountId] = useState<string | null>(null);
  const [detailsDraft, setDetailsDraft] =
    useState<BrowserAccountDetailsDraft | null>(null);

  const connectedBrowserConnections = useMemo(
    () =>
      connections.filter(isConnectedBrowserConnection),
    [connections],
  );
  const browserConnections = useMemo(
    () => connectedBrowserConnections.filter(isReadyBrowserConnection),
    [connectedBrowserConnections],
  );
  const browserConnectionsMissingTools = useMemo(
    () =>
      connectedBrowserConnections.filter(
        (connection) => !isReadyBrowserConnection(connection),
      ),
    [connectedBrowserConnections],
  );
  const selectedConnection = browserConnections.find(
    (connection) => connection.id === form.providerConnectionId,
  );
  const usesLocalBrowser = form.providerConnectionId === LOCAL_BROWSER_PROVIDER;
  const usesStoredCredentials =
    usesLocalBrowser || selectedConnection?.safe_metadata.preset_id === "browserless";
  const supportsBrowserCountry =
    selectedConnection !== undefined && !usesStoredCredentials;

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [loadedAccounts, loadedAgents, loadedConnections] =
        await Promise.all([
          loadBrowserAccounts(),
          loadAgents(),
          loadMcpServers(),
        ]);
      setAccounts(sortAccounts(loadedAccounts));
      setAgents(loadedAgents.filter((agent) => agent.status === "active"));
      setConnections(loadedConnections);
      setForm((current) => {
        if (current.providerConnectionId === LOCAL_BROWSER_PROVIDER) {
          return current;
        }
        const selectedConnectionStillReady = loadedConnections.some(
          (connection) =>
            connection.id === current.providerConnectionId &&
            isReadyBrowserConnection(connection),
        );
        if (selectedConnectionStillReady) {
          return current;
        }
        return {
          ...current,
          password: "",
          providerConnectionId: LOCAL_BROWSER_PROVIDER,
          username: "",
        };
      });
    } catch (loadError) {
      const message = authErrorMessage(loadError);
      setError(message);
      onLog(`Browser Account load failed: ${message}`, "error");
    } finally {
      setIsLoading(false);
    }
  }, [onLog]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void refresh();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [refresh]);

  const createAccount = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      !canEdit ||
      isCreating ||
      form.label.trim() === "" ||
      form.primaryOrigin.trim() === "" ||
      (supportsBrowserCountry &&
        form.browserCountryCode.trim().length !== 2) ||
      (!usesLocalBrowser && selectedConnection === undefined) ||
      (usesStoredCredentials && (form.username === "" || form.password === ""))
    ) {
      return;
    }
    setIsCreating(true);
    setError(null);
    try {
      const account = await createBrowserAccount({
        label: form.label.trim(),
        primary_origin: form.primaryOrigin.trim(),
        authentication_origins: parseOrigins(form.authenticationOrigins),
        browser_country_code: usesStoredCredentials
          ? "GB"
          : form.browserCountryCode.trim().toUpperCase(),
        provider: usesLocalBrowser
          ? "stagehand_local"
          : selectedConnection?.safe_metadata.preset_id === "browserless"
            ? "browserless"
            : "browser_use_cloud",
        ...(usesLocalBrowser
          ? {}
          : { provider_connection_id: form.providerConnectionId }),
        agent_ids: form.agentIds,
        ...(usesStoredCredentials
          ? { username: form.username, password: form.password }
          : {}),
      });
      setAccounts((current) => sortAccounts([...current, account]));
      setForm((current) => ({
        ...EMPTY_FORM,
        providerConnectionId: current.providerConnectionId,
      }));
      onLog(
        usesStoredCredentials
          ? "Browser Account created with encrypted credentials."
          : "Browser Account created. Start setup to sign in.",
      );
    } catch (createError) {
      const message = authErrorMessage(createError);
      setError(message);
      onLog(`Browser Account creation failed: ${message}`, "error");
    } finally {
      setIsCreating(false);
    }
  };

  const openSetup = async (account: BrowserAccount, reauthenticate: boolean) => {
    if (!canEdit || busyAccountId !== null) {
      return;
    }
    setBusyAccountId(account.id);
    setError(null);
    const setupWindow = window.open("about:blank", "_blank");
    if (setupWindow !== null) {
      setupWindow.opener = null;
    }
    let setupStarted = false;
    try {
      const setup = reauthenticate
        ? await reauthenticateBrowserAccount(account.id)
        : await startBrowserAccountSetup(account.id);
      setupStarted = true;
      setAccounts((current) => upsertAccount(current, setup.account));
      if (setupWindow === null) {
        const fallbackWindow = window.open(
          setup.live_url,
          "_blank",
          "noopener,noreferrer",
        );
        if (fallbackWindow === null) {
          throw new Error("Allow pop-ups, then start secure sign-in again.");
        }
      } else {
        setupWindow.location.replace(setup.live_url);
      }
      onLog(
        `Opened a private Browser Use setup session for ${account.primary_origin}.`,
      );
    } catch (setupError) {
      setupWindow?.close();
      if (setupStarted) {
        try {
          const cancelled = await cancelBrowserAccountSetup(account.id);
          setAccounts((current) => upsertAccount(current, cancelled));
        } catch {
          // The server reconciles an unreachable setup session after its lease expires.
        }
      }
      const message = authErrorMessage(setupError);
      setError(message);
      onLog(`Browser Account setup failed: ${message}`, "error");
    } finally {
      setBusyAccountId(null);
    }
  };

  const completeSetup = async (account: BrowserAccount) => {
    await updateAccountOperation(account, async () => {
      const updated = await completeBrowserAccountSetup(account.id);
      onLog(`${account.label} is ready for approved browser tasks.`);
      return updated;
    });
  };

  const cancelSetup = async (account: BrowserAccount) => {
    await updateAccountOperation(account, () =>
      cancelBrowserAccountSetup(account.id),
    );
  };

  const toggleAgent = async (account: BrowserAccount, agentId: string) => {
    const agentIds = account.agent_ids.includes(agentId)
      ? account.agent_ids.filter((id) => id !== agentId)
      : [...account.agent_ids, agentId];
    await updateAccountOperation(account, () =>
      setBrowserAccountAgents(account.id, agentIds),
    );
  };

  const saveCredentials = async (account: BrowserAccount) => {
    const draft = credentialDrafts[account.id];
    if (draft === undefined || draft.username === "" || draft.password === "") {
      return;
    }
    await updateAccountOperation(account, async () => {
      const updated = await updateBrowserAccountCredentials(
        account.id,
        draft.username,
        draft.password,
      );
      setCredentialDrafts((current) => {
        const next = { ...current };
        delete next[account.id];
        return next;
      });
      onLog(`${account.label} credentials updated.`);
      return updated;
    });
  };

  const editAccountDetails = (account: BrowserAccount) => {
    if (!canEdit || busyAccountId !== null) {
      return;
    }
    setEditingAccountId(account.id);
    setDetailsDraft({
      authenticationOrigins: account.authentication_origins.join("\n"),
      browserCountryCode: account.browser_country_code,
      label: account.label,
      primaryOrigin: account.primary_origin,
    });
  };

  const saveAccountDetails = async (
    event: FormEvent<HTMLFormElement>,
    account: BrowserAccount,
  ) => {
    event.preventDefault();
    if (
      detailsDraft === null ||
      detailsDraft.label.trim() === "" ||
      detailsDraft.primaryOrigin.trim() === "" ||
      (account.provider === "browser_use_cloud" &&
        detailsDraft.browserCountryCode.trim().length !== 2)
    ) {
      return;
    }
    await updateAccountOperation(account, async () => {
      const updated = await updateBrowserAccount(account.id, {
        authentication_origins: parseOrigins(
          detailsDraft.authenticationOrigins,
        ),
        browser_country_code:
          account.provider === "browser_use_cloud"
            ? detailsDraft.browserCountryCode.trim().toUpperCase()
            : account.browser_country_code,
        label: detailsDraft.label.trim(),
        primary_origin: detailsDraft.primaryOrigin.trim(),
      });
      setEditingAccountId(null);
      setDetailsDraft(null);
      onLog(`${updated.label} details updated.`);
      return updated;
    });
  };

  const revokeAccount = async (account: BrowserAccount) => {
    if (
      !window.confirm(
        account.authentication_mode === "stored_credentials"
          ? `Revoke ${account.label}? Its stored website credentials will be deleted.`
          : `Revoke ${account.label}? Its remote signed-in profile will be deleted.`,
      )
    ) {
      return;
    }
    await updateAccountOperation(account, async () => {
      const updated = await revokeBrowserAccount(account.id);
      onLog(`${account.label} revoked.`);
      return updated;
    });
  };

  const removeAccount = async (account: BrowserAccount) => {
    if (
      !canEdit ||
      busyAccountId !== null ||
      !window.confirm(
        account.authentication_mode === "stored_credentials"
          ? `Delete ${account.label}? Voxos will revoke it and delete its stored credentials.`
          : `Delete ${account.label}? Voxos will revoke it and delete the remote profile.`,
      )
    ) {
      return;
    }
    setBusyAccountId(account.id);
    setError(null);
    try {
      await deleteBrowserAccount(account.id);
      setAccounts((current) => current.filter((item) => item.id !== account.id));
      onLog(`${account.label} deleted.`);
    } catch (deleteError) {
      const message = authErrorMessage(deleteError);
      setError(message);
      onLog(`Browser Account deletion failed: ${message}`, "error");
    } finally {
      setBusyAccountId(null);
    }
  };

  const updateAccountOperation = async (
    account: BrowserAccount,
    operation: () => Promise<BrowserAccount>,
  ) => {
    if (!canEdit || busyAccountId !== null) {
      return;
    }
    setBusyAccountId(account.id);
    setError(null);
    try {
      const updated = await operation();
      setAccounts((current) => upsertAccount(current, updated));
    } catch (operationError) {
      const message = authErrorMessage(operationError);
      setError(message);
      onLog(`Browser Account update failed: ${message}`, "error");
    } finally {
      setBusyAccountId(null);
    }
  };

  return (
    <div className="settings-page browser-accounts-page">
      <section className="settings-panel browser-account-intro">
        <div className="section-heading-row">
          <div className="section-title">
            <p className="eyebrow">Browser Accounts</p>
            <h2>Reusable signed-in browser profiles</h2>
          </div>
          <button disabled={isLoading} onClick={() => void refresh()} type="button">
            <RefreshCw aria-hidden="true" size={16} />
            Refresh
          </button>
        </div>
        <p className="muted">
          Local Browser Accounts use an isolated Stagehand and Chromium service and
          keep the website username and password encrypted in Voxos. They work in
          chat, voice, and explicitly linked scheduled tasks. Hosted Browserless and
          Browser Use connections remain available as alternatives.
        </p>
        <div className="warning-banner">
          <ShieldAlert aria-hidden="true" size={18} />
          <span>
            The selected browser provider can observe browser content and credentials
            used during login. Use a least-privilege website account and revoke it
            immediately if you suspect compromise.
          </span>
        </div>
      </section>

      <section className="settings-panel">
        <div className="section-title">
          <p className="eyebrow">New profile</p>
          <h2>Create Browser Account</h2>
        </div>
        {browserConnectionsMissingTools.length > 0 ? (
          <p className="error-text">
            Browser Accounts requires each provider&apos;s execution tool. Enable{" "}
            {browserConnectionsMissingTools
              .map(
                (connection) =>
                  `${connection.label} (${requiredBrowserExecutionTool(connection)})`,
              )
              .join(", ")} under allowed tools on the <a href="/mcp">MCP page</a>,
            then return and refresh.
          </p>
        ) : null}
        <form className="settings-form" onSubmit={createAccount}>
          <label className="field">
            <span>Account label</span>
            <input
              disabled={!canEdit || isCreating}
              onChange={(event) =>
                setForm({ ...form, label: event.currentTarget.value })
              }
              placeholder="Work GitHub"
              value={form.label}
            />
          </label>
          <label className="field">
            <span>Exact website origin</span>
            <input
              disabled={!canEdit || isCreating}
              onChange={(event) =>
                setForm({ ...form, primaryOrigin: event.currentTarget.value })
              }
              placeholder="https://github.com"
              type="url"
              value={form.primaryOrigin}
            />
          </label>
          <label className="field">
            <span>Login and SSO origins</span>
            <textarea
              aria-label="Login and SSO origins"
              disabled={!canEdit || isCreating}
              onChange={(event) =>
                setForm({
                  ...form,
                  authenticationOrigins: event.currentTarget.value,
                })
              }
              placeholder={"https://login.example.com\nhttps://id.example.com"}
              value={form.authenticationOrigins}
            />
            <small className="muted">
              Add every exact origin used during sign-in, one per line. Leave
              blank only when sign-in stays on the website origin.
            </small>
          </label>
          {supportsBrowserCountry ? (
            <label className="field">
              <span>Browser country</span>
              <input
                autoCapitalize="characters"
                disabled={!canEdit || isCreating}
                maxLength={2}
                minLength={2}
                onChange={(event) =>
                  setForm({
                    ...form,
                    browserCountryCode: event.currentTarget.value.toUpperCase(),
                  })
                }
                pattern="[A-Za-z]{2}"
                placeholder="GB"
                required
                value={form.browserCountryCode}
              />
              <small className="muted">
                Two-letter proxy country used by Browser Use Cloud. Use GB for
                UK-only sites.
              </small>
            </label>
          ) : null}
          <label className="field">
            <span>Browser runtime</span>
            <select
              disabled={!canEdit || isCreating}
              onChange={(event) =>
                setForm({
                  ...form,
                  password: "",
                  providerConnectionId: event.currentTarget.value,
                  username: "",
                })
              }
              value={form.providerConnectionId}
            >
              <option value={LOCAL_BROWSER_PROVIDER}>
                Voxos Local Browser (recommended)
              </option>
              {browserConnections.map((connection) => (
                <option key={connection.id} value={connection.id}>
                  {connection.label}
                </option>
              ))}
            </select>
          </label>
          {usesStoredCredentials ? (
            <>
              <label className="field">
                <span>Website username or email</span>
                <input
                  autoComplete="off"
                  disabled={!canEdit || isCreating}
                  onChange={(event) =>
                    setForm({ ...form, username: event.currentTarget.value })
                  }
                  value={form.username}
                />
              </label>
              <label className="field">
                <span>Website password</span>
                <input
                  autoComplete="new-password"
                  disabled={!canEdit || isCreating}
                  onChange={(event) =>
                    setForm({ ...form, password: event.currentTarget.value })
                  }
                  type="password"
                  value={form.password}
                />
              </label>
            </>
          ) : null}
          <fieldset className="browser-account-agent-picker">
            <legend>Agents allowed to request this account</legend>
            {agents.map((agent) => (
              <label className="inline-checkbox" key={agent.id}>
                <input
                  checked={form.agentIds.includes(agent.id)}
                  disabled={!canEdit || isCreating}
                  onChange={() =>
                    setForm({
                      ...form,
                      agentIds: form.agentIds.includes(agent.id)
                        ? form.agentIds.filter((id) => id !== agent.id)
                        : [...form.agentIds, agent.id],
                    })
                  }
                  type="checkbox"
                />
                <span>{agent.display_name}</span>
              </label>
            ))}
          </fieldset>
          <button
            className="primary"
            disabled={
              !canEdit ||
              isCreating ||
              form.label.trim() === "" ||
              form.primaryOrigin.trim() === "" ||
              (supportsBrowserCountry &&
                form.browserCountryCode.trim().length !== 2) ||
              (!usesLocalBrowser && selectedConnection === undefined) ||
              (usesStoredCredentials &&
                (form.username === "" || form.password === ""))
            }
            type="submit"
          >
            <GlobeLock aria-hidden="true" size={16} />
            {isCreating ? "Creating…" : "Create Browser Account"}
          </button>
        </form>
      </section>

      {error !== null ? <p className="error-text">{error}</p> : null}

      <section className="browser-account-list" aria-busy={isLoading}>
        {!isLoading && accounts.length === 0 ? (
          <div className="settings-panel empty-state">
            <KeyRound aria-hidden="true" size={22} />
            <p>No Browser Accounts yet.</p>
          </div>
        ) : null}
        {accounts.map((account) => {
          const isBusy = busyAccountId === account.id;
          const isEditingDetails = editingAccountId === account.id;
          const connection = connections.find(
            (item) => item.id === account.provider_connection_id,
          );
          return (
            <article className="settings-panel browser-account-card" key={account.id}>
              <div className="section-heading-row">
                <div className="section-title">
                  <p className="eyebrow">{statusLabel(account.status)}</p>
                  <h2>{account.label}</h2>
                </div>
                <div className="browser-account-heading-actions">
                  <span className={`status-chip ${account.status}`}>
                    {account.status.replaceAll("_", " ")}
                  </span>
                  {!isEditingDetails ? (
                    <button
                      disabled={!canEdit || busyAccountId !== null}
                      onClick={() => editAccountDetails(account)}
                      type="button"
                    >
                      <Pencil aria-hidden="true" size={15} />
                      Edit details
                    </button>
                  ) : null}
                </div>
              </div>
              {isEditingDetails && detailsDraft !== null ? (
                <form
                  className="settings-form browser-account-details-form"
                  onSubmit={(event) => void saveAccountDetails(event, account)}
                >
                  <label className="field">
                    <span>Account label</span>
                    <input
                      aria-label={`Edit account label for ${account.label}`}
                      disabled={!canEdit || isBusy}
                      onChange={(event) =>
                        setDetailsDraft({
                          ...detailsDraft,
                          label: event.currentTarget.value,
                        })
                      }
                      value={detailsDraft.label}
                    />
                  </label>
                  <label className="field">
                    <span>Exact website origin</span>
                    <input
                      aria-label={`Edit website origin for ${account.label}`}
                      disabled={!canEdit || isBusy}
                      onChange={(event) =>
                        setDetailsDraft({
                          ...detailsDraft,
                          primaryOrigin: event.currentTarget.value,
                        })
                      }
                      type="url"
                      value={detailsDraft.primaryOrigin}
                    />
                  </label>
                  <label className="field">
                    <span>Login and SSO origins</span>
                    <textarea
                      aria-label={`Edit login and SSO origins for ${account.label}`}
                      disabled={!canEdit || isBusy}
                      onChange={(event) =>
                        setDetailsDraft({
                          ...detailsDraft,
                          authenticationOrigins: event.currentTarget.value,
                        })
                      }
                      placeholder={"https://login.example.com\nhttps://id.example.com"}
                      value={detailsDraft.authenticationOrigins}
                    />
                    <small className="muted">
                      Add every exact origin used during sign-in, one per line,
                      including destinations reached after a login redirect.
                    </small>
                  </label>
                  {account.provider === "browser_use_cloud" ? (
                    <label className="field">
                      <span>Browser country</span>
                      <input
                        aria-label={`Edit browser country for ${account.label}`}
                        autoCapitalize="characters"
                        disabled={!canEdit || isBusy}
                        maxLength={2}
                        minLength={2}
                        onChange={(event) =>
                          setDetailsDraft({
                            ...detailsDraft,
                            browserCountryCode:
                              event.currentTarget.value.toUpperCase(),
                          })
                        }
                        pattern="[A-Za-z]{2}"
                        required
                        value={detailsDraft.browserCountryCode}
                      />
                      <small className="muted">
                        Changing this requires secure sign-in again.
                      </small>
                    </label>
                  ) : null}
                  <div className="provider-card-actions">
                    <button
                      className="primary"
                      disabled={
                        !canEdit ||
                        isBusy ||
                        detailsDraft.label.trim() === "" ||
                        detailsDraft.primaryOrigin.trim() === "" ||
                        (account.provider === "browser_use_cloud" &&
                          detailsDraft.browserCountryCode.trim().length !== 2)
                      }
                      type="submit"
                    >
                      {isBusy ? "Saving…" : "Save details"}
                    </button>
                    <button
                      disabled={!canEdit || isBusy}
                      onClick={() => {
                        setEditingAccountId(null);
                        setDetailsDraft(null);
                      }}
                      type="button"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              ) : (
                <>
                  <dl className="browser-account-metadata">
                    <div>
                      <dt>Website</dt>
                      <dd>{account.primary_origin}</dd>
                    </div>
                    <div>
                      <dt>Provider</dt>
                      <dd>
                        {connection?.label ??
                          (account.provider === "stagehand_local"
                            ? "Voxos Local Browser"
                            : account.provider === "browserless"
                            ? "Browserless"
                            : "Browser Use Cloud")}
                      </dd>
                    </div>
                    {account.provider === "browser_use_cloud" ? (
                      <div>
                        <dt>Browser country</dt>
                        <dd>{account.browser_country_code}</dd>
                      </div>
                    ) : null}
                    <div>
                      <dt>Last verified</dt>
                      <dd>{formatTimestamp(account.last_verified_at)}</dd>
                    </div>
                    <div>
                      <dt>Last used</dt>
                      <dd>{formatTimestamp(account.last_used_at)}</dd>
                    </div>
                  </dl>
                  {account.authentication_origins.length > 0 ? (
                    <p className="muted">
                      Sign-in origins: {account.authentication_origins.join(", ")}
                    </p>
                  ) : null}
                </>
              )}
              {account.error_category !== null ? (
                <p className="error-text">
                  Recovery needed: {account.error_category.replaceAll("_", " ")}
                </p>
              ) : null}
              {account.authentication_mode === "stored_credentials" ? (
                <fieldset className="browser-account-agent-picker">
                  <legend>Replace stored credentials</legend>
                  <input
                    aria-label={`${account.label} username`}
                    autoComplete="off"
                    disabled={!canEdit || busyAccountId !== null}
                    onChange={(event) =>
                      setCredentialDrafts({
                        ...credentialDrafts,
                        [account.id]: {
                          password: credentialDrafts[account.id]?.password ?? "",
                          username: event.currentTarget.value,
                        },
                      })
                    }
                    placeholder="Username or email"
                    value={credentialDrafts[account.id]?.username ?? ""}
                  />
                  <input
                    aria-label={`${account.label} password`}
                    autoComplete="new-password"
                    disabled={!canEdit || busyAccountId !== null}
                    onChange={(event) =>
                      setCredentialDrafts({
                        ...credentialDrafts,
                        [account.id]: {
                          password: event.currentTarget.value,
                          username: credentialDrafts[account.id]?.username ?? "",
                        },
                      })
                    }
                    placeholder="Password"
                    type="password"
                    value={credentialDrafts[account.id]?.password ?? ""}
                  />
                  <button
                    disabled={
                      !canEdit ||
                      busyAccountId !== null ||
                      (credentialDrafts[account.id]?.username ?? "") === "" ||
                      (credentialDrafts[account.id]?.password ?? "") === ""
                    }
                    onClick={() => void saveCredentials(account)}
                    type="button"
                  >
                    Update credentials
                  </button>
                </fieldset>
              ) : null}
              <fieldset className="browser-account-agent-picker">
                <legend>Assigned agents</legend>
                {agents.map((agent) => (
                  <label className="inline-checkbox" key={agent.id}>
                    <input
                      checked={account.agent_ids.includes(agent.id)}
                      disabled={!canEdit || busyAccountId !== null}
                      onChange={() => void toggleAgent(account, agent.id)}
                      type="checkbox"
                    />
                    <span>{agent.display_name}</span>
                  </label>
                ))}
              </fieldset>
              <div className="provider-card-actions">
                {account.provider === "browser_use_cloud" && account.status === "setup_in_progress" ? (
                  <>
                    <button
                      className="primary"
                      disabled={!canEdit || busyAccountId !== null}
                      onClick={() => void completeSetup(account)}
                      type="button"
                    >
                      <UserCheck aria-hidden="true" size={16} />
                      {isBusy ? "Checking…" : "I’m signed in"}
                    </button>
                    <button
                      disabled={!canEdit || busyAccountId !== null}
                      onClick={() => void cancelSetup(account)}
                      type="button"
                    >
                      Cancel setup
                    </button>
                  </>
                ) : account.provider === "browser_use_cloud" && account.status === "setup_required" ? (
                  <button
                    className="primary"
                    disabled={!canEdit || busyAccountId !== null}
                    onClick={() => void openSetup(account, false)}
                    type="button"
                  >
                    <ExternalLink aria-hidden="true" size={16} />
                    Start secure sign-in
                  </button>
                ) : account.provider === "browser_use_cloud" && ["expired", "error"].includes(account.status) ? (
                  <button
                    className="primary"
                    disabled={!canEdit || busyAccountId !== null}
                    onClick={() => void openSetup(account, true)}
                    type="button"
                  >
                    <ExternalLink aria-hidden="true" size={16} />
                    Reauthenticate
                  </button>
                ) : null}
                {account.status === "ready" ? (
                  <button
                    disabled={!canEdit || busyAccountId !== null}
                    onClick={() => void revokeAccount(account)}
                    type="button"
                  >
                    Revoke
                  </button>
                ) : null}
                <button
                  className="danger-button"
                  disabled={!canEdit || busyAccountId !== null}
                  onClick={() => void removeAccount(account)}
                  type="button"
                >
                  <Trash2 aria-hidden="true" size={16} />
                  Delete
                </button>
              </div>
            </article>
          );
        })}
      </section>
    </div>
  );
}

function parseOrigins(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[\n,]/)
        .map((origin) => origin.trim())
        .filter(Boolean),
    ),
  );
}

function requiredBrowserExecutionTool(connection: McpServer): string | null {
  const presetId = connection.safe_metadata.preset_id;
  if (presetId === "browserless") {
    return "browserless_agent";
  }
  if (presetId === "browser-use-cloud") {
    return "run_session";
  }
  return null;
}

function isConnectedBrowserConnection(connection: McpServer): boolean {
  return (
    requiredBrowserExecutionTool(connection) !== null &&
    connection.enabled &&
    connection.trust_state === "trusted" &&
    connection.status === "connected" &&
    connection.auth.authenticated
  );
}

function isReadyBrowserConnection(connection: McpServer): boolean {
  const requiredTool = requiredBrowserExecutionTool(connection);
  return (
    isConnectedBrowserConnection(connection) &&
    requiredTool !== null &&
    connection.allowed_tools.includes(requiredTool)
  );
}

function upsertAccount(
  accounts: BrowserAccount[],
  account: BrowserAccount,
): BrowserAccount[] {
  return sortAccounts([
    ...accounts.filter((item) => item.id !== account.id),
    account,
  ]);
}

function sortAccounts(accounts: BrowserAccount[]): BrowserAccount[] {
  return [...accounts].sort((left, right) => left.label.localeCompare(right.label));
}

function statusLabel(status: BrowserAccount["status"]): string {
  if (status === "ready") {
    return "Ready for agent use";
  }
  if (status === "setup_in_progress") {
    return "Complete sign-in in Browser Use";
  }
  if (status === "deletion_pending") {
    return "Remote cleanup pending";
  }
  return "Browser Account";
}

function formatTimestamp(value: string | null): string {
  return value === null ? "Never" : new Date(value).toLocaleString();
}
