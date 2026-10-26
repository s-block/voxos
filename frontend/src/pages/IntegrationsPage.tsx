import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronRight,
  ExternalLink,
  PlugZap,
  RefreshCw,
  Save,
  Unplug,
} from "lucide-react";
import type { LogEntry } from "../appTypes";
import {
  connectTwilioResources,
  disconnectIntegration,
  loadIntegrationCallActivity,
  loadIntegrationMessageActivity,
  loadIntegrationResources,
  loadIntegrations,
  repairTelnyxIntegration,
  setupTelnyxIntegration,
  setupTwilioIntegration,
  verifyTelnyxIntegration,
  type BrandLogo,
  type Integration,
  type IntegrationCapability,
  type IntegrationProviderType,
  type IntegrationCallActivity,
  type IntegrationMessageActivity,
  type IntegrationResource,
  type IntegrationResourceType,
  type TelnyxPhoneSetupRequest,
  type TelnyxReadinessResponse,
  type TwilioCredentialSetupResponse,
  type TwilioPhoneNumberCandidate,
} from "../api";
import { TextField } from "../components/FormFields";
import { ServiceLogo } from "../components/ServiceLogo";
import { WHATSAPP_LOGO } from "../serviceLogos";
import { WhatsAppIntegrationPanel } from "./WhatsAppIntegrationPanel";

const DEFAULT_TWILIO_CAPABILITIES: IntegrationCapability[] = [
  "inbound_voice",
  "outbound_voice",
  "sms_read",
  "sms_send",
];

const PROVIDER_LABELS: Record<IntegrationProviderType, string> = {
  telnyx: "Telnyx",
  twilio: "Twilio",
  whatsapp: "WhatsApp",
};

const PHONE_PROVIDER_LOGOS: Record<IntegrationProviderType, BrandLogo> = {
  telnyx: {
    domain: "telnyx.com",
    label: "Telnyx",
    source: "domain",
  },
  twilio: {
    domain: "twilio.com",
    label: "Twilio",
    source: "domain",
  },
  whatsapp: WHATSAPP_LOGO,
};

const PHONE_RESOURCE_TYPES = new Set<IntegrationResourceType>(["phone_number"]);

type GuideLink = {
  href: string;
  label: string;
};

type PhoneProviderGuide = {
  summary: string;
  tenantNote: string;
  steps: string[];
  links: GuideLink[];
};

const TELNYX_GUIDE: PhoneProviderGuide = {
  summary:
    "Telnyx setup connects one number end to end. VoxOS validates the API key, creates a dedicated Voice API application, outbound voice profile, and messaging profile, assigns the number to those provider-side objects, verifies signed webhooks, and streams calls into LiveKit.",
  tenantNote:
    "The API key and generated media-stream auth token are encrypted under the signed-in VoxOS user. Provider objects are dedicated to this VoxOS phone number, and webhooks resolve through per-resource route keys.",
  steps: [
    "In Telnyx Mission Control, buy a number with the voice and SMS capabilities you need.",
    "Create a Telnyx API v2 key and copy the webhook public key from Telnyx account settings.",
    "Paste the number, API key, and public key here.",
    "Confirm number assignment. VoxOS will create dedicated Telnyx routing objects and attach the number to them.",
    "Keep LiveKit configured in Connection so calls have a realtime voice runtime.",
    "If you need outbound calls or SMS to more countries, adjust the created Telnyx profiles after setup.",
  ],
  links: [
    {
      href: "https://developers.telnyx.com/docs/numbers/phone-numbers/buy-phone-number",
      label: "Buy a Telnyx number",
    },
    {
      href: "https://developers.telnyx.com/development/api-fundamentals/create-api-keys",
      label: "Create Telnyx API keys",
    },
    {
      href: "https://developers.telnyx.com/docs/voice/programmable-voice/media-streaming",
      label: "Telnyx media streaming",
    },
    {
      href: "https://developers.telnyx.com/docs/messaging/messages/configuration-and-usage",
      label: "Messaging profiles",
    },
  ],
};

const DEFAULT_TELNYX_FORM: TelnyxFormState = {
  apiKey: "",
  confirmNumberAssignment: false,
  label: "Telnyx",
  phoneNumber: "",
  transcriptStorageEnabled: false,
  webhookPublicKey: "",
};

const E164_PHONE_PATTERN = /^\+[1-9]\d{1,14}$/;

