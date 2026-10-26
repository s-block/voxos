import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  loadIntegrationCallActivity,
  loadIntegrationMessageActivity,
  loadIntegrationResources,
  loadIntegrations,
  loadWhatsAppOptions,
  repairTelnyxIntegration,
  verifyTelnyxIntegration,
  type Integration,
  type IntegrationResource,
  type TelnyxReadinessResponse,
} from "../api";
import { IntegrationsPage } from "./IntegrationsPage";

vi.mock("../api", () => ({
  connectTwilioResources: vi.fn(),
  disconnectIntegration: vi.fn(),
  loadIntegrationCallActivity: vi.fn(),
  loadIntegrationMessageActivity: vi.fn(),
  loadIntegrationResources: vi.fn(),
  loadIntegrations: vi.fn(),
  loadWhatsAppChallenge: vi.fn(),
  loadWhatsAppOptions: vi.fn(),
  loadWhatsAppStatus: vi.fn(),
  linkWhatsApp: vi.fn(),
  regenerateWhatsAppChallenge: vi.fn(),
  relinkWhatsApp: vi.fn(),
  repairTelnyxIntegration: vi.fn(),
  setupTelnyxIntegration: vi.fn(),
  setupTwilioIntegration: vi.fn(),
  verifyTelnyxIntegration: vi.fn(),
}));

describe("IntegrationsPage Telnyx operations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(loadIntegrations).mockResolvedValue([telnyxIntegration]);
    vi.mocked(loadIntegrationResources).mockResolvedValue([telnyxResource]);
    vi.mocked(loadWhatsAppOptions).mockResolvedValue([]);
    vi.mocked(loadIntegrationMessageActivity).mockResolvedValue({
      limit: 10,
      messages: [
        {
          agent_run_id: "run-1",
          agent_run_status: "queued",
          condensation_attempted: false,
          conversation_id: "conversation-1",
          created_at: "2026-07-14T09:00:00Z",
          direction: "inbound",
          external_message_id: "message-1",
          failure_code: null,
          failure_title: null,
          id: "message-record-1",
          integration_id: "integration-1",
          remote_party: "+15551234567",
          estimated_segments: 1,
          overflow_fallback_used: false,
          provider: "telnyx",
          reply_id: "reply-1",
          reply_status: "provider_accepted",
          resource_id: "resource-1",
          response_profile: "sms:v1",
          sms_encoding: "gsm_7",
          status: "received",
          updated_at: "2026-07-14T09:00:00Z",
        },
        {
          agent_run_id: null,
          agent_run_status: null,
          condensation_attempted: false,
          conversation_id: null,
          created_at: "2026-07-14T08:00:00Z",
          direction: "outbound",
          external_message_id: "message-2",
          failure_code: "40001",
          failure_title: "Destination blocked",
          id: "message-record-2",
          integration_id: "integration-1",
          remote_party: "+447700900123",
          estimated_segments: null,
          overflow_fallback_used: false,
          provider: "telnyx",
          reply_id: null,
          reply_status: null,
          resource_id: "resource-1",
          response_profile: null,
          sms_encoding: null,
          status: "delivery_unconfirmed",
          updated_at: "2026-07-14T08:01:00Z",
        },
      ],
      next_offset: null,
      offset: 0,
    });
    vi.mocked(loadIntegrationCallActivity).mockResolvedValue({
      calls: [
        {
          agent_id: "agent-1",
          answered_at: null,
          contact_display_name: "Approved test contact",
          contact_id: "contact-1",
          created_at: "2026-07-14T07:00:00Z",
          direction: "outbound",
          ended_at: "2026-07-14T07:00:01Z",
          external_call_id: "reservation-1",
          failure_code: "provider_call_start_failed",
          id: "call-1",
          integration_id: "integration-1",
          remote_party: "+447700900123",
          provider: "telnyx",
          resource_id: "resource-1",
          started_at: "2026-07-14T07:00:00Z",
          status: "failed",
          updated_at: "2026-07-14T07:00:01Z",
        },
      ],
      limit: 10,
      next_offset: null,
      offset: 0,
    });
    vi.mocked(verifyTelnyxIntegration).mockResolvedValue(telnyxReadiness);
    vi.mocked(repairTelnyxIntegration).mockResolvedValue({
      ...telnyxReadiness,
      repaired: true,
    });
  });

  it("renders phone activity and verifies readiness states", async () => {
    const user = userEvent.setup();
    render(<IntegrationsPage canEdit onLog={vi.fn()} />);

    expect(await screen.findByText("Inbound · +15551234567")).toBeInTheDocument();
    expect(screen.getByText("Provider Accepted")).toBeInTheDocument();
    expect(screen.getByText("Delivery Unconfirmed")).toBeInTheDocument();
    expect(screen.getByText("Failure provider_call_start_failed")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByText("Inbound SMS")).toBeInTheDocument();
    expect(screen.getByText("Agent reply worker")).toBeInTheDocument();
    expect(verifyTelnyxIntegration).toHaveBeenCalledWith("integration-1");
  });

  it("requires browser confirmation before provider-side repair", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<IntegrationsPage canEdit onLog={vi.fn()} />);
    await screen.findByText("Readiness and activity");

    await user.click(screen.getByRole("button", { name: "Repair" }));
    expect(repairTelnyxIntegration).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Repair" }));
    await waitFor(() => {
      expect(repairTelnyxIntegration).toHaveBeenCalledWith("integration-1");
    });
    expect(confirm).toHaveBeenCalledTimes(2);
    confirm.mockRestore();
  });
});

