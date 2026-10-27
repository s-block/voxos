import { FormEvent, useState } from "react";
import type {
  OpenAICompatibleProviderFormValues,
  TextProviderKeyFormValues,
  VoiceProviderKeyFormValues,
} from "../appTypes";
import type {
  TextProvider,
  TextProviderSettings,
  TextProviderSummary,
  VoiceProvider,
  VoiceProviderSettings,
  VoiceProviderSummary,
} from "../api";
import { TextAreaField, TextField } from "../components/FormFields";
import { ServiceLogo } from "../components/ServiceLogo";

const SHARED_TEXT_PROVIDER_BY_VOICE: Partial<
  Record<VoiceProvider, TextProvider>
> = {
  grok_voice_agent: "xai",
  openai_realtime: "openai",
};

type ProviderSettingsPageProps = {
  canEdit: boolean;
  isTextCredentialSaving: boolean;
  isTextSettingsLoading: boolean;
  isVoiceCredentialSaving: boolean;
  isVoiceSettingsLoading: boolean;
  saveTextProviderKey: (values: TextProviderKeyFormValues) => Promise<boolean>;
  saveOpenAICompatibleProvider: (
    values: OpenAICompatibleProviderFormValues,
  ) => Promise<boolean>;
  saveVoiceProviderKey: (values: VoiceProviderKeyFormValues) => Promise<boolean>;
  textProviderSettings: TextProviderSettings | null;
  textSettingsError: string | null;
  voiceProviderSettings: VoiceProviderSettings | null;
  voiceSettingsError: string | null;
};

export function ProviderSettingsPage({
  canEdit,
  isTextCredentialSaving,
  isTextSettingsLoading,
  isVoiceCredentialSaving,
  isVoiceSettingsLoading,
  saveTextProviderKey,
  saveOpenAICompatibleProvider,
  saveVoiceProviderKey,
  textProviderSettings,
  textSettingsError,
  voiceProviderSettings,
  voiceSettingsError,
}: ProviderSettingsPageProps) {
  const voiceProviders = voiceProviderSettings?.providers ?? [];
  const textProviders = textProviderSettings?.providers ?? [];
  const textProvidersById = new Map(
    textProviders.map((provider) => [provider.provider, provider]),
  );
  const sharedTextProviderIds = new Set(
    Object.values(SHARED_TEXT_PROVIDER_BY_VOICE),
  );
  const textOnlyProviders = textProviders.filter(
    (provider) => !sharedTextProviderIds.has(provider.provider),
  );
  const isLoading = isTextSettingsLoading || isVoiceSettingsLoading;

  return (
    <div className="settings-page">
      <section className="settings-panel">
        <div className="section-heading-row">
          <div className="section-title">
            <p className="eyebrow">Providers</p>
            <h2>Credentials</h2>
          </div>
          {isLoading ? <span className="status-chip">Loading</span> : null}
        </div>
        <p className="muted">
          Keys are stored by provider. Each card lists the Voxos text and voice
          integrations that can currently use that key; Agent settings choose their
          models and voices independently.
        </p>
        <div className="provider-card-grid">
          {voiceProviders.map((provider) => {
            const textProviderId = SHARED_TEXT_PROVIDER_BY_VOICE[provider.provider];
            return (
              <VoiceProviderCard
                canEdit={canEdit}
                isSaving={isVoiceCredentialSaving}
                key={provider.provider}
                provider={provider}
                saveProviderKey={saveVoiceProviderKey}
                textProvider={
                  textProviderId === undefined
                    ? null
                    : textProvidersById.get(textProviderId) ?? null
                }
              />
            );
          })}
          {textOnlyProviders.map((provider) =>
            provider.provider === "openai_compatible" ? (
              <OpenAICompatibleProviderCard
                canEdit={canEdit}
                isSaving={isTextCredentialSaving}
                key={provider.provider}
                provider={provider}
                saveProvider={saveOpenAICompatibleProvider}
              />
            ) : (
              <TextProviderCard
                canEdit={canEdit}
                isSaving={isTextCredentialSaving}
                key={provider.provider}
                provider={provider}
                saveProviderKey={saveTextProviderKey}
              />
            ),
          )}
        </div>
        {!isLoading && voiceProviders.length === 0 && textOnlyProviders.length === 0 ? (
          <p className="muted">No providers returned by the API.</p>
        ) : null}
        {voiceSettingsError !== null ? <p className="error-text">{voiceSettingsError}</p> : null}
        {textSettingsError !== null ? <p className="error-text">{textSettingsError}</p> : null}
      </section>
    </div>
  );
}

