import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  CheckCircle2,
  Copy,
  Link2,
  MessageSquareText,
  PhoneCall,
  QrCode,
  RefreshCw,
  Smartphone,
  Unplug,
} from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import type { LogEntry } from "../appTypes";
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
  type WhatsAppLinkMethod,
  type WhatsAppMobileRegistrationChannel,
  type WhatsAppMobileRegistrationStatus,
  type WhatsAppSessionState,
  type WhatsAppSetupOption,
  type WhatsAppStatus,
} from "../api";
import { ServiceLogo } from "../components/ServiceLogo";
import { WHATSAPP_LOGO } from "../serviceLogos";

const ACTIVE_POLL_INTERVAL_MS = 2_000;
const RECONNECTING_POLL_INTERVAL_MS = 5_000;
const RETRY_POLL_INTERVAL_MS = 5_000;
const CONNECTED_POLL_INTERVAL_MS = 30_000;

type WhatsAppIntegrationPanelProps = {
  canEdit: boolean;
  integrationId: string | null;
  isIntegrationListLoading: boolean;
  onIntegrationChange: () => Promise<void>;
  onLog: (message: string, level?: LogEntry["level"]) => void;
};

export function WhatsAppIntegrationPanel({
  canEdit,
  integrationId,
  isIntegrationListLoading,
  onIntegrationChange,
  onLog,
}: WhatsAppIntegrationPanelProps) {
  const [options, setOptions] = useState<WhatsAppSetupOption[]>([]);
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [method, setMethod] = useState<WhatsAppLinkMethod>("qr");
  const [setupMode, setSetupMode] = useState<"choose" | "registered">(
    "choose",
  );
  const [registration, setRegistration] =
    useState<WhatsAppMobileRegistrationStatus | null>(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [status, setStatus] = useState<WhatsAppStatus | null>(null);
  const [challenge, setChallenge] = useState<WhatsAppChallenge | null>(null);
  const [challengeClock, setChallengeClock] = useState(() => Date.now());
  const [isLoadingOptions, setIsLoadingOptions] = useState(true);
  const [isLoadingStatus, setIsLoadingStatus] = useState(integrationId !== null);
  const [isWorking, setIsWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fetchedChallengeKeyRef = useRef<string | null>(null);
  const notifiedConnectedIdRef = useRef<string | null>(null);
  const statusIntegrationId = status?.integration_id ?? null;
  const statusState = status?.state ?? null;
  const statusChallengeExpiresAt = status?.challenge_expires_at ?? null;
  const shouldLoadChallenge =
    canEdit &&
    statusState !== null &&
    isChallengeState(statusState) &&
    statusChallengeExpiresAt !== null;
  const registrationId = registration?.registration_id ?? null;
  const registrationState = registration?.state ?? null;

  const applyStatus = useCallback((nextStatus: WhatsAppStatus) => {
    const nextChallengeKey = challengeKey(nextStatus);
    setStatus(nextStatus);
    setChallenge((current) =>
      challengeKey(current) === nextChallengeKey ? current : null,
    );
    if (fetchedChallengeKeyRef.current !== nextChallengeKey) {
      fetchedChallengeKeyRef.current = null;
    }
  }, []);

  const refreshOptions = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const nextOptions = await loadWhatsAppOptions(signal);
        setOptions(nextOptions);
        setSelectedSourceId((current) => {
          if (
            nextOptions.some((option) => option.source_resource_id === current)
          ) {
            return current;
          }
          return nextOptions.length === 1
            ? nextOptions[0].source_resource_id
            : "";
        });
      } catch (loadError) {
        if (isAbortError(loadError)) {
          return;
        }
        const message = errorMessage(loadError);
        setError(message);
        onLog(`WhatsApp setup options failed to load: ${message}`, "error");
      } finally {
        if (signal?.aborted !== true) {
          setIsLoadingOptions(false);
        }
      }
    },
    [onLog],
  );

  useEffect(() => {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => {
      void refreshOptions(controller.signal);
    }, 0);
    return () => {
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [refreshOptions]);

  useEffect(() => {
    if (integrationId === null) {
      return;
    }

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => {
      setIsLoadingStatus(true);
      void loadWhatsAppStatus(integrationId, controller.signal)
        .then((nextStatus) => {
          applyStatus(nextStatus);
          setError(null);
        })
        .catch((loadError: unknown) => {
          if (isAbortError(loadError)) {
            return;
          }
          const message = errorMessage(loadError);
          setError(message);
          onLog(`WhatsApp status failed to load: ${message}`, "error");
        })
        .finally(() => {
          if (!controller.signal.aborted) {
            setIsLoadingStatus(false);
          }
        });
    }, 0);

    return () => {
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [applyStatus, integrationId, onLog]);

  useEffect(() => {
    if (
      !shouldLoadChallenge ||
      statusIntegrationId === null ||
      statusChallengeExpiresAt === null
    ) {
      return;
    }

    const challengeKey = `${statusIntegrationId}:${statusChallengeExpiresAt}`;
    if (fetchedChallengeKeyRef.current === challengeKey) {
      return;
    }
    let controller: AbortController | null = null;
    let hasLoggedLoadError = false;
    let retryTimeoutId: number | null = null;
    let stopped = false;

    const loadChallenge = () => {
      if (stopped) {
        return;
      }
      fetchedChallengeKeyRef.current = challengeKey;
      controller = new AbortController();
      void loadWhatsAppChallenge(statusIntegrationId, controller.signal)
        .then((nextChallenge) => {
          if (stopped) {
            return;
          }
          setChallenge(nextChallenge);
          setError(null);
          setMethod(nextChallenge.method);
          setChallengeClock(Date.now());
        })
        .catch((loadError: unknown) => {
          if (stopped || isAbortError(loadError)) {
            return;
          }
          fetchedChallengeKeyRef.current = null;
          if (isChallengeNotReadyError(loadError)) {
            retryTimeoutId = window.setTimeout(
              loadChallenge,
              ACTIVE_POLL_INTERVAL_MS,
            );
            return;
          }
          setError("The WhatsApp link code could not be loaded. Retrying…");
          if (!hasLoggedLoadError) {
            hasLoggedLoadError = true;
            onLog(
              `WhatsApp link code failed to load: ${errorMessage(loadError)}`,
              "error",
            );
          }
          retryTimeoutId = window.setTimeout(
            loadChallenge,
            RETRY_POLL_INTERVAL_MS,
          );
        });
    };

    loadChallenge();
    return () => {
      stopped = true;
      if (retryTimeoutId !== null) {
        window.clearTimeout(retryTimeoutId);
      }
      controller?.abort();
    };
  }, [
    onLog,
    shouldLoadChallenge,
    statusChallengeExpiresAt,
    statusIntegrationId,
  ]);

  useEffect(() => {
    if (challenge === null || challenge.challenge_expires_at === null) {
      return;
    }
    const expiresAt = Date.parse(challenge.challenge_expires_at);
    if (!Number.isFinite(expiresAt) || expiresAt <= challengeClock) {
      return;
    }
    const delay = Math.min(
      expiresAt - challengeClock + 25,
      2_147_483_647,
    );
    const timeoutId = window.setTimeout(
      () => setChallengeClock(Date.now()),
      delay,
    );
    return () => window.clearTimeout(timeoutId);
  }, [challenge, challengeClock]);

  useEffect(() => {
    if (statusIntegrationId === null || statusState === null) {
      return;
    }
    const firstDelay = pollInterval(statusState);
    if (firstDelay === null) {
      return;
    }

    let controller: AbortController | null = null;
    let stopped = false;
    let timeoutId: number | null = null;

    const schedule = (delay: number) => {
      timeoutId = window.setTimeout(() => {
        controller = new AbortController();
        void loadWhatsAppStatus(
          statusIntegrationId,
          controller.signal,
        )
          .then((nextStatus) => {
            if (stopped) {
              return;
            }
            applyStatus(nextStatus);
            setError(null);
            const nextDelay = pollInterval(nextStatus.state);
            if (nextDelay !== null) {
              schedule(nextDelay);
            }
          })
          .catch((loadError: unknown) => {
            if (stopped || isAbortError(loadError)) {
              return;
            }
            if (
              loadError instanceof ApiRequestError &&
              loadError.status === 404 &&
              statusState === "disconnecting"
            ) {
              setStatus(null);
              setChallenge(null);
              setSetupMode("choose");
              fetchedChallengeKeyRef.current = null;
              notifiedConnectedIdRef.current = null;
              onLog("WhatsApp disconnected and its linked session removed.");
              void Promise.all([refreshOptions(), onIntegrationChange()]).catch(
                (refreshError: unknown) => {
                  onLog(
                    `WhatsApp setup failed to refresh after disconnect: ${errorMessage(refreshError)}`,
                    "error",
                  );
                },
              );
              return;
            }
            const message = errorMessage(loadError);
            setError(message);
            schedule(RETRY_POLL_INTERVAL_MS);
          });
      }, delay);
    };

    schedule(firstDelay);
    return () => {
      stopped = true;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
      controller?.abort();
    };
  }, [
    applyStatus,
    onIntegrationChange,
    onLog,
    refreshOptions,
    statusIntegrationId,
    statusState,
  ]);

  useEffect(() => {
    if (
      status?.state !== "connected" ||
      notifiedConnectedIdRef.current === status.integration_id
    ) {
      return;
    }
    notifiedConnectedIdRef.current = status.integration_id;
    onLog("WhatsApp number verified and connected.");
    void onIntegrationChange().catch((refreshError: unknown) => {
      onLog(
        `Connected integrations failed to refresh: ${errorMessage(refreshError)}`,
        "error",
      );
    });
  }, [onIntegrationChange, onLog, status?.integration_id, status?.state]);

  useEffect(() => {
    if (
      registrationId === null ||
      registrationState === null ||
      registrationState === "expired"
    ) {
      return;
    }
    let controller: AbortController | null = null;
    let stopped = false;
    let timeoutId: number | null = null;

    const poll = () => {
      timeoutId = window.setTimeout(() => {
        controller = new AbortController();
        void loadWhatsAppMobileRegistration(
          registrationId,
          controller.signal,
        )
          .then((nextRegistration) => {
            if (stopped) {
              return;
            }
            setRegistration(nextRegistration);
            setSelectedCandidateId((current) =>
              nextRegistration.candidates.some(
                (candidate) => candidate.candidate_id === current,
              )
                ? current
                : (nextRegistration.candidates[0]?.candidate_id ?? ""),
            );
            setError(null);
            if (nextRegistration.state !== "expired") {
              poll();
            }
          })
          .catch((loadError: unknown) => {
            if (stopped || isAbortError(loadError)) {
              return;
            }
            setError(errorMessage(loadError));
            poll();
          });
      }, ACTIVE_POLL_INTERVAL_MS);
    };

    poll();
    return () => {
      stopped = true;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
      controller?.abort();
    };
  }, [registrationId, registrationState]);

  const startLink = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      !canEdit ||
      isWorking ||
      selectedSourceId === "" ||
      setupMode !== "registered"
    ) {
      return;
    }

    setIsWorking(true);
    setError(null);
    setChallenge(null);
    fetchedChallengeKeyRef.current = null;
    try {
      const nextStatus = await linkWhatsApp({
        method,
        source_resource_id: selectedSourceId,
      });
      applyStatus(nextStatus);
      onLog("WhatsApp linking started.");
      await onIntegrationChange();
    } catch (linkError) {
      const message = errorMessage(linkError);
      setError(message);
      onLog(`WhatsApp linking failed: ${message}`, "error");
    } finally {
      setIsWorking(false);
    }
  };

  const startMobileRegistration = async (
    channel: WhatsAppMobileRegistrationChannel,
  ) => {
    if (!canEdit || isWorking || selectedSourceId === "") {
      return;
    }
    setIsWorking(true);
    setError(null);
    try {
      const nextRegistration = await startWhatsAppMobileRegistration({
        channel,
        source_resource_id: selectedSourceId,
      });
      setRegistration(nextRegistration);
      setSelectedCandidateId(
        nextRegistration.candidates[0]?.candidate_id ?? "",
      );
      onLog(
        channel === "voice"
          ? "Waiting for the WhatsApp verification call."
          : "Waiting securely for the WhatsApp verification SMS.",
      );
    } catch (registrationError) {
      const message = errorMessage(registrationError);
      setError(message);
      onLog(`WhatsApp mobile registration failed: ${message}`, "error");
    } finally {
      setIsWorking(false);
    }
  };

  const rejectRegistrationCandidate = async () => {
    if (
      !canEdit ||
      isWorking ||
      registration === null ||
      selectedCandidateId === ""
    ) {
      return;
    }
    setIsWorking(true);
    setError(null);
    try {
      const nextRegistration =
        await rejectWhatsAppMobileRegistrationCandidate(
          registration.registration_id,
          { candidate_id: selectedCandidateId },
        );
      setRegistration(nextRegistration);
      setSelectedCandidateId(
        nextRegistration.candidates[0]?.candidate_id ?? "",
      );
      onLog(
        selectedRegistrationCandidate?.channel === "sms"
          ? "The SMS was returned to the normal message flow."
          : "The captured voice code was dismissed.",
      );
    } catch (registrationError) {
      const message = errorMessage(registrationError);
      setError(message);
      onLog(`The captured code could not be dismissed: ${message}`, "error");
    } finally {
      setIsWorking(false);
    }
  };

  const completeMobileRegistration = async () => {
    if (
      !canEdit ||
      isWorking ||
      registration === null ||
      selectedCandidateId === ""
    ) {
      return;
    }
    setIsWorking(true);
    setError(null);
    try {
      const nextStatus = await completeWhatsAppMobileRegistration(
        registration.registration_id,
        {
          candidate_id: selectedCandidateId,
          method,
        },
      );
      setRegistration(null);
      setSelectedCandidateId("");
      applyStatus(nextStatus);
      onLog("WhatsApp Business registration confirmed; linking started.");
      await onIntegrationChange();
    } catch (registrationError) {
      const message = errorMessage(registrationError);
      setError(message);
      onLog(`WhatsApp linking failed: ${message}`, "error");
    } finally {
      setIsWorking(false);
    }
  };

  const cancelMobileRegistration = async () => {
    if (!canEdit || isWorking || registration === null) {
      return;
    }
    setIsWorking(true);
    setError(null);
    try {
      await cancelWhatsAppMobileRegistration(registration.registration_id);
      setRegistration(null);
      setSelectedCandidateId("");
      setSetupMode("choose");
      onLog("WhatsApp mobile registration cancelled.");
    } catch (registrationError) {
      const message = errorMessage(registrationError);
      setError(message);
      onLog(`WhatsApp registration could not be cancelled: ${message}`, "error");
    } finally {
      setIsWorking(false);
    }
  };

  const regenerateChallenge = async (nextMethod: WhatsAppLinkMethod) => {
    if (!canEdit || isWorking || status === null) {
      return;
    }
    setIsWorking(true);
    setError(null);
    setMethod(nextMethod);
    setChallenge(null);
    fetchedChallengeKeyRef.current = null;
    try {
      const nextChallenge = await regenerateWhatsAppChallenge(
        status.integration_id,
        { method: nextMethod },
      );
      fetchedChallengeKeyRef.current = `${nextChallenge.integration_id}:${nextChallenge.challenge_expires_at}`;
      setChallenge(nextChallenge);
      setMethod(nextChallenge.method);
      setChallengeClock(Date.now());
      applyStatus(nextChallenge);
      onLog("WhatsApp link code regenerated.");
    } catch (challengeError) {
      if (isChallengeNotReadyError(challengeError)) {
        try {
          const nextStatus = await loadWhatsAppStatus(status.integration_id);
          applyStatus(nextStatus);
          onLog("WhatsApp link code regeneration requested.");
          return;
        } catch (statusError) {
          const message = errorMessage(statusError);
          setError(message);
          onLog(
            `WhatsApp status failed to load after code regeneration: ${message}`,
            "error",
          );
          return;
        }
      }
      const message = errorMessage(challengeError);
      setError(message);
      onLog(`WhatsApp link code regeneration failed: ${message}`, "error");
    } finally {
      setIsWorking(false);
    }
  };

  const relink = async () => {
    if (!canEdit || isWorking || status === null) {
      return;
    }
    setIsWorking(true);
    setError(null);
    setChallenge(null);
    fetchedChallengeKeyRef.current = null;
    try {
      const nextStatus = await relinkWhatsApp(status.integration_id, {
        method,
      });
      applyStatus(nextStatus);
      onLog("WhatsApp relinking started.");
    } catch (relinkError) {
      const message = errorMessage(relinkError);
      setError(message);
      onLog(`WhatsApp relinking failed: ${message}`, "error");
    } finally {
      setIsWorking(false);
    }
  };

  const disconnect = async () => {
    if (
      !canEdit ||
      isWorking ||
      status === null ||
      !window.confirm(
        "Disconnect WhatsApp? VoxOS will revoke this linked device and delete its saved session. Connecting again will require a new link.",
      )
    ) {
      return;
    }

    setIsWorking(true);
    setError(null);
    try {
      await disconnectIntegration(status.integration_id);
      applyStatus({
        ...status,
        challenge_expires_at: null,
        state: "disconnecting",
        status_message:
          "VoxOS is closing the linked session and deleting its credentials.",
      });
      onLog("WhatsApp disconnect requested. Waiting for secure cleanup.");
      await onIntegrationChange();
    } catch (disconnectError) {
      const message = errorMessage(disconnectError);
      setError(message);
      onLog(`WhatsApp disconnect failed: ${message}`, "error");
    } finally {
      setIsWorking(false);
    }
  };

  const challengeExpired =
    challenge !== null &&
    isExpired(challenge.challenge_expires_at, challengeClock);
  const isResolvingExistingIntegration =
    status === null &&
    (isIntegrationListLoading || integrationId !== null);
  const canStart =
    canEdit &&
    !isIntegrationListLoading &&
    !isWorking &&
    selectedSourceId !== "" &&
    setupMode === "registered";
  const selectedRegistrationCandidate =
    registration?.candidates.find(
      (candidate) => candidate.candidate_id === selectedCandidateId,
    ) ?? null;
  const selectedOption =
    options.find((option) => option.source_resource_id === selectedSourceId) ??
    null;
  const canVerifyByVoice = selectedOption?.provider === "telnyx";

  return (
    <section
      aria-labelledby="whatsapp-integration-title"
      className="settings-panel whatsapp-panel"
    >
      <div className="section-heading-row">
        <div className="phone-card-title-row">
          <ServiceLogo label="WhatsApp" logo={WHATSAPP_LOGO} size="md" />
          <div className="section-title">
            <p className="eyebrow">Messaging</p>
            <h2 id="whatsapp-integration-title">WhatsApp linked device</h2>
          </div>
        </div>
        {isLoadingStatus ? (
          <span className="status-chip">Loading</span>
        ) : status !== null ? (
          <span
            className={`status-chip whatsapp-status-${status.state}`}
            data-testid="whatsapp-status-chip"
          >
            {statusLabel(status.state)}
          </span>
        ) : null}
      </div>

      <p className="whatsapp-intro">
        Connect WhatsApp Business to let your agent receive and reply to customer
        messages from your VoxOS number. The mobile app remains the primary
        account and may occasionally require relinking.
      </p>

      {isResolvingExistingIntegration ? (
        <p className="muted">Loading WhatsApp connection…</p>
      ) : status === null ? (
        <form className="whatsapp-setup" onSubmit={(event) => void startLink(event)}>
          <div className="whatsapp-prerequisite">
            <Smartphone aria-hidden="true" size={20} />
            <span>
              <strong>Use your VoxOS number in WhatsApp Business</strong>
              <small>
                VoxOS can capture the one-time code by call or SMS and guide you
                through linking the registered account.
              </small>
            </span>
          </div>

          {isLoadingOptions ? (
            <p className="muted">Loading connected phone numbers…</p>
          ) : options.length === 0 ? (
            <p className="muted">
              Connect a Twilio or Telnyx phone number before setting up WhatsApp.
            </p>
          ) : (
            <label className="field whatsapp-number-field">
              <span>Phone number</span>
              <select
                disabled={!canEdit || isWorking || registration !== null}
                onChange={(event) => {
                  setSelectedSourceId(event.currentTarget.value);
                  setSetupMode("choose");
                }}
                value={selectedSourceId}
              >
                {options.length > 1 ? (
                  <option value="">Choose a connected number</option>
                ) : null}
                {options.map((option) => (
                  <option
                    key={option.source_resource_id}
                    value={option.source_resource_id}
                  >
                    {option.phone_number} · {providerLabel(option.provider)}
                    {option.line_type === null ||
                    option.line_type === undefined
                      ? ""
                      : ` · ${titleCase(option.line_type)}`}
                  </option>
                ))}
              </select>
            </label>
          )}

          {registration !== null ? (
            <div className="whatsapp-registration" aria-live="polite">
              <div className="whatsapp-state-summary">
                <span>
                  {registration.state === "armed_voice" ||
                  selectedRegistrationCandidate?.channel === "voice" ? (
                    <PhoneCall aria-hidden="true" size={20} />
                  ) : (
                    <MessageSquareText aria-hidden="true" size={20} />
                  )}
                </span>
                <span>
                  <strong>
                    {registration.state === "expired"
                      ? "Verification window expired"
                      : registration.candidates.length > 0
                        ? "Verification code received"
                        : registration.state === "armed_voice"
                          ? "Waiting for the WhatsApp call"
                          : "Waiting for the WhatsApp SMS"}
                  </strong>
                  <small>
                    {registration.state === "expired"
                      ? "Cancel this attempt and start again immediately before requesting a new code."
                      : registration.candidates.length > 0
                        ? selectedRegistrationCandidate?.channel === "voice"
                          ? "VoxOS captured the spoken verification code from this call."
                          : "VoxOS did not send this message to the agent or reply to it."
                        : registration.state === "armed_voice"
                          ? `In WhatsApp Business enter ${registration.phone_number}, choose “Call me”, and keep this page open.`
                        : `In WhatsApp Business choose “Use a different number”, enter ${registration.phone_number}, then request the SMS.`}
                  </small>
                </span>
              </div>

              {registration.candidates.length > 0 ? (
                <div className="whatsapp-registration-candidates">
                  {registration.candidates.map((candidate) => (
                    <label
                      className="whatsapp-registration-code"
                      key={candidate.candidate_id}
                    >
                      <input
                        checked={
                          selectedCandidateId === candidate.candidate_id
                        }
                        disabled={!canEdit || isWorking}
                        name="whatsapp-registration-code"
                        onChange={() =>
                          setSelectedCandidateId(candidate.candidate_id)
                        }
                        type="radio"
                      />
                      <span>
                        <small>
                          {candidate.channel === "voice"
                            ? "Received by voice call"
                            : `From ${candidate.sender}`}
                        </small>
                        <strong>{formatVerificationCode(candidate.code)}</strong>
                      </span>
                      <button
                        aria-label={`Copy verification code ${candidate.code}`}
                        className="secondary compact"
                        disabled={!canEdit}
                        onClick={() =>
                          void navigator.clipboard.writeText(candidate.code)
                        }
                        type="button"
                      >
                        <Copy aria-hidden="true" size={15} />
                        Copy
                      </button>
                    </label>
                  ))}
                </div>
              ) : null}

              {selectedRegistrationCandidate !== null ? (
                <>
                  <p className="muted">
                    Enter this code in WhatsApp Business. When the app confirms
                    the number, continue below.
                  </p>
                  <LinkMethodPicker
                    canEdit={canEdit && !isWorking}
                    method={method}
                    onChange={setMethod}
                  />
                  <div className="whatsapp-registration-actions">
                    <button
                      className="primary"
                      disabled={!canEdit || isWorking}
                      onClick={() => void completeMobileRegistration()}
                      type="button"
                    >
                      <Link2 aria-hidden="true" size={16} />
                      I entered this code
                    </button>
                    <button
                      className="secondary"
                      disabled={!canEdit || isWorking}
                      onClick={() => void rejectRegistrationCandidate()}
                      type="button"
                    >
                      Not the WhatsApp code
                    </button>
                  </div>
                </>
              ) : null}

              <button
                className="text-button"
                disabled={!canEdit || isWorking}
                onClick={() => void cancelMobileRegistration()}
                type="button"
              >
                Cancel registration
              </button>
            </div>
          ) : setupMode === "choose" ? (
            <div className="whatsapp-setup-choice">
              {canVerifyByVoice ? (
                <button
                  className="primary"
                  disabled={
                    !canEdit ||
                    isWorking ||
                    isLoadingOptions ||
                    selectedSourceId === ""
                  }
                  onClick={() => void startMobileRegistration("voice")}
                  type="button"
                >
                  <PhoneCall aria-hidden="true" size={16} />
                  Verify with a call
                </button>
              ) : null}
              <button
                className={canVerifyByVoice ? "secondary" : "primary"}
                disabled={
                  !canEdit ||
                  isWorking ||
                  isLoadingOptions ||
                  selectedSourceId === ""
                }
                onClick={() => void startMobileRegistration("sms")}
                type="button"
              >
                <MessageSquareText aria-hidden="true" size={16} />
                Verify with SMS
              </button>
              <button
                className="secondary"
                disabled={
                  !canEdit ||
                  isWorking ||
                  isLoadingOptions ||
                  selectedSourceId === ""
                }
                onClick={() => setSetupMode("registered")}
                type="button"
              >
                It is already registered
              </button>
            </div>
          ) : (
            <>
              <div className="whatsapp-prerequisite">
                <CheckCircle2 aria-hidden="true" size={20} />
                <span>
                  <strong>Ready to link the registered account</strong>
                  <small>
                    VoxOS will still verify that the linked WhatsApp identity
                    matches the selected number.
                  </small>
                </span>
              </div>
              <LinkMethodPicker
                canEdit={canEdit && !isWorking}
                method={method}
                onChange={setMethod}
              />
              <div className="whatsapp-registration-actions">
                <button className="primary" disabled={!canStart} type="submit">
                  <Link2 aria-hidden="true" size={16} />
                  {isWorking ? "Starting link" : "Link WhatsApp"}
                </button>
                <button
                  className="text-button"
                  disabled={!canEdit || isWorking}
                  onClick={() => setSetupMode("choose")}
                  type="button"
                >
                  Back
                </button>
              </div>
            </>
          )}
        </form>
      ) : (
        <div className="whatsapp-session">
          <div aria-live="polite" className="whatsapp-state-summary">
            <span>
              {status.state === "connected" ? (
                <CheckCircle2 aria-hidden="true" size={20} />
              ) : (
                <QrCode aria-hidden="true" size={20} />
              )}
            </span>
            <span>
              <strong>{statusTitle(status.state)}</strong>
              <small>
                {status.status_message ??
                  statusDescription(status.state, status.phone_number)}
              </small>
            </span>
          </div>

          {canEdit && challenge !== null && isChallengeState(status.state) ? (
            <ChallengeDisplay
              canEdit={canEdit}
              challenge={challenge}
              expired={challengeExpired}
              isWorking={isWorking}
              onRegenerate={regenerateChallenge}
            />
          ) : !canEdit &&
            (status.state === "awaiting_link" ||
              status.state === "connecting") ? (
            <p className="muted">
              The temporary link code is hidden while settings are read-only.
            </p>
          ) : status.state === "awaiting_link" ||
            status.state === "connecting" ? (
            <p className="muted">Preparing a secure link code…</p>
          ) : null}

          {status.state === "needs_relink" ||
          status.state === "failed" ||
          status.state === "disabled" ? (
            <div className="whatsapp-relink-actions">
              <LinkMethodPicker
                canEdit={canEdit && !isWorking}
                method={method}
                onChange={setMethod}
              />
              <button
                className="primary"
                disabled={!canEdit || isWorking}
                onClick={() => void relink()}
                type="button"
              >
                <Link2 aria-hidden="true" size={16} />
                {isWorking ? "Starting relink" : "Relink WhatsApp"}
              </button>
            </div>
          ) : null}

          {status.state === "connected" ? (
            <dl className="whatsapp-connection-meta">
              <div>
                <dt>Number</dt>
                <dd>{status.phone_number}</dd>
              </div>
              <div>
                <dt>Verified</dt>
                <dd>{formatDateTime(status.validated_at)}</dd>
              </div>
              <div>
                <dt>Connected</dt>
                <dd>{formatDateTime(status.connected_at)}</dd>
              </div>
            </dl>
          ) : null}

          <div className="phone-card-actions">
            <button
              className="danger"
              disabled={
                !canEdit || isWorking || status.state === "disconnecting"
              }
              onClick={() => void disconnect()}
              type="button"
            >
              <Unplug aria-hidden="true" size={16} />
              {status.state === "disconnecting"
                ? "Disconnecting WhatsApp"
                : "Disconnect WhatsApp"}
            </button>
          </div>
        </div>
      )}

      {error !== null ? <p className="error-text">{error}</p> : null}
    </section>
  );
}

