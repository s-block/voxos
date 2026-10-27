import { FormEvent, useEffect, useState } from "react";
import {
  DEFAULT_AGENT_INSTRUCTIONS,
  DEFAULT_INITIAL_REPLY,
  DEFAULT_MODEL,
  DEFAULT_PREFERRED_LANGUAGE,
  DEFAULT_TEXT_AGENT_INSTRUCTIONS,
  DEFAULT_TEXT_MODEL,
  DEFAULT_TEXT_PREFERRED_LANGUAGE,
  DEFAULT_VOICE,
} from "../appConstants";
import type {
  AgentIdentityFormValues,
  ImageGenerationPreferencesFormValues,
  TextPreferencesFormValues,
  VoicePreferencesFormValues,
} from "../appTypes";
import {
  loadDefaultAgent,
  updateAgent,
  type Agent,
  type ProviderImageGenerationModelOption,
  type ProviderModelOption,
  type TextProvider,
  type TextProviderSettings,
  type TextProviderSummary,
  type TextSettings,
  type VoiceProvider,
  type VoiceProviderSettings,
  type VoiceProviderSummary,
  type VoiceSettings,
} from "../api";
import { SelectField, TextAreaField, TextField } from "../components/FormFields";
import { ServiceLogo } from "../components/ServiceLogo";
import { modelIds, selectableModelOptions } from "../modelOptions";

type AgentSettingsPageProps = {
  canEdit: boolean;
  imageGenerationSettingsError: string | null;
  isImageGenerationSaving: boolean;
  isTextLoading: boolean;
  isTextSaving: boolean;
  isVoiceLoading: boolean;
  isVoiceSaving: boolean;
  onSaveImageGeneration: (
    values: ImageGenerationPreferencesFormValues,
  ) => Promise<boolean>;
  onSaveText: (values: TextPreferencesFormValues) => Promise<boolean>;
  onSaveVoice: (values: VoicePreferencesFormValues) => Promise<boolean>;
  provider: VoiceProviderSummary | null;
  providerSettings: VoiceProviderSettings | null;
  settings: VoiceSettings | null;
  settingsError: string | null;
  textProviderSettings: TextProviderSettings | null;
  textSettings: TextSettings | null;
  textSettingsError: string | null;
};