function OpenAICompatibleProviderCard({
  canEdit,
  isSaving,
  provider,
  saveProvider,
}: {
  canEdit: boolean;
  isSaving: boolean;
  provider: TextProviderSummary;
  saveProvider: (
    values: OpenAICompatibleProviderFormValues,
  ) => Promise<boolean>;
}) {
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(provider.base_url ?? "");
  const [modelsText, setModelsText] = useState(
    provider.model_options.map((option) => option.model).join("\n"),
  );
  const models = configuredModelIds(modelsText);
  const canSave =
    canEdit &&
    !isSaving &&
    baseUrl.trim() !== "" &&
    models.length > 0;

  const save = async () => {
    if (!canSave) {
      return;
    }
    const saved = await saveProvider({
      apiKey: apiKey.trim(),
      baseUrl: baseUrl.trim(),
      models,
    });
    if (saved) {
      setApiKey("");
    }
  };

  return (
    <article className="provider-card">
      <div className="provider-card-header">
        <div className="provider-card-heading">
          <ServiceLogo label={provider.label} logo={provider.logo} size="md" />
          <div>
            <h3>{provider.label}</h3>
            <p>Local or remote text endpoint</p>
          </div>
        </div>
        <span className={`status-chip ${provider.configured ? "status-connected" : ""}`}>
          {provider.configured ? "Ready" : "Setup required"}
        </span>
      </div>

      <p className="muted">
        Enter any URL the Voxos backend can reach. No hostname allowlist is
        required. The endpoint must implement streaming OpenAI Chat Completions
        and structured tool calls.
      </p>
      <form
        className="settings-form"
        onSubmit={(event: FormEvent<HTMLFormElement>) => {
          event.preventDefault();
          void save();
        }}
      >
        <TextField
          disabled={!canEdit || isSaving}
          label="Base URL"
          onChange={setBaseUrl}
          placeholder="http://host.docker.internal:11434/v1"
          value={baseUrl}
        />
        <TextAreaField
          disabled={!canEdit || isSaving}
          label="Available model IDs"
          onChange={setModelsText}
          placeholder={"gpt-oss:20b\nqwen3:8b"}
          value={modelsText}
        />
        <TextField
          disabled={!canEdit || isSaving}
          label="Bearer API key (optional)"
          onChange={setApiKey}
          placeholder="Leave blank for an unauthenticated endpoint"
          type="password"
          value={apiKey}
        />
        <div className="provider-card-actions">
          <button className="primary" disabled={!canSave} type="submit">
            {isSaving ? "Saving" : "Save endpoint"}
          </button>
        </div>
      </form>
      <a href={provider.docs_url} rel="noreferrer" target="_blank">
        OpenAI compatibility reference
      </a>
    </article>
  );
}

function configuredModelIds(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[\n,]/)
        .map((model) => model.trim())
        .filter((model) => model !== ""),
    ),
  );
}