function LinkMethodPicker({
  canEdit,
  method,
  onChange,
}: {
  canEdit: boolean;
  method: WhatsAppLinkMethod;
  onChange: (method: WhatsAppLinkMethod) => void;
}) {
  return (
    <div aria-label="WhatsApp link method" className="segmented-control" role="group">
      <button
        aria-pressed={method === "qr"}
        className={method === "qr" ? "selected" : ""}
        disabled={!canEdit}
        onClick={() => onChange("qr")}
        type="button"
      >
        <QrCode aria-hidden="true" size={15} />
        QR code
      </button>
      <button
        aria-pressed={method === "pairing_code"}
        className={method === "pairing_code" ? "selected" : ""}
        disabled={!canEdit}
        onClick={() => onChange("pairing_code")}
        type="button"
      >
        Pairing code
      </button>
    </div>
  );
}

function ChallengeDisplay({
  canEdit,
  challenge,
  expired,
  isWorking,
  onRegenerate,
}: {
  canEdit: boolean;
  challenge: WhatsAppChallenge;
  expired: boolean;
  isWorking: boolean;
  onRegenerate: (method: WhatsAppLinkMethod) => Promise<void>;
}) {
  const alternateMethod =
    challenge.method === "qr" ? "pairing_code" : ("qr" as const);

  return (
    <div className="whatsapp-challenge">
      {expired ? (
        <div aria-live="polite" className="whatsapp-expired">
          <strong>Link code expired</strong>
          <span>Generate a fresh code, then continue on your phone.</span>
        </div>
      ) : (
        <>
          {challenge.method === "qr" ? (
            <figure className="whatsapp-qr">
              <QRCodeSVG
                aria-label="WhatsApp link QR code"
                bgColor="#ffffff"
                fgColor="#17211c"
                level="M"
                marginSize={4}
                role="img"
                size={224}
                title="WhatsApp link QR code"
                value={challenge.value}
              />
              <figcaption>
                This code is temporary and is never saved in your browser.
              </figcaption>
            </figure>
          ) : (
            <div className="whatsapp-pairing-code">
              <span>Enter this temporary code on your phone</span>
              <strong aria-label={`Pairing code ${challenge.value}`}>
                {formatPairingCode(challenge.value)}
              </strong>
            </div>
          )}

          <ol className="whatsapp-link-steps">
            <li>Open WhatsApp Business on your phone.</li>
            <li>
              Open <strong>Linked devices</strong> from Settings or the menu.
            </li>
            <li>
              {challenge.method === "qr"
                ? "Tap Link a device and scan this QR code."
                : "Tap Link a device, choose Link with phone number instead, and enter the code."}
            </li>
          </ol>
        </>
      )}

      <div className="whatsapp-challenge-actions">
        <button
          disabled={!canEdit || isWorking}
          onClick={() => void onRegenerate(challenge.method)}
          type="button"
        >
          <RefreshCw aria-hidden="true" size={15} />
          {expired ? "Generate new code" : "Refresh code"}
        </button>
        {!expired ? (
          <button
            disabled={!canEdit || isWorking}
            onClick={() => void onRegenerate(alternateMethod)}
            type="button"
          >
            {alternateMethod === "qr"
              ? "Show QR code instead"
              : "Use pairing code instead"}
          </button>
        ) : null}
      </div>
    </div>
  );
}