export function AgentSettingsPage({
  canEdit,
  imageGenerationSettingsError,
  isImageGenerationSaving,
  isTextLoading,
  isTextSaving,
  isVoiceLoading,
  isVoiceSaving,
  onSaveImageGeneration,
  onSaveText,
  onSaveVoice,
  provider,
  providerSettings,
  settings,
  settingsError,
  textProviderSettings,
  textSettings,
  textSettingsError,
}: AgentSettingsPageProps) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [isAgentLoading, setIsAgentLoading] = useState(false);
  const [isAgentSaving, setIsAgentSaving] = useState(false);
  const [agentError, setAgentError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const fetchAgent = async () => {
      setIsAgentLoading(true);
      setAgentError(null);
      try {
        const loadedAgent = await loadDefaultAgent();
        if (!cancelled) {
          setAgent(loadedAgent);
        }
      } catch (error) {
        if (!cancelled) {
          setAgentError(errorMessage(error));
        }
      } finally {
        if (!cancelled) {
          setIsAgentLoading(false);
        }
      }
    };

    void fetchAgent();

    return () => {
      cancelled = true;
    };
  }, []);

  const saveAgentIdentity = async ({
    description,
    displayName,
  }: AgentIdentityFormValues): Promise<boolean> => {
    if (agent === null) {
      return false;
    }
    setIsAgentSaving(true);
    setAgentError(null);
    try {
      const savedAgent = await updateAgent(agent.id, {
        description: description.trim() || null,
        display_name: displayName.trim(),
      });
      setAgent(savedAgent);
      return true;
    } catch (error) {
      setAgentError(errorMessage(error));
      return false;
    } finally {
      setIsAgentSaving(false);
    }
  };
  const activeTextProvider = activeTextProviderSummary(
    textProviderSettings,
    textSettings,
  );
  const activeImageGenerationProvider = imageGenerationProviderSummary(
    textProviderSettings,
    textSettings,
  );

  return (
    <div className="settings-page settings-page-grid">
      <section className="summary-panel">
        <div className="section-title">
          <p className="eyebrow">Active defaults</p>
          <h2>Agent runtime</h2>
        </div>
        <div className="provider-summary">
          <span>Voice provider</span>
          <strong className="provider-summary-value">
            {provider !== null ? (
              <ServiceLogo label={provider.label} logo={provider.logo} size="sm" />
            ) : null}
            {provider?.label ?? "Provider not loaded"}
          </strong>
          <span>Voice model</span>
          <strong>{settings?.model ?? provider?.default_model ?? DEFAULT_MODEL}</strong>
          <span>Voice</span>
          <strong>{settings?.voice ?? provider?.default_voice ?? DEFAULT_VOICE}</strong>
          <span>Voice status</span>
          <strong>
            {provider?.configured
              ? provider.runtime_supported
                ? "Ready"
                : "Adapter needed"
              : "Key required"}
          </strong>
          <span>Text provider</span>
          <strong className="provider-summary-value">
            {activeTextProvider !== null ? (
              <ServiceLogo
                label={activeTextProvider.label}
                logo={activeTextProvider.logo}
                size="sm"
              />
            ) : null}
            {activeTextProvider?.label ?? "Provider not loaded"}
          </strong>
          <span>Text model</span>
          <strong>
            {textSettings?.model ??
              activeTextProvider?.default_model ??
              DEFAULT_TEXT_MODEL}
          </strong>
          <span>Text status</span>
          <strong>
            {activeTextProvider?.configured
              ? activeTextProvider.runtime_supported
                ? "Ready"
                : "Adapter needed"
              : "Key required"}
          </strong>
          <span>Image provider</span>
          <strong className="provider-summary-value">
            {activeImageGenerationProvider !== null ? (
              <ServiceLogo
                label={activeImageGenerationProvider.label}
                logo={activeImageGenerationProvider.logo}
                size="sm"
              />
            ) : null}
            {activeImageGenerationProvider?.label ?? "Provider not loaded"}
          </strong>
          <span>Image model</span>
          <strong>
            {textSettings?.image_generation_model ??
              activeImageGenerationProvider?.default_image_generation_model ??
              "Not loaded"}
          </strong>
          <span>Image status</span>
          <strong>
            {activeImageGenerationProvider?.configured ? "Ready" : "Key required"}
          </strong>
        </div>
      </section>

      <div className="agent-config-stack">
        <AgentIdentityPanel
          agent={agent}
          canEdit={canEdit}
          isLoading={isAgentLoading}
          isSaving={isAgentSaving}
          key={agent?.id ?? "loading-agent"}
          onSave={saveAgentIdentity}
          settingsError={agentError}
        />
        <AgentConfigPanel
          canEdit={canEdit}
          imageGenerationSettingsError={imageGenerationSettingsError}
          isImageGenerationSaving={isImageGenerationSaving}
          isTextLoading={isTextLoading}
          isTextSaving={isTextSaving}
          isVoiceLoading={isVoiceLoading}
          isVoiceSaving={isVoiceSaving}
          onSaveImageGeneration={onSaveImageGeneration}
          onSaveText={onSaveText}
          onSaveVoice={onSaveVoice}
          providerSettings={providerSettings}
          settings={settings}
          settingsError={settingsError}
          textProviderSettings={textProviderSettings}
          textSettings={textSettings}
          textSettingsError={textSettingsError}
        />
      </div>
    </div>
  );
}

