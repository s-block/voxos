import { FormEvent, useState } from "react";
import {
  BarVisualizer,
  ControlBar,
  useAgent,
  useChat,
  useTranscriptions,
} from "@livekit/components-react";
import { ConnectionState } from "livekit-client";
import {
  DEFAULT_AGENT_NAME,
  DEFAULT_MODEL,
  DEFAULT_VOICE,
} from "../appConstants";
import type {
  ConnectionSettings,
  ConnectionSettingsUpdater,
  LogEntry,
} from "../appTypes";
import {
  agentAudioStatusMessage,
  sessionReadinessLabel,
  validateSettings,
} from "../appUtils";
import type { VoiceProviderSummary, VoiceSettings } from "../api";
import { EventLog } from "../components/EventLog";
import { TextField } from "../components/FormFields";

type VoicePageProps = {
  canEdit: boolean;
  connectionState: ConnectionState;
  endSession: () => Promise<void>;
  isConnected: boolean;
  isConnecting: boolean;
  logs: LogEntry[];
  provider: VoiceProviderSummary | null;
  resetRoom: () => void;
  settings: ConnectionSettings;
  startSession: () => Promise<void>;
  updateSettings: ConnectionSettingsUpdater;
  voiceSettings: VoiceSettings | null;
};

export function VoicePage({
  canEdit,
  connectionState,
  endSession,
  isConnected,
  isConnecting,
  logs,
  provider,
  resetRoom,
  settings,
  startSession,
  updateSettings,
  voiceSettings,
}: VoicePageProps) {
  return (
    <div className="voice-page">
      <VoiceSessionPanel
        canEdit={canEdit}
        connectionState={connectionState}
        endSession={endSession}
        isConnected={isConnected}
        isConnecting={isConnecting}
        provider={provider}
        resetRoom={resetRoom}
        settings={settings}
        startSession={startSession}
        updateSettings={updateSettings}
        voiceSettings={voiceSettings}
      />
      {isConnected || isConnecting ? <AgentPanel /> : <AgentPlaceholder />}
      <ControlStrip isConnected={isConnected} />
      {isConnected ? (
        <CommunicationPanel isConnected={isConnected} />
      ) : (
        <CommunicationPlaceholder />
      )}
      <EventLog logs={logs} />
    </div>
  );
}

type VoiceSessionPanelProps = {
  canEdit: boolean;
  connectionState: ConnectionState;
  endSession: () => Promise<void>;
  isConnected: boolean;
  isConnecting: boolean;
  provider: VoiceProviderSummary | null;
  resetRoom: () => void;
  settings: ConnectionSettings;
  startSession: () => Promise<void>;
  updateSettings: ConnectionSettingsUpdater;
  voiceSettings: VoiceSettings | null;
};