const telnyxIntegration: Integration = {
  auth_type: "api_key",
  created_at: "2026-07-14T06:00:00Z",
  default_agent_id: "agent-1",
  enabled: true,
  id: "integration-1",
  label: "Telnyx",
  last_checked_at: null,
  logo: { domain: "telnyx.com", label: "Telnyx", source: "domain" },
  provider: "telnyx",
  safe_metadata: {},
  setup_state: "connected",
  status: "connected",
  status_message: null,
  updated_at: "2026-07-14T06:00:00Z",
};

const telnyxResource: IntegrationResource = {
  capabilities: ["inbound_voice", "outbound_voice", "sms_read", "sms_send"],
  created_at: "2026-07-14T06:00:00Z",
  display_name: "+447418606704",
  external_id: "telnyx-number-id",
  id: "resource-1",
  integration_id: "integration-1",
  provider: "telnyx",
  resource_type: "phone_number",
  retained_metadata_json: {},
  safe_metadata: {},
  status: "connected",
  updated_at: "2026-07-14T06:00:00Z",
};

const telnyxReadiness: TelnyxReadinessResponse = {
  checked_at: "2026-07-14T10:00:00Z",
  checks: [
    {
      code: "inbound_sms_ready",
      message: "Inbound messaging routing is ready.",
      name: "inbound_sms",
      status: "ready",
    },
    {
      code: "worker_liveness_unobserved",
      message: "Worker liveness cannot be confirmed from durable state.",
      name: "agent_run_processing",
      status: "unknown",
    },
  ],
  integration_id: "integration-1",
  provider: {
    capabilities: ["inbound_voice", "outbound_voice", "sms_read", "sms_send"],
    connection_assignment_matches: true,
    connection_id: "connection-1",
    phone_number: "+447418606704",
    messaging_assignment_matches: true,
    messaging_destinations: ["GB"],
    messaging_profile_enabled: true,
    messaging_profile_found: true,
    messaging_profile_id: "messaging-profile-1",
    messaging_webhook_api_version: "2",
    messaging_webhook_matches: true,
    outbound_voice_profile_assignment_matches: true,
    outbound_voice_profile_enabled: true,
    outbound_voice_profile_found: true,
    outbound_voice_profile_id: "voice-profile-1",
    phone_number_id: "telnyx-number-id",
    phone_number_status: "active",
    voice_application_active: true,
    voice_application_found: true,
    voice_destinations: ["GB"],
    voice_webhook_api_version: "2",
    voice_webhook_matches: true,
  },
  repaired: false,
  resource_id: "resource-1",
};