function pollInterval(state: WhatsAppSessionState): number | null {
  switch (state) {
    case "awaiting_link":
    case "connecting":
    case "waiting_for_capacity":
    case "disconnecting":
      return ACTIVE_POLL_INTERVAL_MS;
    case "reconnecting":
      return RECONNECTING_POLL_INTERVAL_MS;
    case "connected":
      return CONNECTED_POLL_INTERVAL_MS;
    case "needs_relink":
    case "failed":
    case "disabled":
      return null;
  }
}

function challengeKey(
  value: WhatsAppStatus | WhatsAppChallenge | null,
): string | null {
  if (
    value === null ||
    !isChallengeState(value.state) ||
    value.challenge_expires_at === null
  ) {
    return null;
  }
  return `${value.integration_id}:${value.challenge_expires_at}`;
}

function isChallengeState(state: WhatsAppSessionState): boolean {
  return state === "awaiting_link" || state === "connecting";
}

function isExpired(expiresAt: string | null, now: number): boolean {
  if (expiresAt === null) {
    return false;
  }
  const parsed = Date.parse(expiresAt);
  return Number.isFinite(parsed) && parsed <= now;
}

function statusLabel(state: WhatsAppSessionState): string {
  switch (state) {
    case "awaiting_link":
      return "Waiting for scan";
    case "connecting":
      return "Verifying";
    case "connected":
      return "Connected";
    case "reconnecting":
      return "Reconnecting";
    case "waiting_for_capacity":
      return "Waiting";
    case "needs_relink":
      return "Relink required";
    case "disconnecting":
      return "Disconnecting";
    case "failed":
      return "Failed";
    case "disabled":
      return "Not connected";
  }
}