function VoiceSessionPanel({
  canEdit,
  connectionState,
  endSession,
  isConnected,
  isConnecting,
  provider,
  resetRoom,
  settings,
  startSession,
  updateSettings,
  voiceSettings,
}: VoiceSessionPanelProps) {
  const startLabel = isConnecting ? "Connecting" : "Start voice";
  const canToggleStreaming = canEdit || isConnected || isConnecting;
  const needsProviderKey =
    settings.mode === "endpoint" && voiceSettings?.configured !== true;
  const needsProviderAdapter =
    settings.mode === "endpoint" && provider?.runtime_supported === false;
  const settingsError = validateSettings(settings);
  const canStart =
    canEdit &&
    !needsProviderKey &&
    !needsProviderAdapter &&
    settings.livekitStreamingEnabled &&
    settingsError === null;
  const readiness = sessionReadinessLabel(
    settings.livekitStreamingEnabled,
    needsProviderKey,
    needsProviderAdapter,
    settingsError,
    isConnected,
    isConnecting,
  );

  const updateStreamingEnabled = (enabled: boolean) => {
    updateSettings("livekitStreamingEnabled", enabled);
    if (!enabled && (isConnected || isConnecting)) {
      void endSession();
    }
  };

  return (
    <section className="voice-command-panel">
      <div className="voice-command-main">
        <div className="voice-status-row">
          <span className={`readiness-pill ${readiness.kind}`}>
            {readiness.label}
          </span>
          <span className="connection-mode-pill">{settings.mode}</span>
          <span className="connection-mode-pill">
            {connectionState.toString().toLowerCase()}
          </span>
        </div>

        <div className="voice-title-row">
          <div>
            <p className="eyebrow">Voice agent</p>
            <h2>{provider?.label ?? "Provider not loaded"}</h2>
            <p>
              {voiceSettings?.model ?? provider?.default_model ?? DEFAULT_MODEL} /{" "}
              {voiceSettings?.voice ?? provider?.default_voice ?? DEFAULT_VOICE}
            </p>
          </div>
          {isConnected || isConnecting ? (
            <button
              className="danger session-main-button"
              onClick={() => void endSession()}
              type="button"
            >
              Disconnect
            </button>
          ) : (
            <button
              className="primary session-main-button"
              disabled={!canStart}
              onClick={() => void startSession()}
              type="button"
            >
              {startLabel}
            </button>
          )}
        </div>
      </div>

      <form
        className="session-form"
        onSubmit={(event: FormEvent<HTMLFormElement>) => {
          event.preventDefault();
          if (canStart) {
            void startSession();
          }
        }}
      >
        <div className="session-toggle-grid">
          <label className="switch-row">
            <input
              checked={settings.livekitStreamingEnabled}
              disabled={!canToggleStreaming}
              onChange={(event) =>
                updateStreamingEnabled(event.currentTarget.checked)
              }
              role="switch"
              type="checkbox"
            />
            <span className="switch-control" aria-hidden="true">
              <span />
            </span>
            <span className="switch-label">
              <strong>LiveKit</strong>
              <small>{settings.livekitStreamingEnabled ? "On" : "Off"}</small>
            </span>
          </label>
          <label className="check-row">
            <input
              checked={settings.startMicrophone}
              disabled={!canEdit || !settings.livekitStreamingEnabled}
              onChange={(event) =>
                updateSettings("startMicrophone", event.currentTarget.checked)
              }
              type="checkbox"
            />
            <span>Start mic enabled</span>
          </label>
        </div>

        <div className="session-field-grid compact">
          <TextField
            disabled={!canEdit}
            label="Room"
            onChange={(value) => updateSettings("roomName", value)}
            placeholder="voxos-dev"
            value={settings.roomName}
          />
          <TextField
            disabled={!canEdit}
            label="Participant"
            onChange={(value) => updateSettings("participantIdentity", value)}
            placeholder="tester"
            value={settings.participantIdentity}
          />
          <TextField
            disabled={!canEdit}
            label="Agent"
            onChange={(value) => updateSettings("agentName", value)}
            placeholder={DEFAULT_AGENT_NAME}
            value={settings.agentName}
          />
        </div>

        <div className="session-actions">
          <button disabled={!canEdit} onClick={resetRoom} type="button">
            New room
          </button>
          <button className="primary secondary-start" disabled={!canStart} type="submit">
            {startLabel}
          </button>
        </div>
      </form>
    </section>
  );
}

function AgentPlaceholder() {
  return (
    <section className="panel hero-panel">
      <div className="agent-header">
        <div>
          <p className="eyebrow">Agent</p>
          <h2>Waiting for session</h2>
        </div>
        <span className="agent-state">disconnected</span>
      </div>
      <div className="visualizer-frame">
        <div className="empty-visualizer">Start voice to join a LiveKit room</div>
      </div>
    </section>
  );
}

