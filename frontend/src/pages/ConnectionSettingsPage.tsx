import { FormEvent, useState } from "react";
import { modeLabels } from "../appConstants";
import type {
  ConnectionMode,
  ConnectionSettings,
  ConnectionSettingsUpdater,
  LiveKitSettingsFormValues,
} from "../appTypes";
import type { BrandLogo, LiveKitSettings } from "../api";
import { TextAreaField, TextField } from "../components/FormFields";
import { ServiceLogo } from "../components/ServiceLogo";

type ConnectionSettingsPageProps = {
  canEdit: boolean;
  isLiveKitSettingsLoading: boolean;
  isLiveKitSettingsSaving: boolean;
  liveKitSettings: LiveKitSettings | null;
  liveKitSettingsError: string | null;
  saveLiveKitCredentials: (values: LiveKitSettingsFormValues) => Promise<boolean>;
  settings: ConnectionSettings;
  updateSettings: ConnectionSettingsUpdater;
};

const LIVEKIT_LOGO: BrandLogo = {
  domain: "livekit.io",
  label: "LiveKit",
  source: "domain",
};
const FIREBASE_LOGO: BrandLogo = {
  domain: "firebase.google.com",
  label: "Firebase",
  source: "domain",
};

export function ConnectionSettingsPage({
  canEdit,
  isLiveKitSettingsLoading,
  isLiveKitSettingsSaving,
  liveKitSettings,
  liveKitSettingsError,
  saveLiveKitCredentials,
  settings,
  updateSettings,
}: ConnectionSettingsPageProps) {
  return (
    <div className="settings-page settings-page-grid">
      <LiveKitSettingsPanel
        canEdit={canEdit}
        isLoading={isLiveKitSettingsLoading}
        isSaving={isLiveKitSettingsSaving}
        onSave={saveLiveKitCredentials}
        settings={liveKitSettings}
        settingsError={liveKitSettingsError}
      />
      <ConnectionConfigPanel
        canEdit={canEdit}
        settings={settings}
        updateSettings={updateSettings}
      />
    </div>
  );
}

function LiveKitSettingsPanel({
  canEdit,
  isLoading,
  isSaving,
  onSave,
  settings,
  settingsError,
}: {
  canEdit: boolean;
  isLoading: boolean;
  isSaving: boolean;
  onSave: (values: LiveKitSettingsFormValues) => Promise<boolean>;
  settings: LiveKitSettings | null;
  settingsError: string | null;
}) {
  const [liveKitUrl, setLiveKitUrl] = useState(settings?.livekit_url ?? "");
  const [liveKitApiKey, setLiveKitApiKey] = useState("");
  const [liveKitApiSecret, setLiveKitApiSecret] = useState("");
  const canSubmit =
    canEdit &&
    !isLoading &&
    !isSaving &&
    liveKitUrl.trim() !== "" &&
    liveKitApiKey.trim() !== "" &&
    liveKitApiSecret.trim() !== "";

  const submit = async () => {
    if (!canSubmit) {
      return;
    }

    const saved = await onSave({
      livekitApiKey: liveKitApiKey.trim(),
      livekitApiSecret: liveKitApiSecret.trim(),
      livekitUrl: liveKitUrl.trim(),
    });
    if (saved) {
      setLiveKitApiKey("");
      setLiveKitApiSecret("");
    }
  };

  return (
    <section className="settings-panel">
      <div className="section-title service-title">
        <ServiceLogo label="LiveKit" logo={LIVEKIT_LOGO} size="md" />
        <div>
          <p className="eyebrow">LiveKit</p>
          <h2>Credentials</h2>
        </div>
      </div>

      <div className="setup-status">
        {isLoading ? (
          <span>Loading settings</span>
        ) : settings?.configured ? (
          <span>LiveKit credentials configured</span>
        ) : (
          <span>LiveKit credentials required</span>
        )}
      </div>

      <form
        className="settings-form"
        onSubmit={(event: FormEvent<HTMLFormElement>) => {
          event.preventDefault();
          void submit();
        }}
      >
        <TextField
          disabled={!canEdit || isSaving}
          label="LiveKit URL"
          onChange={setLiveKitUrl}
          placeholder="wss://your-livekit-host"
          value={liveKitUrl}
        />
        <TextField
          disabled={!canEdit || isSaving}
          label="LiveKit API key"
          onChange={setLiveKitApiKey}
          placeholder={settings?.configured ? "Enter a new key" : "API..."}
          type="password"
          value={liveKitApiKey}
        />
        <TextField
          disabled={!canEdit || isSaving}
          label="LiveKit API secret"
          onChange={setLiveKitApiSecret}
          placeholder={settings?.configured ? "Enter a new secret" : "Secret..."}
          type="password"
          value={liveKitApiSecret}
        />
        <button className="primary" disabled={!canSubmit} type="submit">
          {isSaving
            ? "Saving"
            : settings?.configured
              ? "Replace credentials"
              : "Save credentials"}
        </button>
      </form>

      {settingsError !== null ? <p className="error-text">{settingsError}</p> : null}
    </section>
  );
}

function ConnectionConfigPanel({
  canEdit,
  settings,
  updateSettings,
}: {
  canEdit: boolean;
  settings: ConnectionSettings;
  updateSettings: ConnectionSettingsUpdater;
}) {
  return (
    <section className="settings-panel">
      <div className="section-title">
        <p className="eyebrow">Routing</p>
        <h2>Agent connection</h2>
      </div>

      <div className="settings-form">
        <div className="form-section">
          <label className="field-label" htmlFor="mode">
            Connect mode
          </label>
          <div className="segmented" id="mode" role="group">
            {Object.entries(modeLabels).map(([mode, label]) => (
              <button
                aria-pressed={settings.mode === mode}
                className={settings.mode === mode ? "selected" : ""}
                disabled={!canEdit}
                key={mode}
                onClick={() => updateSettings("mode", mode as ConnectionMode)}
                type="button"
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {settings.mode === "endpoint" ? (
          <TextField
            disabled={!canEdit}
            label="Token endpoint"
            onChange={(value) => updateSettings("tokenEndpoint", value)}
            placeholder="http://localhost:7100/api/livekit/token"
            value={settings.tokenEndpoint}
          />
        ) : null}

        {settings.mode === "sandbox" ? (
          <TextField
            disabled={!canEdit}
            label="Sandbox token server ID"
            onChange={(value) => updateSettings("sandboxId", value)}
            placeholder="sandbox_xxx"
            value={settings.sandboxId}
          />
        ) : null}

        {settings.mode === "manual" ? (
          <>
            <TextField
              disabled={!canEdit}
              label="LiveKit URL"
              onChange={(value) => updateSettings("livekitUrl", value)}
              placeholder="wss://your-livekit-host"
              value={settings.livekitUrl}
            />
            <TextAreaField
              disabled={!canEdit}
              label="Participant token"
              onChange={(value) => updateSettings("participantToken", value)}
              placeholder="eyJhbGciOi..."
              value={settings.participantToken}
            />
          </>
        ) : null}

        {settings.mode === "endpoint" ? (
          <div className="identity-summary identity-summary-row">
            <ServiceLogo label="Firebase" logo={FIREBASE_LOGO} size="sm" />
            <span>
              <span>Identity</span>
              <strong>Firebase authenticated</strong>
            </span>
          </div>
        ) : (
          <TextField
            disabled={!canEdit}
            label="VoxOS user id"
            onChange={(value) => updateSettings("userId", value)}
            placeholder="manual-user-id"
            value={settings.userId}
          />
        )}
      </div>
    </section>
  );
}