function statusTitle(state: WhatsAppSessionState): string {
  switch (state) {
    case "awaiting_link":
      return "Waiting for you to link your phone";
    case "connecting":
      return "Checking the linked number";
    case "connected":
      return "Number verified and connected";
    case "reconnecting":
      return "Reconnecting automatically";
    case "waiting_for_capacity":
      return "Waiting for connection capacity";
    case "needs_relink":
      return "WhatsApp needs to be relinked";
    case "disconnecting":
      return "Disconnecting WhatsApp";
    case "failed":
      return "WhatsApp could not connect";
    case "disabled":
      return "WhatsApp is not connected";
  }
}

function statusDescription(
  state: WhatsAppSessionState,
  phoneNumber: string,
): string {
  switch (state) {
    case "awaiting_link":
      return `Complete the link in WhatsApp Business for ${phoneNumber}.`;
    case "connecting":
      return "VoxOS is securely verifying that the linked account matches your number.";
    case "connected":
      return `${phoneNumber} is ready for one-to-one WhatsApp messages.`;
    case "reconnecting":
      return "No action is needed. VoxOS is restoring the saved linked session.";
    case "waiting_for_capacity":
      return "Your setup is saved and will connect automatically when a worker is available.";
    case "needs_relink":
      return "The saved linked session was revoked or expired. Link the phone again to continue.";
    case "disconnecting":
      return "VoxOS is closing the linked session and deleting its credentials.";
    case "failed":
      return "Try linking again. VoxOS will only mark the number verified after an exact match.";
    case "disabled":
      return "Link this number again when you are ready.";
  }
}

function providerLabel(provider: WhatsAppSetupOption["provider"]): string {
  switch (provider) {
    case "telnyx":
      return "Telnyx";
    case "twilio":
      return "Twilio";
  }
}

function formatPairingCode(value: string): string {
  return value.replace(/\s+/g, "").replace(/(.{4})(?=.)/g, "$1 ");
}

function formatVerificationCode(value: string): string {
  if (value.length === 6) {
    return `${value.slice(0, 3)} ${value.slice(3)}`;
  }
  return value.replace(/(.{4})(?=.)/g, "$1 ");
}

function formatDateTime(value: string | null): string {
  if (value === null) {
    return "Pending";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Recorded";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function titleCase(value: string): string {
  return value
    .split("_")
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function isChallengeNotReadyError(error: unknown): boolean {
  return (
    error instanceof Error &&
    error.message.toLowerCase().includes("challenge_not_ready")
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