function AgentPanel() {
  const agent = useAgent();
  const failureText =
    agent.state === "failed" ? agent.failureReasons.join(", ") : null;
  const audioStatusMessage = agentAudioStatusMessage(
    agent.state,
    agent.microphoneTrack !== undefined,
  );

  return (
    <section className="panel hero-panel">
      <div className="agent-header">
        <div>
          <p className="eyebrow">Agent</p>
          <h2>{agent.name ?? agent.identity ?? "Waiting for agent"}</h2>
        </div>
        <span className={`agent-state state-${agent.state}`}>
          {agent.state}
        </span>
      </div>
      <div className="visualizer-frame">
        {agent.microphoneTrack ? (
          <BarVisualizer
            barCount={24}
            className="voice-bars"
            state={agent.state}
            track={agent.microphoneTrack}
          />
        ) : (
          <div className="empty-visualizer">{audioStatusMessage}</div>
        )}
      </div>
      {failureText ? <p className="error-text">{failureText}</p> : null}
    </section>
  );
}

function ControlStrip({ isConnected }: { isConnected: boolean }) {
  return (
    <section className="panel controls-panel">
      <div>
        <p className="eyebrow">Audio</p>
        <h2>Mic controls</h2>
      </div>
      {isConnected ? (
        <ControlBar
          controls={{ microphone: true, camera: false, screenShare: false }}
        />
      ) : (
        <p className="muted">Connect before toggling the microphone.</p>
      )}
    </section>
  );
}

function CommunicationPlaceholder() {
  return (
    <section className="panel communication-panel">
      <div className="communication-grid">
        <div className="message-column">
          <div className="section-title">
            <p className="eyebrow">Text</p>
            <h2>Chat</h2>
          </div>
          <div className="message-list">
            <p className="muted">Connect before sending text messages.</p>
          </div>
        </div>

        <div className="message-column">
          <div className="section-title">
            <p className="eyebrow">Realtime</p>
            <h2>Transcripts</h2>
          </div>
          <div className="message-list">
            <p className="muted">Transcription streams appear during a session.</p>
          </div>
        </div>
      </div>
    </section>
  );
}

function CommunicationPanel({ isConnected }: { isConnected: boolean }) {
  const [draft, setDraft] = useState("");
  const { chatMessages, isSending, send } = useChat();
  const transcriptions = useTranscriptions();

  const sendMessage = async () => {
    const message = draft.trim();
    if (!message) {
      return;
    }

    await send(message);
    setDraft("");
  };

  return (
    <section className="panel communication-panel">
      <div className="communication-grid">
        <div className="message-column">
          <div className="section-title">
            <p className="eyebrow">Text</p>
            <h2>Chat</h2>
          </div>
          <div className="message-list">
            {chatMessages.length > 0 ? (
              chatMessages.map((message) => (
                <article className="message" key={message.id}>
                  <span>{message.from?.identity ?? "local"}</span>
                  <p>{message.message}</p>
                </article>
              ))
            ) : (
              <p className="muted">No chat messages yet.</p>
            )}
          </div>
          <form
            className="chat-form"
            onSubmit={(event: FormEvent<HTMLFormElement>) => {
              event.preventDefault();
              void sendMessage();
            }}
          >
            <input
              disabled={!isConnected}
              onChange={(event) => setDraft(event.currentTarget.value)}
              placeholder="Send a text message"
              value={draft}
            />
            <button disabled={!isConnected || isSending} type="submit">
              Send
            </button>
          </form>
        </div>

        <div className="message-column">
          <div className="section-title">
            <p className="eyebrow">Realtime</p>
            <h2>Transcripts</h2>
          </div>
          <div className="message-list">
            {transcriptions.length > 0 ? (
              transcriptions.slice(-8).map((transcription) => (
                <article
                  className="message"
                  key={`${transcription.participantInfo.identity}-${transcription.streamInfo.id}`}
                >
                  <span>{transcription.participantInfo.identity}</span>
                  <p>{transcription.text}</p>
                </article>
              ))
            ) : (
              <p className="muted">No transcription streams yet.</p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
