import type { LogEntry } from "../appTypes";
import type {
  ToolGrant,
  ToolGrantUpdate,
  ToolPolicy,
  ToolRouteCandidate,
  ToolRouteDefinition,
  ToolRoutePreference,
  ToolRoutePreferenceUpdate,
  ToolRouteSelection,
} from "../api";
import { isToolAllowed, useToolGrants } from "../toolGrants";
import { useToolRoutes } from "../toolRoutes";

type ToolsSettingsPageProps = {
  canEdit: boolean;
  onLog: (message: string, level?: LogEntry["level"]) => void;
};

export function ToolsSettingsPage({ canEdit, onLog }: ToolsSettingsPageProps) {
  const {
    error,
    grants,
    internalTools,
    isLoading,
    isSaving,
    refreshGrants,
    setGrant,
  } = useToolGrants({ onLog });
  const toolRoutes = useToolRoutes({ onLog });

  return (
    <div className="settings-page">
      <ToolRoutingPanel
        canEdit={canEdit}
        candidates={toolRoutes.candidates}
        definitions={toolRoutes.definitions}
        isLoading={toolRoutes.isLoading}
        isSaving={toolRoutes.isSaving}
        onRefresh={toolRoutes.refreshRoutes}
        onSetRoute={toolRoutes.setRoute}
        preferences={toolRoutes.preferences}
        settingsError={toolRoutes.error}
      />
      <InternalToolGrantsPanel
        canEdit={canEdit}
        grants={grants}
        internalTools={internalTools}
        isLoading={isLoading}
        isSaving={isSaving}
        onRefresh={refreshGrants}
        onSetGrant={setGrant}
        settingsError={error}
      />
    </div>
  );
}

function ToolRoutingPanel({
  canEdit,
  candidates,
  definitions,
  isLoading,
  isSaving,
  onRefresh,
  onSetRoute,
  preferences,
  settingsError,
}: {
  canEdit: boolean;
  candidates: ToolRouteCandidate[];
  definitions: ToolRouteDefinition[];
  isLoading: boolean;
  isSaving: boolean;
  onRefresh: () => Promise<void>;
  onSetRoute: (preference: ToolRoutePreferenceUpdate) => Promise<void>;
  preferences: ToolRoutePreference[];
  settingsError: string | null;
}) {
  return (
    <section className="settings-panel tool-approval-panel">
      <div className="section-heading-row">
        <div className="section-title">
          <p className="eyebrow">Routing</p>
          <h2>Preferred tools for the default agent</h2>
          <p className="muted">
            Choose which authorized provider handles an action. Automatic uses the
            recommended available provider and falls back by priority.
          </p>
        </div>
        <button
          aria-busy={isLoading}
          disabled={isLoading || isSaving}
          onClick={() => void onRefresh()}
          type="button"
        >
          Refresh
        </button>
      </div>

      <div className="tool-approval-grid">
        {definitions.map((definition) => {
          const preference = preferences.find(
            (candidatePreference) => candidatePreference.action === definition.action,
          );
          const actionCandidates = candidates
            .filter((candidate) => candidate.action === definition.action)
            .sort((left, right) => left.automatic_priority - right.automatic_priority);
          const selectedValue = selectedRouteValue(preference);
          const automaticProvider = actionCandidates[0]?.provider_label;
          const selectedCandidateAvailable = actionCandidates.some(
            (candidate) => routeSelectionValue(candidate.reference) === selectedValue,
          );
          return (
            <label className="field" key={definition.action}>
              <span>{definition.label}</span>
              <select
                aria-label={`${definition.label} provider`}
                disabled={!canEdit || isLoading || isSaving}
                onChange={(event) => {
                  const candidate = actionCandidates.find(
                    (item) => routeSelectionValue(item.reference) === event.currentTarget.value,
                  );
                  void onSetRoute(
                    candidate === undefined
                      ? {
                          action: definition.action,
                          mode: "automatic",
                          pinned_tool: null,
                        }
                      : {
                          action: definition.action,
                          mode: "pinned",
                          pinned_tool: {
                            integration_id: candidate.reference.integration_id,
                            mcp_server_id: candidate.reference.mcp_server_id,
                            tool_name: candidate.reference.tool_name,
                            tool_source: candidate.reference.tool_source,
                          },
                        },
                  );
                }}
                value={selectedValue}
              >
                <option value="automatic">
                  Automatic
                  {automaticProvider === undefined ? "" : ` (${automaticProvider})`}
                </option>
                {selectedValue !== "automatic" && !selectedCandidateAvailable ? (
                  <option disabled value={selectedValue}>
                    Pinned provider unavailable
                  </option>
                ) : null}
                {actionCandidates.map((candidate) => (
                  <option
                    key={routeSelectionValue(candidate.reference)}
                    value={routeSelectionValue(candidate.reference)}
                  >
                    {candidate.provider_label} · {candidate.reference.tool_name}
                  </option>
                ))}
              </select>
              <small>{definition.description}</small>
              {actionCandidates.length === 0 ? (
                <small className="muted">
                  No authorized providers currently implement this action.
                </small>
              ) : null}
            </label>
          );
        })}
      </div>

      {settingsError !== null ? <p className="error-text">{settingsError}</p> : null}
    </section>
  );
}

