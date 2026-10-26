import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError,
  cancelWhatsAppMobileRegistration,
  completeWhatsAppMobileRegistration,
  disconnectIntegration,
  linkWhatsApp,
  loadWhatsAppChallenge,
  loadWhatsAppMobileRegistration,
  loadWhatsAppOptions,
  loadWhatsAppStatus,
  rejectWhatsAppMobileRegistrationCandidate,
  regenerateWhatsAppChallenge,
  relinkWhatsApp,
  startWhatsAppMobileRegistration,
  type WhatsAppChallenge,
  type WhatsAppMobileRegistrationStatus,
  type WhatsAppStatus,
} from "../api";
import { WhatsAppIntegrationPanel } from "./WhatsAppIntegrationPanel";

vi.mock("../api", () => ({
  ApiRequestError: class ApiRequestError extends Error {
    readonly status: number;

    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  cancelWhatsAppMobileRegistration: vi.fn(),
  completeWhatsAppMobileRegistration: vi.fn(),
  disconnectIntegration: vi.fn(),
  linkWhatsApp: vi.fn(),
  loadWhatsAppChallenge: vi.fn(),
  loadWhatsAppMobileRegistration: vi.fn(),
  loadWhatsAppOptions: vi.fn(),
  loadWhatsAppStatus: vi.fn(),
  rejectWhatsAppMobileRegistrationCandidate: vi.fn(),
  regenerateWhatsAppChallenge: vi.fn(),
  relinkWhatsApp: vi.fn(),
  startWhatsAppMobileRegistration: vi.fn(),
}));

describe("WhatsAppIntegrationPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(loadWhatsAppOptions).mockResolvedValue([
      {
        line_type: "mobile",
        phone_number: "+447418606704",
        provider: "telnyx",
        source_resource_id: "resource-1",
      },
    ]);
    vi.mocked(loadWhatsAppChallenge).mockResolvedValue(qrChallenge);
    vi.mocked(regenerateWhatsAppChallenge).mockResolvedValue(pairingChallenge);
    vi.mocked(relinkWhatsApp).mockResolvedValue(awaitingStatus);
    vi.mocked(disconnectIntegration).mockResolvedValue();
    vi.mocked(cancelWhatsAppMobileRegistration).mockResolvedValue();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("auto-selects one phone number and starts the default QR link", async () => {
    const user = userEvent.setup();
    const onIntegrationChange = vi.fn().mockResolvedValue(undefined);
    vi.mocked(linkWhatsApp).mockResolvedValue(awaitingStatus);

    renderPanel({ onIntegrationChange });

    expect(
      await screen.findByRole("option", {
        name: "+447418606704 · Telnyx · Mobile",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Connect WhatsApp Business to let your agent receive and reply/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/capture the one-time code by call or SMS/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Meta developer|Meta Cloud|Meta-supported/i),
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "It is already registered" }),
    );
    const linkButton = screen.getByRole("button", { name: "Link WhatsApp" });
    await user.click(linkButton);

    expect(linkWhatsApp).toHaveBeenCalledWith({
      method: "qr",
      source_resource_id: "resource-1",
    });
    expect(onIntegrationChange).toHaveBeenCalledTimes(1);
    expect(
      await screen.findByRole("img", { name: "WhatsApp link QR code" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Open WhatsApp Business on your phone."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Tap Link a device and scan this QR code."),
    ).toBeInTheDocument();
  });

  it("polls through verification and later notices that a connected session needs relinking", async () => {
    vi.useFakeTimers();
    const onIntegrationChange = vi.fn().mockResolvedValue(undefined);
    vi.mocked(loadWhatsAppStatus)
      .mockResolvedValueOnce(awaitingStatus)
      .mockResolvedValueOnce(connectedStatus)
      .mockResolvedValueOnce(needsRelinkStatus);

    renderPanel({
      integrationId: "whatsapp-1",
      onIntegrationChange,
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(
      screen.getByText("Waiting for you to link your phone"),
    ).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(
      screen.getByText("Number verified and connected"),
    ).toBeInTheDocument();
    expect(loadWhatsAppStatus).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(loadWhatsAppStatus).toHaveBeenCalledTimes(3);
    expect(
      screen.getByRole("button", { name: "Relink WhatsApp" }),
    ).toBeEnabled();
    expect(onIntegrationChange).toHaveBeenCalledTimes(1);
  });

  it("keeps polling while the worker is still preparing the challenge", async () => {
    vi.useFakeTimers();
    vi.mocked(loadWhatsAppStatus).mockResolvedValue(awaitingStatus);
    vi.mocked(loadWhatsAppChallenge)
      .mockRejectedValueOnce(new Error("challenge_not_ready"))
      .mockResolvedValueOnce(qrChallenge);

    renderPanel({ integrationId: "whatsapp-1" });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("Preparing a secure link code…")).toBeInTheDocument();
    expect(screen.queryByText("challenge_not_ready")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(
      screen.getByRole("img", { name: "WhatsApp link QR code" }),
    ).toBeInTheDocument();
    expect(loadWhatsAppChallenge).toHaveBeenCalledTimes(2);
    expect(loadWhatsAppStatus).toHaveBeenCalledTimes(2);
  });

  it("does not offer relink during recovery but does when credentials need it", async () => {
    vi.mocked(loadWhatsAppStatus).mockResolvedValue(reconnectingStatus);
    const firstRender = renderPanel({ integrationId: "whatsapp-1" });

    expect(
      await screen.findByText("Reconnecting automatically"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Relink WhatsApp" }),
    ).not.toBeInTheDocument();
    firstRender.unmount();

    vi.mocked(loadWhatsAppStatus).mockResolvedValue(needsRelinkStatus);
    renderPanel({ integrationId: "whatsapp-1" });

    expect(
      await screen.findByRole("button", { name: "Relink WhatsApp" }),
    ).toBeEnabled();
  });

  it("switches an in-memory QR challenge to a pairing code", async () => {
    const user = userEvent.setup();
    vi.mocked(loadWhatsAppStatus).mockResolvedValue(awaitingStatus);
    renderPanel({ integrationId: "whatsapp-1" });

    await screen.findByRole("img", { name: "WhatsApp link QR code" });
    await user.click(
      screen.getByRole("button", { name: "Use pairing code instead" }),
    );

    expect(regenerateWhatsAppChallenge).toHaveBeenCalledWith("whatsapp-1", {
      method: "pairing_code",
    });
    expect(
      await screen.findByText("1234 5678", { exact: true }),
    ).toBeInTheDocument();
  });

  it("hides an obsolete challenge while regeneration is still pending", async () => {
    const user = userEvent.setup();
    const pendingStatus: WhatsAppStatus = {
      ...awaitingStatus,
      challenge_expires_at: null,
    };
    vi.mocked(loadWhatsAppStatus)
      .mockResolvedValueOnce(awaitingStatus)
      .mockResolvedValueOnce(pendingStatus);
    vi.mocked(regenerateWhatsAppChallenge).mockRejectedValueOnce(
      new Error("challenge_not_ready"),
    );
    renderPanel({ integrationId: "whatsapp-1" });

    await screen.findByRole("img", { name: "WhatsApp link QR code" });
    await user.click(
      screen.getByRole("button", { name: "Use pairing code instead" }),
    );

    await waitFor(() => {
      expect(loadWhatsAppStatus).toHaveBeenCalledTimes(2);
    });
    expect(
      screen.queryByRole("img", { name: "WhatsApp link QR code" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("challenge_not_ready")).not.toBeInTheDocument();
    expect(screen.getByText("Preparing a secure link code…")).toBeInTheDocument();
  });

  it("does not render an expired challenge and can generate a fresh one", async () => {
    const user = userEvent.setup();
    const expiredStatus: WhatsAppStatus = {
      ...awaitingStatus,
      challenge_expires_at: "2020-01-01T00:00:00Z",
    };
    vi.mocked(loadWhatsAppStatus).mockResolvedValue(expiredStatus);
    vi.mocked(loadWhatsAppChallenge).mockResolvedValue({
      ...expiredStatus,
      method: "qr",
      value: "expired-qr-value",
    });
    vi.mocked(regenerateWhatsAppChallenge).mockResolvedValue(qrChallenge);
    renderPanel({ integrationId: "whatsapp-1" });

    expect(await screen.findByText("Link code expired")).toBeInTheDocument();
    expect(
      screen.queryByRole("img", { name: "WhatsApp link QR code" }),
    ).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Generate new code" }),
    );
    expect(regenerateWhatsAppChallenge).toHaveBeenCalledWith("whatsapp-1", {
      method: "qr",
    });
    expect(
      await screen.findByRole("img", { name: "WhatsApp link QR code" }),
    ).toBeInTheDocument();
  });

  it("keeps setup mutations unavailable to read-only users", async () => {
    renderPanel({ canEdit: false });

    expect(
      await screen.findByRole("button", {
        name: "Verify with a call",
      }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Verify with SMS" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "It is already registered" }),
    ).toBeDisabled();
    expect(linkWhatsApp).not.toHaveBeenCalled();
    expect(startWhatsAppMobileRegistration).not.toHaveBeenCalled();
  });

  it("never fetches or renders a live challenge while settings are read-only", async () => {
    vi.mocked(loadWhatsAppStatus).mockResolvedValue(awaitingStatus);

    renderPanel({ canEdit: false, integrationId: "whatsapp-1" });

    expect(
      await screen.findByText(
        "The temporary link code is hidden while settings are read-only.",
      ),
    ).toBeInTheDocument();
    expect(loadWhatsAppChallenge).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("img", { name: "WhatsApp link QR code" }),
    ).not.toBeInTheDocument();
  });

  it("requires choosing the setup path again after changing numbers", async () => {
    const user = userEvent.setup();
    vi.mocked(loadWhatsAppOptions).mockResolvedValue([
      {
        phone_number: "+447418606704",
        provider: "telnyx",
        source_resource_id: "resource-1",
      },
      {
        phone_number: "+447700901234",
        provider: "twilio",
        source_resource_id: "resource-2",
      },
    ]);
    renderPanel();

    const numberSelect = await screen.findByLabelText("Phone number");
    await user.selectOptions(numberSelect, "resource-1");
    await user.click(
      screen.getByRole("button", { name: "It is already registered" }),
    );
    expect(
      screen.getByRole("button", { name: "Link WhatsApp" }),
    ).toBeEnabled();

    await user.selectOptions(numberSelect, "resource-2");

    expect(
      screen.queryByRole("button", { name: "Link WhatsApp" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Verify with SMS" }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Verify with a call" }),
    ).not.toBeInTheDocument();
  });

  it("shows a captured SMS code and starts linking after the user enters it", async () => {
    const user = userEvent.setup();
    const onIntegrationChange = vi.fn().mockResolvedValue(undefined);
    vi.mocked(startWhatsAppMobileRegistration).mockResolvedValue(
      registrationWithCode,
    );
    vi.mocked(loadWhatsAppMobileRegistration).mockResolvedValue(
      registrationWithCode,
    );
    vi.mocked(completeWhatsAppMobileRegistration).mockResolvedValue(
      awaitingStatus,
    );

    renderPanel({ onIntegrationChange });

    await user.click(
      await screen.findByRole("button", {
        name: "Verify with SMS",
      }),
    );

    expect(startWhatsAppMobileRegistration).toHaveBeenCalledWith({
      channel: "sms",
      source_resource_id: "resource-1",
    });
    expect(await screen.findByText("123 456")).toBeInTheDocument();
    expect(
      screen.getByText(
        "VoxOS did not send this message to the agent or reply to it.",
      ),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "I entered this code" }),
    );

    expect(completeWhatsAppMobileRegistration).toHaveBeenCalledWith(
      "registration-1",
      {
        candidate_id: "message-1",
        method: "qr",
      },
    );
    expect(onIntegrationChange).toHaveBeenCalledTimes(1);
    expect(
      await screen.findByRole("img", { name: "WhatsApp link QR code" }),
    ).toBeInTheDocument();
  });

  it("releases a captured non-WhatsApp code back to normal SMS handling", async () => {
    const user = userEvent.setup();
    vi.mocked(startWhatsAppMobileRegistration).mockResolvedValue(
      registrationWithCode,
    );
    vi.mocked(rejectWhatsAppMobileRegistrationCandidate).mockResolvedValue({
      ...registrationWithCode,
      candidates: [],
      state: "armed",
    });
    renderPanel();

    await user.click(
      await screen.findByRole("button", {
        name: "Verify with SMS",
      }),
    );
    await user.click(
      await screen.findByRole("button", {
        name: "Not the WhatsApp code",
      }),
    );

    expect(rejectWhatsAppMobileRegistrationCandidate).toHaveBeenCalledWith(
      "registration-1",
      { candidate_id: "message-1" },
    );
    expect(
      await screen.findByText("Waiting for the WhatsApp SMS"),
    ).toBeInTheDocument();
  });

  it("captures a spoken code from a WhatsApp verification call", async () => {
    const user = userEvent.setup();
    const voiceRegistration: WhatsAppMobileRegistrationStatus = {
      ...registrationWithCode,
      candidates: [
        {
          ...registrationWithCode.candidates[0],
          candidate_id: "voice-event-1",
          channel: "voice",
          sender: "+447700900000",
        },
      ],
    };
    vi.mocked(startWhatsAppMobileRegistration).mockResolvedValue(
      voiceRegistration,
    );
    vi.mocked(loadWhatsAppMobileRegistration).mockResolvedValue(
      voiceRegistration,
    );
    renderPanel();

    await user.click(
      await screen.findByRole("button", { name: "Verify with a call" }),
    );

    expect(startWhatsAppMobileRegistration).toHaveBeenCalledWith({
      channel: "voice",
      source_resource_id: "resource-1",
    });
    expect(await screen.findByText("Received by voice call")).toBeInTheDocument();
    expect(
      screen.getByText(
        "VoxOS captured the spoken verification code from this call.",
      ),
    ).toBeInTheDocument();
  });

  it("requires confirmation before revoking and deleting a linked session", async () => {
    const user = userEvent.setup();
    const confirm = vi
      .spyOn(window, "confirm")
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    const onIntegrationChange = vi.fn().mockResolvedValue(undefined);
    const onLog = vi.fn();
    vi.mocked(loadWhatsAppStatus).mockResolvedValue(connectedStatus);
    renderPanel({
      integrationId: "whatsapp-1",
      onIntegrationChange,
      onLog,
    });

    const disconnectButton = await screen.findByRole("button", {
      name: "Disconnect WhatsApp",
    });
    await user.click(disconnectButton);
    expect(disconnectIntegration).not.toHaveBeenCalled();

    await user.click(disconnectButton);
    await waitFor(() => {
      expect(disconnectIntegration).toHaveBeenCalledWith("whatsapp-1");
    });
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(onIntegrationChange).toHaveBeenCalled();
    expect(screen.getByTestId("whatsapp-status-chip")).toHaveTextContent(
      "Disconnecting",
    );
    expect(onLog).toHaveBeenCalledWith(
      "WhatsApp disconnect requested. Waiting for secure cleanup.",
    );
    expect(onLog).not.toHaveBeenCalledWith(
      "WhatsApp disconnected and its linked session removed.",
    );
    confirm.mockRestore();
  });

  it("reports disconnect completion only after the worker cleanup removes status", async () => {
    vi.useFakeTimers();
    const onIntegrationChange = vi.fn().mockResolvedValue(undefined);
    const onLog = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(loadWhatsAppStatus)
      .mockResolvedValueOnce(connectedStatus)
      .mockRejectedValueOnce(
        new ApiRequestError(404, "WhatsApp integration not found."),
      );
    renderPanel({
      integrationId: "whatsapp-1",
      onIntegrationChange,
      onLog,
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Disconnect WhatsApp" }),
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(onLog).not.toHaveBeenCalledWith(
      "WhatsApp disconnected and its linked session removed.",
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(onLog).toHaveBeenCalledWith(
      "WhatsApp disconnected and its linked session removed.",
    );
    expect(onIntegrationChange).toHaveBeenCalledTimes(3);
  });
});

function renderPanel({
  canEdit = true,
  integrationId = null,
  onIntegrationChange = vi.fn().mockResolvedValue(undefined),
  onLog = vi.fn(),
}: {
  canEdit?: boolean;
  integrationId?: string | null;
  onIntegrationChange?: () => Promise<void>;
  onLog?: (message: string, level?: "info" | "error") => void;
} = {}) {
  return render(
    <WhatsAppIntegrationPanel
      canEdit={canEdit}
      integrationId={integrationId}
      isIntegrationListLoading={false}
      onIntegrationChange={onIntegrationChange}
      onLog={onLog}
    />,
  );
}

const awaitingStatus: WhatsAppStatus = {
  challenge_expires_at: "2099-07-20T12:05:00Z",
  connected_at: null,
  integration_id: "whatsapp-1",
  phone_number: "+447418606704",
  source_resource_id: "resource-1",
  state: "awaiting_link",
  status_message: null,
  validated_at: null,
};

const connectedStatus: WhatsAppStatus = {
  ...awaitingStatus,
  challenge_expires_at: null,
  connected_at: "2026-07-20T12:00:00Z",
  state: "connected",
  validated_at: "2026-07-20T11:59:58Z",
};

const reconnectingStatus: WhatsAppStatus = {
  ...connectedStatus,
  state: "reconnecting",
  status_message: "Restoring the saved linked session.",
};

const needsRelinkStatus: WhatsAppStatus = {
  ...connectedStatus,
  connected_at: null,
  state: "needs_relink",
  status_message: null,
};

const qrChallenge: WhatsAppChallenge = {
  ...awaitingStatus,
  method: "qr",
  value: "whatsapp-qr-value",
};

const pairingChallenge: WhatsAppChallenge = {
  ...awaitingStatus,
  method: "pairing_code",
  value: "12345678",
};

const registrationWithCode: WhatsAppMobileRegistrationStatus = {
  candidates: [
    {
      candidate_id: "message-1",
      channel: "sms",
      code: "123456",
      sender: "+447700900000",
      received_at: "2026-07-20T12:00:00Z",
    },
  ],
  capture_expires_at: "2099-07-20T12:10:00Z",
  phone_number: "+447418606704",
  registration_id: "registration-1",
  source_resource_id: "resource-1",
  state: "code_received",
};