function AgentIdentityPanel({
  agent,
  canEdit,
  isLoading,
  isSaving,
  onSave,
  settingsError,
}: {
  agent: Agent | null;
  canEdit: boolean;
  isLoading: boolean;
  isSaving: boolean;
  onSave: (values: AgentIdentityFormValues) => Promise<boolean>;
  settingsError: string | null;
}) {
  const [displayName, setDisplayName] = useState(agent?.display_name ?? "");
  const [description, setDescription] = useState(agent?.description ?? "");

  const canSubmit =
    canEdit && !isLoading && !isSaving && displayName.trim() !== "" && agent !== null;

  const submit = async () => {
    if (!canSubmit) {
      return;
    }
    await onSave({
      description,
      displayName,
    });
  };

  return (
    <section className="settings-panel">
      <div className="section-title">
        <p className="eyebrow">Identity</p>
        <h2>{agent?.display_name ?? "Default agent"}</h2>
      </div>

      <div className="identity-summary">
        <span>Agent ID</span>
        <strong>{agent?.id ?? "Loading"}</strong>
        <span>Status</span>
        <strong>{agent?.status ?? "active"}</strong>
      </div>

      <form
        className="settings-form"
        onSubmit={(event: FormEvent<HTMLFormElement>) => {
          event.preventDefault();
          void submit();
        }}
      >
        <TextField
          disabled={!canEdit || isLoading || isSaving}
          label="Display name"
          onChange={setDisplayName}
          placeholder="Agent name"
          value={displayName}
        />
        <TextAreaField
          disabled={!canEdit || isLoading || isSaving}
          label="Description"
          onChange={setDescription}
          placeholder="Personal voice agent"
          value={description}
        />
        <button className="primary" disabled={!canSubmit} type="submit">
          {isSaving ? "Saving" : "Save identity"}
        </button>
      </form>

      {settingsError !== null ? <p className="error-text">{settingsError}</p> : null}
    </section>
  );
}

function AgentConfigPanel({
  canEdit,
  imageGenerationSettingsError,
  isImageGenerationSaving,
  isTextLoading,
  isTextSaving,
  isVoiceLoading,
  isVoiceSaving,
  onSaveImageGeneration,
  onSaveText,
  onSaveVoice,
  providerSettings,
  settings,
  settingsError,
  textProviderSettings,
  textSettings,
  textSettingsError,
}: {
  canEdit: boolean;
  imageGenerationSettingsError: string | null;
  isImageGenerationSaving: boolean;
  isTextLoading: boolean;
  isTextSaving: boolean;
  isVoiceLoading: boolean;
  isVoiceSaving: boolean;
  onSaveImageGeneration: (
    values: ImageGenerationPreferencesFormValues,
  ) => Promise<boolean>;
  onSaveText: (values: TextPreferencesFormValues) => Promise<boolean>;
  onSaveVoice: (values: VoicePreferencesFormValues) => Promise<boolean>;
  providerSettings: VoiceProviderSettings | null;
  settings: VoiceSettings | null;
  settingsError: string | null;
  textProviderSettings: TextProviderSettings | null;
  textSettings: TextSettings | null;
  textSettingsError: string | null;
}) {
  return (
    <section className="settings-panel">
      <div className="section-title">
        <p className="eyebrow">Agent</p>
        <h2>Configuration</h2>
      </div>

      <div className="agent-config-sections">
        <VoiceDefaultsSection
          canEdit={canEdit}
          isLoading={isVoiceLoading}
          isSaving={isVoiceSaving}
          onSave={onSaveVoice}
          providerSettings={providerSettings}
          settings={settings}
          settingsError={settingsError}
        />
        <TextDefaultsSection
          canEdit={canEdit}
          isLoading={isTextLoading}
          isSaving={isTextSaving}
          onSave={onSaveText}
          providerSettings={textProviderSettings}
          settings={textSettings}
          settingsError={textSettingsError}
        />
        <ImageGenerationDefaultsSection
          canEdit={canEdit}
          isLoading={isTextLoading}
          isSaving={isImageGenerationSaving}
          onSave={onSaveImageGeneration}
          providerSettings={textProviderSettings}
          settings={textSettings}
          settingsError={imageGenerationSettingsError}
        />
      </div>
    </section>
  );
}