function InternalToolGrantsPanel({
  canEdit,
  grants,
  internalTools,
  isLoading,
  isSaving,
  onRefresh,
  onSetGrant,
  settingsError,
}: {
  canEdit: boolean;
  grants: ToolGrant[];
  internalTools: ToolPolicy[];
  isLoading: boolean;
  isSaving: boolean;
  onRefresh: () => Promise<void>;
  onSetGrant: (grant: ToolGrantUpdate) => Promise<void>;
  settingsError: string | null;
}) {
  return (
    <section className="settings-panel tool-approval-panel">
      <div className="section-heading-row">
        <div className="section-title">
          <p className="eyebrow">Tools</p>
          <h2>Internal approvals</h2>
        </div>
        <button
          aria-busy={isLoading}
          disabled={isLoading || isSaving}
          onClick={() => void onRefresh()}
          type="button"
        >
          Refresh
        </button>
      </div>

      <div className="tool-approval-grid">
        {internalTools.length > 0 ? (
          internalTools.map((tool) => {
            const enabled = isToolAllowed(grants, "internal", null, tool.tool_name);
            return (
              <label className="tool-approval-row" key={tool.tool_name}>
                <input
                  checked={enabled}
                  disabled={!canEdit || isLoading || isSaving}
                  onChange={(event) =>
                    void onSetGrant({
                      risk: tool.risk,
                      status: event.currentTarget.checked ? "allowed" : "denied",
                      tool_name: tool.tool_name,
                      tool_source: "internal",
                    })
                  }
                  type="checkbox"
                />
                <span>
                  <strong>{humanizeToolName(tool.tool_name)}</strong>
                  <small>
                    {tool.tool_name} · {tool.risk} risk ·{" "}
                    {tool.confirmation_required ? "mutating" : "read-only"}
                  </small>
                </span>
              </label>
            );
          })
        ) : (
          <p className="muted">No internal tools loaded.</p>
        )}
      </div>

      {settingsError !== null ? <p className="error-text">{settingsError}</p> : null}
    </section>
  );
}

function humanizeToolName(toolName: string): string {
  const words = toolName.replaceAll("_", " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function selectedRouteValue(
  preference: ToolRoutePreference | undefined,
): string {
  if (preference?.mode !== "pinned" || preference.pinned_tool === null) {
    return "automatic";
  }
  return routeSelectionValue(preference.pinned_tool);
}

function routeSelectionValue(selection: ToolRouteSelection): string {
  return [
    selection.tool_source,
    selection.mcp_server_id ?? "",
    selection.integration_id ?? "",
    selection.tool_name,
  ].join(":");
}