function VoiceProviderCard({
  canEdit,
  isSaving,
  provider,
  saveProviderKey,
  textProvider,
}: {
  canEdit: boolean;
  isSaving: boolean;
  provider: VoiceProviderSummary;
  saveProviderKey: (values: VoiceProviderKeyFormValues) => Promise<boolean>;
  textProvider: TextProviderSummary | null;
}) {
  const [apiKey, setApiKey] = useState("");
  const canSave = canEdit && !isSaving && apiKey.trim() !== "";
  const capabilities = textProvider === null ? "Voice" : "Voice and text";

  const save = async () => {
    if (!canSave) {
      return;
    }
    const saved = await saveProviderKey({
      apiKey: apiKey.trim(),
      provider: provider.provider,
    });
    if (saved) {
      setApiKey("");
    }
  };

  return (
    <article className="provider-card">
      <div className="provider-card-header">
        <div className="provider-card-heading">
          <ServiceLogo label={provider.label} logo={provider.logo} size="md" />
          <div>
            <h3>{provider.label}</h3>
            <p>{capabilities}</p>
          </div>
        </div>
        <span className={`status-chip ${provider.configured ? "status-connected" : ""}`}>
          {provider.configured ? "Saved" : "Key required"}
        </span>
      </div>

      <div className="provider-meta-grid">
        <span>Voice model</span>
        <strong>{provider.default_model}</strong>
        <span>Voice</span>
        <strong>{provider.default_voice}</strong>
        {textProvider !== null ? (
          <>
            <span>Text model</span>
            <strong>{textProvider.default_model}</strong>
          </>
        ) : null}
        <span>Voice runtime</span>
        <strong>{provider.runtime_supported ? "Ready" : "Adapter needed"}</strong>
        {provider.configured ? (
          <>
            <span>Key</span>
            <strong>
              {provider.credential_last_four === null
                ? "Stored"
                : `**** ${provider.credential_last_four}`}
            </strong>
          </>
        ) : null}
      </div>
      <p className="muted">{provider.note}</p>

      <form
        className="settings-form"
        onSubmit={(event: FormEvent<HTMLFormElement>) => {
          event.preventDefault();
          void save();
        }}
      >
        <TextField
          disabled={!canEdit || isSaving}
          label={provider.api_key_label}
          onChange={setApiKey}
          placeholder={provider.configured ? "Enter a new key" : "Paste key"}
          type="password"
          value={apiKey}
        />
        <div className="provider-card-actions">
          <button className="primary" disabled={!canSave} type="submit">
            {isSaving ? "Saving" : "Save key"}
          </button>
        </div>
      </form>
      <div className="provider-card-actions">
        <a href={provider.docs_url} rel="noreferrer" target="_blank">
          Voice integration docs
        </a>
        {textProvider !== null ? (
          <a href={textProvider.docs_url} rel="noreferrer" target="_blank">
            Text API docs
          </a>
        ) : null}
      </div>
    </article>
  );
}

function TextProviderCard({
  canEdit,
  isSaving,
  provider,
  saveProviderKey,
}: {
  canEdit: boolean;
  isSaving: boolean;
  provider: TextProviderSummary;
  saveProviderKey: (values: TextProviderKeyFormValues) => Promise<boolean>;
}) {
  const [apiKey, setApiKey] = useState("");
  const canSave = canEdit && !isSaving && apiKey.trim() !== "";
  const defaultModel = provider.model_options.find(
    (option) => option.model === provider.default_model,
  );
  const defaultMultimodalModel = provider.model_options.find(
    (option) => option.model === provider.default_multimodal_model,
  );
  const defaultBrowserModel = provider.model_options.find(
    (option) => option.model === provider.default_browser_model,
  );

  const save = async () => {
    if (!canSave) {
      return;
    }
    const saved = await saveProviderKey({
      apiKey: apiKey.trim(),
      provider: provider.provider,
    });
    if (saved) {
      setApiKey("");
    }
  };

  return (
    <article className="provider-card">
      <div className="provider-card-header">
        <div className="provider-card-heading">
          <ServiceLogo label={provider.label} logo={provider.logo} size="md" />
          <div>
            <h3>{provider.label}</h3>
            <p>Text</p>
          </div>
        </div>
        <span className={`status-chip ${provider.configured ? "status-connected" : ""}`}>
          {provider.configured ? "Saved" : "Key required"}
        </span>
      </div>

      <div className="provider-meta-grid">
        <span>Text model</span>
        <strong>{provider.default_model}</strong>
        <span>Image model</span>
        <strong>{defaultMultimodalModel?.model ?? "Unavailable"}</strong>
        <span>Browser model</span>
        <strong>{defaultBrowserModel?.model ?? "Unavailable"}</strong>
        {provider.configured ? (
          <>
            <span>Key</span>
            <strong>
              {provider.credential_last_four === null
                ? "Stored"
                : `**** ${provider.credential_last_four}`}
            </strong>
          </>
        ) : null}
      </div>
      {defaultModel !== undefined ? <p className="muted">{defaultModel.description}</p> : null}

      <form
        className="settings-form"
        onSubmit={(event: FormEvent<HTMLFormElement>) => {
          event.preventDefault();
          void save();
        }}
      >
        <TextField
          disabled={!canEdit || isSaving}
          label={provider.api_key_label}
          onChange={setApiKey}
          placeholder={provider.configured ? "Enter a new key" : "Paste key"}
          type="password"
          value={apiKey}
        />
        <div className="provider-card-actions">
          <button className="primary" disabled={!canSave} type="submit">
            {isSaving ? "Saving" : "Save key"}
          </button>
        </div>
      </form>
      <a href={provider.docs_url} rel="noreferrer" target="_blank">
        Provider docs
      </a>
    </article>
  );
}