const TWILIO_GUIDE: PhoneProviderGuide = {
  summary:
    "Twilio setup validates the credentials, lists your Twilio numbers, and writes the voice/SMS/status webhook URLs after you select a number.",
  tenantNote:
    "Twilio credentials are encrypted and scoped to the signed-in VoxOS user. Twilio webhooks route back to a per-user integration resource, not to a shared global account.",
  steps: [
    "In Twilio, buy a number with Voice and SMS capabilities.",
    "Create or copy the Account SID, API Key SID, API Key Secret, and Auth Token.",
    "Save the credentials here. VoxOS will list eligible Twilio numbers from that account.",
    "Select the number, confirm webhook replacement, and connect it.",
    "Keep the LiveKit Connection settings configured separately for the realtime voice runtime.",
  ],
  links: [
    {
      href: "https://www.twilio.com/docs/phone-numbers",
      label: "Twilio phone numbers",
    },
    {
      href: "https://help.twilio.com/articles/223135247-How-to-Search-for-and-Buy-a-Twilio-Phone-Number-from-Console",
      label: "Buy a Twilio number",
    },
  ],
};

type IntegrationsPageProps = {
  canEdit: boolean;
  onLog: (message: string, level?: LogEntry["level"]) => void;
};

type IntegrationWithResources = {
  integration: Integration;
  resources: IntegrationResource[];
};

type TwilioFormState = {
  label: string;
  accountSid: string;
  apiKeySid: string;
  apiKeySecret: string;
  authToken: string;
};

type TelnyxFormState = {
  apiKey: string;
  confirmNumberAssignment: boolean;
  label: string;
  phoneNumber: string;
  transcriptStorageEnabled: boolean;
  webhookPublicKey: string;
};