function VoiceDefaultsSection({
  canEdit,
  isLoading,
  isSaving,
  onSave,
  providerSettings,
  settings,
  settingsError,
}: {
  canEdit: boolean;
  isLoading: boolean;
  isSaving: boolean;
  onSave: (values: VoicePreferencesFormValues) => Promise<boolean>;
  providerSettings: VoiceProviderSettings | null;
  settings: VoiceSettings | null;
  settingsError: string | null;
}) {
  const initialProvider =
    settings?.provider ?? providerSettings?.active_provider ?? "openai_realtime";
  const [selectedProvider, setSelectedProvider] =
    useState<VoiceProvider>(initialProvider);
  const [model, setModel] = useState(settings?.model ?? DEFAULT_MODEL);
  const [voice, setVoice] = useState(settings?.voice ?? DEFAULT_VOICE);
  const [preferredLanguage, setPreferredLanguage] = useState(
    settings?.preferred_language ?? DEFAULT_PREFERRED_LANGUAGE,
  );
  const [agentInstructions, setAgentInstructions] = useState(
    settings?.agent_instructions ?? DEFAULT_AGENT_INSTRUCTIONS,
  );
  const [initialReplyInstructions, setInitialReplyInstructions] = useState(
    settings?.initial_reply_instructions ?? DEFAULT_INITIAL_REPLY,
  );
  const configuredProviders = (providerSettings?.providers ?? []).filter(
    (provider) => provider.configured,
  );
  const selectedProviderSummary =
    providerSettings?.providers.find(
      (provider) => provider.provider === selectedProvider,
    ) ?? null;
  const configuredProviderOptions = configuredProviders.map((provider) => ({
    label: provider.label,
    value: provider.provider,
  }));
  const selectedProviderHasOption = configuredProviderOptions.some(
    (option) => option.value === selectedProvider,
  );
  const providerOptions = selectedProviderHasOption
    ? configuredProviderOptions
    : [
        {
          disabled: true,
          label:
            selectedProviderSummary === null
              ? "Save a provider key first"
              : `${selectedProviderSummary.label} key required`,
          value: selectedProvider,
        },
        ...configuredProviderOptions,
      ];
  const modelOptions = selectableModelOptions(
    model,
    selectedProviderSummary?.model_options ?? [],
    selectedProviderSummary?.default_model ?? DEFAULT_MODEL,
    selectedProviderSummary?.suggested_models ?? [],
  );
  const selectedModelOption =
    selectedProviderSummary?.model_options.find(
      (option) => option.model === model,
    ) ?? null;
  const voiceOptions = selectableOptions(
    voice,
    selectedProviderSummary?.suggested_voices ?? [],
    selectedProviderSummary?.default_voice ?? DEFAULT_VOICE,
  );
  const hasCredential = selectedProviderSummary?.configured === true;
  const canSubmit =
    canEdit &&
    !isLoading &&
    !isSaving &&
    hasCredential &&
    model.trim() !== "" &&
    voice.trim() !== "" &&
    preferredLanguage.trim() !== "";

  const updateSelectedProvider = (providerValue: string) => {
    const nextProvider = providerValue as VoiceProvider;
    const nextProviderSummary =
      providerSettings?.providers.find(
        (summary) => summary.provider === nextProvider,
      ) ?? null;

    setSelectedProvider(nextProvider);
    setModel(
      settings?.provider === nextProvider
        ? settings.model
        : nextProviderSummary?.default_model ?? DEFAULT_MODEL,
    );
    setVoice(
      settings?.provider === nextProvider
        ? settings.voice
        : nextProviderSummary?.default_voice ?? DEFAULT_VOICE,
    );
  };

  const submit = async () => {
    if (!canSubmit) {
      return;
    }

    await onSave({
      agentInstructions: agentInstructions.trim(),
      initialReplyInstructions: initialReplyInstructions.trim(),
      model: model.trim(),
      preferredLanguage: preferredLanguage.trim(),
      provider: selectedProvider,
      voice: voice.trim(),
    });
  };

  return (
    <section className="agent-config-section">
      <div className="section-heading-row">
        <div className="section-title">
          <p className="eyebrow">Voice</p>
          <span className="provider-title-row">
            {selectedProviderSummary !== null ? (
              <ServiceLogo
                decorative
                label={selectedProviderSummary.label}
                logo={selectedProviderSummary.logo}
                size="sm"
              />
            ) : null}
            <h3>Voice defaults</h3>
          </span>
        </div>
        <span className={`status-chip ${hasCredential ? "status-connected" : ""}`}>
          {hasCredential ? "Ready" : "Key required"}
        </span>
      </div>

      {!hasCredential ? (
        <div className="setup-status">
          <span>Save a provider key in Providers before saving voice defaults.</span>
        </div>
      ) : null}

      <form
        className="settings-form"
        onSubmit={(event: FormEvent<HTMLFormElement>) => {
          event.preventDefault();
          void submit();
        }}
      >
        <SelectField
          disabled={
            !canEdit ||
            isLoading ||
            isSaving ||
            configuredProviders.length === 0
          }
          label="Provider"
          onChange={updateSelectedProvider}
          options={providerOptions}
          value={selectedProvider}
        />
        <div className="two-col">
          <SelectField
            disabled={!canEdit || isLoading || isSaving || !hasCredential}
            label="Model"
            onChange={setModel}
            options={modelOptions}
            value={model}
          />
          <SelectField
            disabled={!canEdit || isLoading || isSaving || !hasCredential}
            label="Voice"
            onChange={setVoice}
            options={voiceOptions}
            value={voice}
          />
        </div>
        <ModelOptionDetails option={selectedModelOption} />
        <TextField
          disabled={!canEdit || isSaving}
          label="Preferred language"
          onChange={setPreferredLanguage}
          placeholder={DEFAULT_PREFERRED_LANGUAGE}
          value={preferredLanguage}
        />
        <TextAreaField
          disabled={!canEdit || isSaving}
          label="Agent instructions"
          onChange={setAgentInstructions}
          placeholder={DEFAULT_AGENT_INSTRUCTIONS}
          value={agentInstructions}
        />
        <TextAreaField
          disabled={!canEdit || isSaving}
          label="Initial reply"
          onChange={setInitialReplyInstructions}
          placeholder={DEFAULT_INITIAL_REPLY}
          value={initialReplyInstructions}
        />
        <button className="primary" disabled={!canSubmit} type="submit">
          {isSaving ? "Saving" : "Save voice defaults"}
        </button>
      </form>

      {settingsError !== null ? <p className="error-text">{settingsError}</p> : null}
    </section>
  );
}

function TextDefaultsSection({
  canEdit,
  isLoading,
  isSaving,
  onSave,
  providerSettings,
  settings,
  settingsError,
}: {
  canEdit: boolean;
  isLoading: boolean;
  isSaving: boolean;
  onSave: (values: TextPreferencesFormValues) => Promise<boolean>;
  providerSettings: TextProviderSettings | null;
  settings: TextSettings | null;
  settingsError: string | null;
}) {
  const initialProvider =
    settings?.provider ?? providerSettings?.active_provider ?? "openai";
  const [selectedProvider, setSelectedProvider] =
    useState<TextProvider>(initialProvider);
  const [model, setModel] = useState(settings?.model ?? DEFAULT_TEXT_MODEL);
  const [preferredLanguage, setPreferredLanguage] = useState(
    settings?.preferred_language ?? DEFAULT_TEXT_PREFERRED_LANGUAGE,
  );
  const [agentInstructions, setAgentInstructions] = useState(
    settings?.agent_instructions ?? DEFAULT_TEXT_AGENT_INSTRUCTIONS,
  );
  const [allowedSubagentModels, setAllowedSubagentModels] = useState<string[]>(
    settings?.allowed_subagent_models ?? [],
  );
  const [subagentModel, setSubagentModel] = useState(
    settings?.subagent_model ?? DEFAULT_TEXT_MODEL,
  );
  const configuredProviders = (providerSettings?.providers ?? []).filter(
    (provider) => provider.configured,
  );
  const selectedProviderSummary =
    providerSettings?.providers.find(
      (provider) => provider.provider === selectedProvider,
    ) ?? null;
  const configuredProviderOptions = configuredProviders.map((provider) => ({
    label: provider.label,
    value: provider.provider,
  }));
  const selectedProviderHasOption = configuredProviderOptions.some(
    (option) => option.value === selectedProvider,
  );
  const providerOptions = selectedProviderHasOption
    ? configuredProviderOptions
    : [
        {
          disabled: true,
          label:
            selectedProviderSummary === null
              ? "Save a provider key first"
              : `${selectedProviderSummary.label} key required`,
          value: selectedProvider,
        },
        ...configuredProviderOptions,
      ];
  const modelOptions = selectableModelOptions(
    model,
    selectedProviderSummary?.model_options ?? [],
    selectedProviderSummary?.default_model ?? DEFAULT_TEXT_MODEL,
  );
  const selectedModelOption =
    selectedProviderSummary?.model_options.find(
      (option) => option.model === model,
    ) ?? null;
  const effectiveAllowedSubagentModels =
    allowedSubagentModels.length > 0
      ? allowedSubagentModels
      : modelIds(selectedProviderSummary?.model_options ?? []);
  const subagentModelOptions = selectableModelOptions(
    subagentModel,
    selectedProviderSummary?.model_options ?? [],
    selectedProviderSummary?.default_subagent_model ?? DEFAULT_TEXT_MODEL,
  );
  const selectedSubagentModelOption =
    selectedProviderSummary?.model_options.find(
      (option) => option.model === subagentModel,
    ) ?? null;
  const hasCredential = selectedProviderSummary?.configured === true;
  const canSubmit =
    canEdit &&
    !isLoading &&
    !isSaving &&
    hasCredential &&
    model.trim() !== "" &&
    effectiveAllowedSubagentModels.length > 0 &&
    effectiveAllowedSubagentModels.includes(subagentModel) &&
    preferredLanguage.trim() !== "" &&
    agentInstructions.trim() !== "";

  const updateSelectedProvider = (providerValue: string) => {
    const nextProvider = providerValue as TextProvider;
    const nextProviderSummary =
      providerSettings?.providers.find(
        (summary) => summary.provider === nextProvider,
      ) ?? null;

    setSelectedProvider(nextProvider);
    setModel(
      settings?.provider === nextProvider
        ? settings.model
        : nextProviderSummary?.default_model ?? DEFAULT_TEXT_MODEL,
    );
    setAllowedSubagentModels(modelIds(nextProviderSummary?.model_options ?? []));
    setSubagentModel(
      nextProviderSummary?.default_subagent_model ?? DEFAULT_TEXT_MODEL,
    );
  };

  const toggleAllowedSubagentModel = (modelId: string) => {
    const nextModels = effectiveAllowedSubagentModels.includes(modelId)
      ? effectiveAllowedSubagentModels.filter((value) => value !== modelId)
      : [...effectiveAllowedSubagentModels, modelId];
    if (nextModels.length === 0) {
      return;
    }
    setAllowedSubagentModels(nextModels);
    if (!nextModels.includes(subagentModel)) {
      setSubagentModel(nextModels[0]);
    }
  };

  const updateSubagentModel = (modelId: string) => {
    setSubagentModel(modelId);
    if (!effectiveAllowedSubagentModels.includes(modelId)) {
      setAllowedSubagentModels([...effectiveAllowedSubagentModels, modelId]);
    }
  };

  const submit = async () => {
    if (!canSubmit) {
      return;
    }

    await onSave({
      agentInstructions: agentInstructions.trim(),
      allowedSubagentModels: effectiveAllowedSubagentModels,
      model: model.trim(),
      preferredLanguage: preferredLanguage.trim(),
      provider: selectedProvider,
      subagentModel,
      temperature:
        settings?.provider === selectedProvider ? settings.temperature : null,
    });
  };

  return (
    <section className="agent-config-section">
      <div className="section-heading-row">
        <div className="section-title">
          <p className="eyebrow">Text</p>
          <span className="provider-title-row">
            {selectedProviderSummary !== null ? (
              <ServiceLogo
                decorative
                label={selectedProviderSummary.label}
                logo={selectedProviderSummary.logo}
                size="sm"
              />
            ) : null}
            <h3>Text defaults</h3>
          </span>
        </div>
        <span className={`status-chip ${hasCredential ? "status-connected" : ""}`}>
          {hasCredential ? "Ready" : "Key required"}
        </span>
      </div>

      {!hasCredential ? (
        <div className="setup-status">
          <span>Configure this provider before saving text defaults.</span>
        </div>
      ) : null}

      <form
        className="settings-form"
        onSubmit={(event: FormEvent<HTMLFormElement>) => {
          event.preventDefault();
          void submit();
        }}
      >
        <SelectField
          disabled={
            !canEdit ||
            isLoading ||
            isSaving ||
            configuredProviders.length === 0
          }
          label="Provider"
          onChange={updateSelectedProvider}
          options={providerOptions}
          value={selectedProvider}
        />
        <SelectField
          disabled={!canEdit || isLoading || isSaving || !hasCredential}
          label="Model"
          onChange={setModel}
          options={modelOptions}
          value={model}
        />
        <ModelOptionDetails option={selectedModelOption} />
        <fieldset className="model-allowlist">
          <legend>Allowed subagent models</legend>
          <p>
            The agent may choose only from these models when it delegates a
            task. Keep a smaller model enabled for routine work and the
            flagship enabled for difficult work.
          </p>
          <div className="model-allowlist-options">
            {(selectedProviderSummary?.model_options ?? []).map((option) => (
              <label key={option.model}>
                <input
                  checked={effectiveAllowedSubagentModels.includes(option.model)}
                  disabled={!canEdit || isSaving}
                  onChange={() => {
                    toggleAllowedSubagentModel(option.model);
                  }}
                  type="checkbox"
                />
                <span>
                  <strong>{option.label}</strong>
                  <small>{option.description}</small>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <SelectField
          disabled={!canEdit || isLoading || isSaving || !hasCredential}
          label="Default subagent model"
          onChange={updateSubagentModel}
          options={subagentModelOptions}
          value={subagentModel}
        />
        <ModelOptionDetails option={selectedSubagentModelOption} />
        <TextField
          disabled={!canEdit || isSaving}
          label="Preferred language"
          onChange={setPreferredLanguage}
          placeholder={DEFAULT_TEXT_PREFERRED_LANGUAGE}
          value={preferredLanguage}
        />
        <TextAreaField
          disabled={!canEdit || isSaving}
          label="Agent instructions"
          onChange={setAgentInstructions}
          placeholder={DEFAULT_TEXT_AGENT_INSTRUCTIONS}
          value={agentInstructions}
        />
        <button className="primary" disabled={!canSubmit} type="submit">
          {isSaving ? "Saving" : "Save text defaults"}
        </button>
      </form>

      {settingsError !== null ? <p className="error-text">{settingsError}</p> : null}
    </section>
  );
}

function selectableOptions(
  selectedValue: string,
  suggestedValues: readonly string[],
  defaultValue: string,
): { label: string; value: string }[] {
  return Array.from(
    new Set(
      [selectedValue, defaultValue, ...suggestedValues]
        .map((value) => value.trim())
        .filter((value) => value !== ""),
    ),
  ).map((value) => ({
    label: value,
    value,
  }));
}

function ImageGenerationDefaultsSection({
  canEdit,
  isLoading,
  isSaving,
  onSave,
  providerSettings,
  settings,
  settingsError,
}: {
  canEdit: boolean;
  isLoading: boolean;
  isSaving: boolean;
  onSave: (values: ImageGenerationPreferencesFormValues) => Promise<boolean>;
  providerSettings: TextProviderSettings | null;
  settings: TextSettings | null;
  settingsError: string | null;
}) {
  const initialProvider =
    settings?.image_generation_provider ??
    providerSettings?.active_provider ??
    "openai";
  const initialProviderSummary =
    providerSettings?.providers.find(
      (provider) => provider.provider === initialProvider,
    ) ?? null;
  const [selectedProvider, setSelectedProvider] =
    useState<TextProvider>(initialProvider);
  const [model, setModel] = useState(
    settings?.image_generation_model ??
      initialProviderSummary?.default_image_generation_model ??
      "",
  );
  const selectedProviderSummary =
    providerSettings?.providers.find(
      (provider) => provider.provider === selectedProvider,
    ) ?? null;
  const providerOptions = (providerSettings?.providers ?? [])
    .filter((provider) => provider.image_generation_model_options.length > 0)
    .map((provider) => ({
      label: provider.configured
        ? provider.label
        : `${provider.label} · key required`,
      value: provider.provider,
    }));
  const modelOptions = selectableModelOptions(
    model,
    selectedProviderSummary?.image_generation_model_options ?? [],
    selectedProviderSummary?.default_image_generation_model ?? "",
  );
  const selectedModelOption =
    selectedProviderSummary?.image_generation_model_options.find(
      (option) => option.model === model,
    ) ?? null;
  const hasCredential = selectedProviderSummary?.configured === true;
  const canSubmit =
    canEdit &&
    !isLoading &&
    !isSaving &&
    hasCredential &&
    selectedModelOption !== null;

  const updateSelectedProvider = (providerValue: string) => {
    const nextProvider = providerValue as TextProvider;
    const nextProviderSummary =
      providerSettings?.providers.find(
        (provider) => provider.provider === nextProvider,
      ) ?? null;

    setSelectedProvider(nextProvider);
    setModel(
      settings?.image_generation_provider === nextProvider
        ? settings.image_generation_model
        : nextProviderSummary?.default_image_generation_model ?? "",
    );
  };

  const submit = async () => {
    if (!canSubmit) {
      return;
    }
    await onSave({ model, provider: selectedProvider });
  };

  return (
    <section className="agent-config-section">
      <div className="section-heading-row">
        <div className="section-title">
          <p className="eyebrow">Images</p>
          <span className="provider-title-row">
            {selectedProviderSummary !== null ? (
              <ServiceLogo
                decorative
                label={selectedProviderSummary.label}
                logo={selectedProviderSummary.logo}
                size="sm"
              />
            ) : null}
            <h3>Image generation defaults</h3>
          </span>
        </div>
        <span className={`status-chip ${hasCredential ? "status-connected" : ""}`}>
          {hasCredential ? "Ready" : "Key required"}
        </span>
      </div>

      {!hasCredential ? (
        <div className="setup-status">
          <span>Review the catalog, then save a provider key to use this selection.</span>
        </div>
      ) : null}

      <form
        className="settings-form"
        onSubmit={(event: FormEvent<HTMLFormElement>) => {
          event.preventDefault();
          void submit();
        }}
      >
        <SelectField
          disabled={
            !canEdit ||
            isLoading ||
            isSaving ||
            providerOptions.length === 0
          }
          label="Image provider"
          onChange={updateSelectedProvider}
          options={providerOptions}
          value={selectedProvider}
        />
        <SelectField
          disabled={
            !canEdit ||
            isLoading ||
            isSaving ||
            selectedProviderSummary === null
          }
          label="Image model"
          onChange={setModel}
          options={modelOptions}
          value={model}
        />
        <ImageGenerationModelOptionDetails option={selectedModelOption} />
        <button className="primary" disabled={!canSubmit} type="submit">
          {isSaving ? "Saving" : "Save image defaults"}
        </button>
      </form>

      {settingsError !== null ? <p className="error-text">{settingsError}</p> : null}
    </section>
  );
}

function ModelOptionDetails({
  option,
}: {
  option: ProviderModelOption | null;
}) {
  if (option === null) {
    return null;
  }
  const capabilities = [
    option.supports_image_input ? "images" : null,
    option.supports_tool_calling ? "tool calling" : null,
    option.supports_browser_use ? "browser use" : null,
  ].filter((capability): capability is string => capability !== null);
  return (
    <div className="model-option-details">
      <strong>
        {option.label}
        {option.recommended ? " · Recommended" : ""}
      </strong>
      <span>{option.description}</span>
      {capabilities.length > 0 ? (
        <small>Capabilities: {capabilities.join(" · ")}</small>
      ) : null}
      {option.pricing_summary !== null ? (
        <small>{option.pricing_summary}</small>
      ) : null}
    </div>
  );
}

function ImageGenerationModelOptionDetails({
  option,
}: {
  option: ProviderImageGenerationModelOption | null;
}) {
  if (option === null) {
    return null;
  }
  return (
    <div className="model-option-details">
      <strong>
        {option.label}
        {option.recommended ? " · Recommended" : ""}
      </strong>
      <span>{option.description}</span>
      {option.hosted_api_supports_image_editing ? (
        <small>Hosted API: generation and image editing</small>
      ) : option.supports_image_editing ? (
        <small>Hosted API: generation only · self-hosted model can edit</small>
      ) : (
        <small>Hosted API: image generation</small>
      )}
      {option.pricing_summary !== null ? <small>{option.pricing_summary}</small> : null}
      <a href={option.docs_url} rel="noreferrer" target="_blank">
        Model documentation
      </a>
    </div>
  );
}

function activeTextProviderSummary(
  providerSettings: TextProviderSettings | null,
  settings: TextSettings | null,
): TextProviderSummary | null {
  if (providerSettings === null) {
    return null;
  }

  const activeProvider = settings?.provider ?? providerSettings.active_provider;
  return (
    providerSettings.providers.find(
      (provider) => provider.provider === activeProvider,
    ) ?? null
  );
}

function imageGenerationProviderSummary(
  providerSettings: TextProviderSettings | null,
  settings: TextSettings | null,
): TextProviderSummary | null {
  if (providerSettings === null) {
    return null;
  }
  const provider =
    settings?.image_generation_provider ?? providerSettings.active_provider;
  return (
    providerSettings.providers.find(
      (providerSummary) => providerSummary.provider === provider,
    ) ?? null
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