export function IntegrationsPage({ canEdit, onLog }: IntegrationsPageProps) {
  const [items, setItems] = useState<IntegrationWithResources[]>([]);
  const [setupResult, setSetupResult] =
    useState<TwilioCredentialSetupResponse | null>(null);
  const [selectedNumbers, setSelectedNumbers] = useState<Record<string, boolean>>({});
  const [transcriptStorage, setTranscriptStorage] = useState<Record<string, boolean>>({});
  const [overwriteConfirmed, setOverwriteConfirmed] = useState(false);
  const [form, setForm] = useState<TwilioFormState>({
    label: "Twilio",
    accountSid: "",
    apiKeySid: "",
    apiKeySecret: "",
    authToken: "",
  });
  const [telnyxForm, setTelnyxForm] = useState<TelnyxFormState>({
    ...DEFAULT_TELNYX_FORM,
  });
  const [isSavingTelnyx, setIsSavingTelnyx] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const integrations = await loadIntegrations();
      const resources = await Promise.all(
        integrations.map(async (integration) => ({
          integration,
          resources: await loadIntegrationResources(integration.id),
        })),
      );
      setItems(resources);
    } catch (loadError) {
      const message = errorMessage(loadError);
      setError(message);
      onLog(`Integration load failed: ${message}`, "error");
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

  const selectedCandidates = useMemo(
    () =>
      (setupResult?.phone_numbers ?? []).filter(
        (candidate) => selectedNumbers[candidate.sid] === true,
      ),
    [selectedNumbers, setupResult],
  );
  const canSubmitSetup =
    canEdit &&
    !isSaving &&
    form.accountSid.trim() !== "" &&
    form.apiKeySid.trim() !== "" &&
    form.apiKeySecret.trim() !== "" &&
    form.authToken.trim() !== "";
  const canConnect =
    canEdit && !isSaving && overwriteConfirmed && selectedCandidates.length > 0;
  const connectedPhoneNumberCounts = useMemo(
    () => phoneNumberCountsByProvider(items),
    [items],
  );
  const carrierItems = useMemo(
    () => items.filter((item) => item.integration.provider !== "whatsapp"),
    [items],
  );
  const whatsappIntegrationId = useMemo(
    () =>
      items.find((item) => item.integration.provider === "whatsapp")
        ?.integration.id ?? null,
    [items],
  );

  const submitSetup = async () => {
    if (!canSubmitSetup) {
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      const response = await setupTwilioIntegration({
        account_sid: form.accountSid.trim(),
        api_key_secret: form.apiKeySecret.trim(),
        api_key_sid: form.apiKeySid.trim(),
        auth_token: form.authToken.trim(),
        label: form.label.trim() || "Twilio",
      });
      setSetupResult(response);
      setSelectedNumbers({});
      setTranscriptStorage({});
      setOverwriteConfirmed(false);
      setForm((current) => ({
        ...current,
        apiKeySecret: "",
        authToken: "",
      }));
      onLog("Twilio credentials saved.");
      await refresh();
    } catch (setupError) {
      const message = errorMessage(setupError);
      setError(message);
      onLog(`Twilio setup failed: ${message}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  const connectSelected = async () => {
    if (setupResult === null || !canConnect) {
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      await connectTwilioResources(setupResult.integration.id, {
        overwrite_existing_webhooks: true,
        resources: selectedCandidates.map((candidate) => ({
          capabilities: selectedCapabilities(candidate),
          phone_number_sid: candidate.sid,
          transcript_storage_enabled: transcriptStorage[candidate.sid] === true,
        })),
      });
      setSetupResult(null);
      setSelectedNumbers({});
      setTranscriptStorage({});
      setOverwriteConfirmed(false);
      onLog("Twilio numbers connected.");
      await refresh();
    } catch (connectError) {
      const message = errorMessage(connectError);
      setError(message);
      onLog(`Twilio number connection failed: ${message}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  const disconnect = async (integration: Integration) => {
    if (!canEdit || isSaving) {
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      await disconnectIntegration(integration.id);
      onLog(`${integration.label} disconnected.`);
      await refresh();
    } catch (disconnectError) {
      const message = errorMessage(disconnectError);
      setError(message);
      onLog(`Integration disconnect failed: ${message}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  const submitTelnyx = async () => {
    if (!canSubmitTelnyx({ canEdit, form: telnyxForm, isSaving })) {
      return;
    }
    setIsSavingTelnyx(true);
    setIsSaving(true);
    setError(null);
    try {
      await setupTelnyxIntegration(telnyxPhoneSetupRequest(telnyxForm));
      setTelnyxForm((current) => ({
        ...current,
        apiKey: "",
        confirmNumberAssignment: false,
      }));
      onLog("Telnyx phone provider connected.");
      await refresh();
    } catch (setupError) {
      const message = errorMessage(setupError);
      setError(message);
      onLog(`Telnyx setup failed: ${message}`, "error");
    } finally {
      setIsSavingTelnyx(false);
      setIsSaving(false);
    }
  };

  return (
    <div className="settings-page">
      <section className="settings-panel">
        <div className="section-heading-row">
          <div className="section-title">
            <p className="eyebrow">Phone</p>
            <h2>Providers</h2>
          </div>
          {isLoading ? <span className="status-chip">Loading</span> : null}
        </div>

        <TwilioProviderSetupDetails
          canEdit={canEdit}
          canSubmitSetup={canSubmitSetup}
          connectedPhoneNumberCount={connectedPhoneNumberCounts.twilio}
          form={form}
          isSaving={isSaving}
          onFormChange={(updates) =>
            setForm((current) => ({ ...current, ...updates }))
          }
          onSubmit={submitSetup}
        />
        <TelnyxProviderSetupDetails
          canEdit={canEdit}
          connectedPhoneNumberCount={connectedPhoneNumberCounts.telnyx}
          form={telnyxForm}
          isSaving={isSaving}
          isSubmitting={isSavingTelnyx}
          onFormChange={(updates) =>
            setTelnyxForm((current) => ({ ...current, ...updates }))
          }
          onSubmit={submitTelnyx}
        />
        {error !== null ? <p className="error-text">{error}</p> : null}
      </section>

      {setupResult !== null ? (
        <TwilioResourceSelectionPanel
          canConnect={canConnect}
          candidates={setupResult.phone_numbers}
          isSaving={isSaving}
          onConnect={connectSelected}
          onSelect={(sid, checked) =>
            setSelectedNumbers((current) => ({ ...current, [sid]: checked }))
          }
          onSetOverwrite={setOverwriteConfirmed}
          onSetTranscriptStorage={(sid, checked) =>
            setTranscriptStorage((current) => ({ ...current, [sid]: checked }))
          }
          overwriteConfirmed={overwriteConfirmed}
          selectedNumbers={selectedNumbers}
          transcriptStorage={transcriptStorage}
        />
      ) : null}

      <WhatsAppIntegrationPanel
        canEdit={canEdit}
        integrationId={whatsappIntegrationId}
        isIntegrationListLoading={isLoading}
        onIntegrationChange={refresh}
        onLog={onLog}
      />

      <section className="settings-panel">
        <div className="section-heading-row">
          <div className="section-title">
            <p className="eyebrow">Connected</p>
            <h2>Phone numbers</h2>
          </div>
          <button disabled={isLoading || isSaving} onClick={() => void refresh()} type="button">
            <RefreshCw aria-hidden="true" size={16} />
            Refresh
          </button>
        </div>
        {carrierItems.length > 0 ? (
          <div className="phone-integration-list">
            {carrierItems.map((item, index) => (
              <PhoneIntegrationCard
                canEdit={canEdit}
                initiallyExpanded={index === 0}
                integration={item.integration}
                isSaving={isSaving}
                key={item.integration.id}
                onDisconnect={disconnect}
                onLog={onLog}
                resources={item.resources}
              />
            ))}
          </div>
        ) : (
          <p className="muted">No phone integrations connected.</p>
        )}
      </section>
    </div>
  );
}

function TwilioProviderSetupDetails({
  canEdit,
  canSubmitSetup,
  connectedPhoneNumberCount,
  form,
  isSaving,
  onFormChange,
  onSubmit,
}: {
  canEdit: boolean;
  canSubmitSetup: boolean;
  connectedPhoneNumberCount: number;
  form: TwilioFormState;
  isSaving: boolean;
  onFormChange: (updates: Partial<TwilioFormState>) => void;
  onSubmit: () => Promise<void>;
}) {
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <details
      className="phone-provider-details"
      onToggle={(event) => setIsExpanded(event.currentTarget.open)}
      open={isExpanded}
    >
      <summary className="phone-provider-summary">
        <ChevronRight
          aria-hidden="true"
          className="phone-card-chevron"
          size={16}
        />
        <span className="phone-card-summary-main">
          <span className="phone-card-header">
            <span className="phone-card-title-row">
              <ServiceLogo
                label="Twilio"
                logo={PHONE_PROVIDER_LOGOS.twilio}
                size="md"
              />
              <span>
                <strong className="phone-card-title">Twilio</strong>
                <span className="phone-card-subtitle">
                  Voice, SMS, webhook routing, and media streams.
                </span>
              </span>
            </span>
            <span
              className={`status-chip ${
                connectedPhoneNumberCount > 0 ? "status-connected" : ""
              }`}
            >
              {connectedPhoneNumberCount > 0
                ? pluralize(connectedPhoneNumberCount, "number")
                : "Not connected"}
            </span>
          </span>
        </span>
      </summary>

      <form
        className="settings-form phone-provider-form"
        onSubmit={(event: FormEvent<HTMLFormElement>) => {
          event.preventDefault();
          void onSubmit();
        }}
      >
        <TextField
          disabled={!canEdit || isSaving}
          label="Label"
          onChange={(value) => onFormChange({ label: value })}
          placeholder="Twilio"
          value={form.label}
        />
        <TextField
          disabled={!canEdit || isSaving}
          label="Account SID"
          onChange={(value) => onFormChange({ accountSid: value })}
          placeholder="AC..."
          value={form.accountSid}
        />
        <TextField
          disabled={!canEdit || isSaving}
          label="API Key SID"
          onChange={(value) => onFormChange({ apiKeySid: value })}
          placeholder="SK..."
          value={form.apiKeySid}
        />
        <TextField
          disabled={!canEdit || isSaving}
          label="API Key Secret"
          onChange={(value) => onFormChange({ apiKeySecret: value })}
          placeholder="Paste secret"
          type="password"
          value={form.apiKeySecret}
        />
        <TextField
          disabled={!canEdit || isSaving}
          label="Auth Token"
          onChange={(value) => onFormChange({ authToken: value })}
          placeholder="Paste token"
          type="password"
          value={form.authToken}
        />
        <button className="primary" disabled={!canSubmitSetup} type="submit">
          <Save aria-hidden="true" size={16} />
          {isSaving ? "Saving" : "Save credentials"}
        </button>
        <PhoneProviderGuidePanel guide={TWILIO_GUIDE} />
      </form>
    </details>
  );
}

function TelnyxProviderSetupDetails({
  canEdit,
  connectedPhoneNumberCount,
  form,
  isSaving,
  isSubmitting,
  onFormChange,
  onSubmit,
}: {
  canEdit: boolean;
  connectedPhoneNumberCount: number;
  form: TelnyxFormState;
  isSaving: boolean;
  isSubmitting: boolean;
  onFormChange: (updates: Partial<TelnyxFormState>) => void;
  onSubmit: () => Promise<void>;
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  const canSubmit = canSubmitTelnyx({ canEdit, form, isSaving });

  return (
    <details
      className="phone-provider-details"
      onToggle={(event) => setIsExpanded(event.currentTarget.open)}
      open={isExpanded}
    >
      <summary className="phone-provider-summary">
        <ChevronRight
          aria-hidden="true"
          className="phone-card-chevron"
          size={16}
        />
        <span className="phone-card-summary-main">
          <span className="phone-card-header">
            <span className="phone-card-title-row">
              <ServiceLogo
                label="Telnyx"
                logo={PHONE_PROVIDER_LOGOS.telnyx}
                size="md"
              />
              <span>
                <strong className="phone-card-title">Telnyx</strong>
                <span className="phone-card-subtitle">
                  Voice, SMS, dedicated Telnyx routing, and media streams.
                </span>
              </span>
            </span>
            <span
              className={`status-chip ${
                connectedPhoneNumberCount > 0 ? "status-connected" : ""
              }`}
            >
              {connectedPhoneNumberCount > 0
                ? pluralize(connectedPhoneNumberCount, "number")
                : "Not connected"}
            </span>
          </span>
        </span>
      </summary>

      <form
        className="settings-form phone-provider-form"
        onSubmit={(event: FormEvent<HTMLFormElement>) => {
          event.preventDefault();
          void onSubmit();
        }}
      >
        <div className="two-col">
          <TextField
            disabled={!canEdit || isSaving}
            label="Label"
            onChange={(value) => onFormChange({ label: value })}
            placeholder="Telnyx"
            value={form.label}
          />
          <TextField
            disabled={!canEdit || isSaving}
            label="Phone number"
            onChange={(value) => onFormChange({ phoneNumber: value })}
            placeholder="+447418606704"
            value={form.phoneNumber}
          />
        </div>

        <div className="two-col">
          <TextField
            disabled={!canEdit || isSaving}
            label="API key"
            onChange={(value) => onFormChange({ apiKey: value })}
            placeholder="Paste Telnyx API key"
            type="password"
            value={form.apiKey}
          />
          <TextField
            disabled={!canEdit || isSaving}
            label="Webhook public key"
            onChange={(value) => onFormChange({ webhookPublicKey: value })}
            placeholder="Paste Telnyx public key"
            value={form.webhookPublicKey}
          />
        </div>

        <label className="tool-approval-row compact">
          <input
            checked={form.confirmNumberAssignment}
            disabled={isSaving}
            onChange={(event) =>
              onFormChange({ confirmNumberAssignment: event.currentTarget.checked })
            }
            type="checkbox"
          />
          <span>
            <strong>Assign this number to VoxOS Telnyx routing</strong>
            <small>
              VoxOS will create dedicated Telnyx voice and messaging profiles,
              then attach this number to those profiles.
            </small>
          </span>
        </label>

        <label className="tool-approval-row compact">
          <input
            checked={form.transcriptStorageEnabled}
            disabled={isSaving}
            onChange={(event) =>
              onFormChange({ transcriptStorageEnabled: event.currentTarget.checked })
            }
            type="checkbox"
          />
          <span>
            <strong>Store transcripts</strong>
            <small>Applies when call capture is available for this provider.</small>
          </span>
        </label>

        <button className="primary" disabled={!canSubmit} type="submit">
          <Save aria-hidden="true" size={16} />
          {isSubmitting ? "Connecting" : "Connect Telnyx"}
        </button>
        <PhoneProviderGuidePanel guide={TELNYX_GUIDE} />
      </form>
    </details>
  );
}

function PhoneProviderGuidePanel({ guide }: { guide: PhoneProviderGuide }) {
  return (
    <details className="phone-guide-details">
      <summary>Setup guide</summary>
      <div className="phone-guide-body">
        <p>{guide.summary}</p>
        <ol>
          {guide.steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
        <p>{guide.tenantNote}</p>
        <div className="phone-guide-links">
          {guide.links.map((link) => (
            <a href={link.href} key={link.href} rel="noreferrer" target="_blank">
              {link.label}
              <ExternalLink aria-hidden="true" size={13} />
            </a>
          ))}
        </div>
      </div>
    </details>
  );
}

function TwilioResourceSelectionPanel({
  canConnect,
  candidates,
  isSaving,
  onConnect,
  onSelect,
  onSetOverwrite,
  onSetTranscriptStorage,
  overwriteConfirmed,
  selectedNumbers,
  transcriptStorage,
}: {
  canConnect: boolean;
  candidates: TwilioPhoneNumberCandidate[];
  isSaving: boolean;
  onConnect: () => Promise<void>;
  onSelect: (sid: string, checked: boolean) => void;
  onSetOverwrite: (checked: boolean) => void;
  onSetTranscriptStorage: (sid: string, checked: boolean) => void;
  overwriteConfirmed: boolean;
  selectedNumbers: Record<string, boolean>;
  transcriptStorage: Record<string, boolean>;
}) {
  return (
    <section className="settings-panel">
      <div className="section-heading-row">
        <div className="section-title">
          <p className="eyebrow">Twilio numbers</p>
          <h2>Webhook setup</h2>
        </div>
      </div>
      <div className="tool-approval-grid">
        {candidates.map((candidate) => (
          <div className="tool-approval-row integration-resource-row" key={candidate.sid}>
            <input
              checked={selectedNumbers[candidate.sid] === true}
              disabled={isSaving}
              onChange={(event) => onSelect(candidate.sid, event.currentTarget.checked)}
              type="checkbox"
            />
            <span>
              <strong>{candidate.phone_number}</strong>
              <small>{webhookSummary(candidate)}</small>
            </span>
            <label className="inline-checkbox">
              <input
                checked={transcriptStorage[candidate.sid] === true}
                disabled={isSaving}
                onChange={(event) =>
                  onSetTranscriptStorage(candidate.sid, event.currentTarget.checked)
                }
                type="checkbox"
              />
              <span>Store transcripts</span>
            </label>
          </div>
        ))}
      </div>
      <label className="tool-approval-row">
        <input
          checked={overwriteConfirmed}
          disabled={isSaving}
          onChange={(event) => onSetOverwrite(event.currentTarget.checked)}
          type="checkbox"
        />
        <span>
          <strong>Replace existing Twilio webhook URLs</strong>
          <small>Previous values will be retained for disconnect cleanup.</small>
        </span>
      </label>
      <button className="primary" disabled={!canConnect} onClick={() => void onConnect()} type="button">
        <PlugZap aria-hidden="true" size={16} />
        {isSaving ? "Connecting" : "Connect selected"}
      </button>
    </section>
  );
}

function PhoneIntegrationCard({
  canEdit,
  initiallyExpanded,
  integration,
  isSaving,
  onDisconnect,
  onLog,
  resources,
}: {
  canEdit: boolean;
  initiallyExpanded: boolean;
  integration: Integration;
  isSaving: boolean;
  onDisconnect: (integration: Integration) => Promise<void>;
  onLog: (message: string, level?: LogEntry["level"]) => void;
  resources: IntegrationResource[];
}) {
  const [isExpanded, setIsExpanded] = useState(initiallyExpanded);
  const phoneNumbers = resources.filter(isPhoneNumberResource);

  return (
    <details
      className="phone-integration-card"
      onToggle={(event) => setIsExpanded(event.currentTarget.open)}
      open={isExpanded}
    >
      <summary className="phone-integration-summary">
        <ChevronRight
          aria-hidden="true"
          className="phone-card-chevron"
          size={16}
        />
        <span className="phone-card-summary-main">
          <span className="phone-card-header">
            <span className="phone-card-title-row">
              <ServiceLogo
                label={providerLabel(integration.provider)}
                logo={integration.logo}
                size="md"
              />
              <span>
                <strong className="phone-card-title">{integration.label}</strong>
                <span className="phone-card-subtitle">
                  {providerLabel(integration.provider)} ·{" "}
                  {pluralize(phoneNumbers.length, "phone number")}
                </span>
              </span>
            </span>
            <span className={`status-chip status-${integration.status}`}>
              {statusLabel(integration.status)}
            </span>
          </span>
          <span className="phone-meta">
            <span>{providerLabel(integration.provider)}</span>
            <span>{integration.enabled ? "enabled" : "disabled"}</span>
            <span>{integrationReferenceSummary(integration)}</span>
          </span>
        </span>
      </summary>

      <div className="phone-card-content">
        {phoneNumbers.length > 0 ? (
          <div className="tool-approval-grid">
            {phoneNumbers.map((resource) => (
              <div
                className="tool-approval-row read-only phone-number-row"
                key={resource.id}
              >
                <span>
                  <strong>{resource.display_name}</strong>
                  <small>{resourceSummary(resource)}</small>
                </span>
                <span className={`status-chip status-${resource.status}`}>
                  {statusLabel(resource.status)}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">No phone numbers connected.</p>
        )}

        {integration.provider === "telnyx" ? (
          <TelnyxOperations
            canEdit={canEdit}
            integrationId={integration.id}
            onLog={onLog}
          />
        ) : null}

        <div className="phone-card-actions">
          <button
            disabled={!canEdit || isSaving}
            onClick={() => void onDisconnect(integration)}
            type="button"
          >
            <Unplug aria-hidden="true" size={16} />
            Disconnect
          </button>
        </div>
      </div>
    </details>
  );
}

function TelnyxOperations({
  canEdit,
  integrationId,
  onLog,
}: {
  canEdit: boolean;
  integrationId: string;
  onLog: (message: string, level?: LogEntry["level"]) => void;
}) {
  const [readiness, setReadiness] = useState<TelnyxReadinessResponse | null>(null);
  const [messages, setMessages] = useState<IntegrationMessageActivity[]>([]);
  const [calls, setCalls] = useState<IntegrationCallActivity[]>([]);
  const [isWorking, setIsWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshActivity = useCallback(async () => {
    try {
      const [messageResponse, callResponse] = await Promise.all([
        loadIntegrationMessageActivity(integrationId),
        loadIntegrationCallActivity(integrationId),
      ]);
      setMessages(messageResponse.messages);
      setCalls(callResponse.calls);
    } catch (activityError) {
      const message = errorMessage(activityError);
      setError(message);
      onLog(`Telnyx activity load failed: ${message}`, "error");
    }
  }, [integrationId, onLog]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void refreshActivity();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [refreshActivity]);

  const verify = async () => {
    setIsWorking(true);
    setError(null);
    try {
      setReadiness(await verifyTelnyxIntegration(integrationId));
      onLog("Telnyx readiness verified.");
      await refreshActivity();
    } catch (verifyError) {
      const message = errorMessage(verifyError);
      setError(message);
      onLog(`Telnyx verification failed: ${message}`, "error");
    } finally {
      setIsWorking(false);
    }
  };

  const repair = async () => {
    if (
      !canEdit ||
      !window.confirm(
        "Repair the existing Telnyx number assignments and webhook routing? This changes provider-side configuration.",
      )
    ) {
      return;
    }
    setIsWorking(true);
    setError(null);
    try {
      setReadiness(await repairTelnyxIntegration(integrationId));
      onLog("Telnyx routing repaired and verified.");
      await refreshActivity();
    } catch (repairError) {
      const message = errorMessage(repairError);
      setError(message);
      onLog(`Telnyx repair failed: ${message}`, "error");
    } finally {
      setIsWorking(false);
    }
  };

  return (
    <section className="telnyx-operations" aria-label="Telnyx operations">
      <div className="telnyx-operations-heading">
        <span>
          <strong>Readiness and activity</strong>
          <small>
            Verify provider routing before testing. Repair updates the existing
            VoxOS-owned assignments only.
          </small>
        </span>
        <span className="telnyx-operations-actions">
          <button disabled={isWorking} onClick={() => void verify()} type="button">
            <RefreshCw aria-hidden="true" size={14} />
            {isWorking ? "Checking" : "Verify"}
          </button>
          <button disabled={!canEdit || isWorking} onClick={() => void repair()} type="button">
            Repair
          </button>
        </span>
      </div>

      {readiness === null ? (
        <p className="muted">Provider routing has not been verified in this session.</p>
      ) : (
        <div className="telnyx-readiness-grid">
          {readiness.checks.map((check) => (
            <div className="telnyx-readiness-row" key={check.name}>
              <span>
                <strong>{readinessCheckLabel(check.name)}</strong>
                <small>{check.message}</small>
              </span>
              <span className={`status-chip readiness-${check.status}`}>
                {readinessStatusLabel(check.status)}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="telnyx-activity-columns">
        <TelnyxMessageActivity messages={messages} />
        <TelnyxCallActivity calls={calls} />
      </div>
      {error !== null ? <p className="error-text">{error}</p> : null}
    </section>
  );
}

function TelnyxMessageActivity({
  messages,
}: {
  messages: IntegrationMessageActivity[];
}) {
  return (
    <div className="telnyx-activity-list">
      <strong>Recent messages</strong>
      {messages.length === 0 ? (
        <small>
          No message events received. If a test SMS was sent, verify the Telnyx
          messaging profile and webhook attempts.
        </small>
      ) : (
        messages.slice(0, 5).map((message) => (
          <div className="telnyx-activity-row" key={message.id}>
            <span>
              <strong>
                {titleCase(message.direction)} · {message.remote_party ?? "unknown number"}
              </strong>
              <small>
                {message.reply_status !== null
                  ? `Reply ${titleCase(message.reply_status)}`
                  : message.agent_run_status === null
                    ? message.failure_title ?? "No agent run"
                    : `Agent run ${titleCase(message.agent_run_status)}`}
                {message.estimated_segments === null
                  ? ""
                  : ` · ${message.estimated_segments} SMS segment${message.estimated_segments === 1 ? "" : "s"}`}
                {message.failure_code === null ? "" : ` · ${message.failure_code}`}
              </small>
            </span>
            <span
              className={`status-chip activity-${message.reply_status ?? message.status}`}
            >
              {titleCase(message.reply_status ?? message.status)}
            </span>
          </div>
        ))
      )}
    </div>
  );
}

function TelnyxCallActivity({ calls }: { calls: IntegrationCallActivity[] }) {
  return (
    <div className="telnyx-activity-list">
      <strong>Recent calls</strong>
      {calls.length === 0 ? (
        <small>No call sessions recorded yet.</small>
      ) : (
        calls.slice(0, 5).map((call) => (
          <div className="telnyx-activity-row" key={call.id}>
            <span>
              <strong>
                {titleCase(call.direction)} · {call.contact_display_name ?? call.remote_party ?? "unknown number"}
              </strong>
              <small>
                {call.failure_code === null
                  ? call.answered_at === null
                    ? "Not answered"
                    : "Answered"
                  : `Failure ${call.failure_code}`}
              </small>
            </span>
            <span className={`status-chip activity-${call.status}`}>
              {titleCase(call.status)}
            </span>
          </div>
        ))
      )}
    </div>
  );
}

function readinessCheckLabel(name: string): string {
  const labels: Record<string, string> = {
    agent_run_processing: "Agent reply worker",
    inbound_sms: "Inbound SMS",
    inbound_voice: "Inbound calls",
    livekit_routing: "LiveKit routing",
    outbound_sms: "Outbound SMS",
    outbound_voice: "Outbound calls",
  };
  return labels[name] ?? titleCase(name);
}

function readinessStatusLabel(status: string): string {
  return status === "not_ready" ? "Not ready" : titleCase(status);
}

function titleCase(value: string): string {
  return value
    .split("_")
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function selectedCapabilities(
  candidate: TwilioPhoneNumberCandidate,
): IntegrationCapability[] {
  return DEFAULT_TWILIO_CAPABILITIES.filter((capability) =>
    candidate.capabilities.includes(capability),
  );
}

function telnyxPhoneSetupRequest(
  form: TelnyxFormState,
): TelnyxPhoneSetupRequest {
  return {
    api_key: form.apiKey.trim(),
    confirm_number_assignment: form.confirmNumberAssignment,
    label: form.label.trim(),
    phone_number: form.phoneNumber.trim(),
    transcript_storage_enabled: form.transcriptStorageEnabled,
    webhook_public_key: form.webhookPublicKey.trim(),
  };
}

function canSubmitTelnyx({
  canEdit,
  form,
  isSaving,
}: {
  canEdit: boolean;
  form: TelnyxFormState;
  isSaving: boolean;
}): boolean {
  return (
    canEdit &&
    !isSaving &&
    form.label.trim() !== "" &&
    E164_PHONE_PATTERN.test(form.phoneNumber.trim()) &&
    form.apiKey.trim() !== "" &&
    form.webhookPublicKey.trim() !== "" &&
    form.confirmNumberAssignment
  );
}

function phoneNumberCountsByProvider(
  items: IntegrationWithResources[],
): Record<IntegrationProviderType, number> {
  const counts: Record<IntegrationProviderType, number> = {
    telnyx: 0,
    twilio: 0,
    whatsapp: 0,
  };
  for (const item of items) {
    counts[item.integration.provider] += item.resources.filter(
      isPhoneNumberResource,
    ).length;
  }
  return counts;
}

function isPhoneNumberResource(resource: IntegrationResource): boolean {
  return PHONE_RESOURCE_TYPES.has(resource.resource_type);
}

function webhookSummary(candidate: TwilioPhoneNumberCandidate): string {
  const values = [
    candidate.voice_url === null ? "Voice empty" : "Voice set",
    candidate.sms_url === null ? "SMS empty" : "SMS set",
    candidate.status_callback === null ? "Status empty" : "Status set",
  ];
  return values.join(" / ");
}

function resourceSummary(resource: IntegrationResource): string {
  const parts = [
    resourceTypeLabel(resource.resource_type),
    capabilitiesSummary(resource.capabilities),
    transcriptStorageSummary(resource),
  ].filter((part) => part !== "");
  return parts.join(" / ");
}

function resourceTypeLabel(resourceType: IntegrationResourceType): string {
  switch (resourceType) {
    case "phone_number":
      return "Phone number";
  }
}

function capabilitiesSummary(capabilities: IntegrationCapability[]): string {
  if (capabilities.length === 0) {
    return "";
  }
  return capabilities.map(capabilityLabel).join(", ");
}

function capabilityLabel(capability: IntegrationCapability): string {
  switch (capability) {
    case "inbound_voice":
      return "inbound voice";
    case "media_stream":
      return "media stream";
    case "outbound_voice":
      return "outbound voice";
    case "sms_read":
      return "SMS read";
    case "sms_send":
      return "SMS send";
    case "tool_actions":
      return "tool actions";
    case "webhook_events":
      return "webhook events";
    case "whatsapp_read":
      return "WhatsApp read";
    case "whatsapp_send":
      return "WhatsApp send";
  }
}

function transcriptStorageSummary(resource: IntegrationResource): string {
  return resource.safe_metadata.transcript_storage_enabled === true
    ? "transcripts stored"
    : "";
}

function providerLabel(provider: IntegrationProviderType): string {
  return PROVIDER_LABELS[provider];
}

function integrationReferenceSummary(integration: Integration): string {
  const metadata = integration.safe_metadata;
  if (typeof metadata.account_sid === "string" && metadata.account_sid.trim() !== "") {
    return `Account ${metadata.account_sid}`;
  }
  if (typeof metadata.connection_id === "string" && metadata.connection_id.trim() !== "") {
    return `Connection ${metadata.connection_id}`;
  }
  if (
    typeof metadata.messaging_profile_id === "string" &&
    metadata.messaging_profile_id.trim() !== ""
  ) {
    return `Messaging profile ${metadata.messaging_profile_id}`;
  }
  if (integration.provider === "whatsapp") {
    return "Linked device";
  }
  return "No provider reference";
}

function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
}

function pluralize(count: number, singular: string): string {
  return `${count} ${singular}${count === 1 ? "" : "s"}`;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
