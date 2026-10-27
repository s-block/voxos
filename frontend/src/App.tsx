import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RoomAudioRenderer, SessionProvider, useSession } from "@livekit/components-react";
import { ConnectionState } from "livekit-client";
import type { User } from "firebase/auth";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router";
import {
  configureFirebase,
  createEmailAccount,
  getFirebaseIdToken,
  signInWithApple,
  signInWithEmail,
  signInWithGoogle,
  signOutOfFirebase,
  subscribeToFirebaseUser,
} from "./firebase";
import {
  defaultApiBaseUrl,
  loadLiveKitSettings,
  loadTextProviderSettings,
  loadVoiceProviderSettings,
  saveImageGenerationSettings,
  saveLiveKitSettings as saveLiveKitSettingsRequest,
  saveOpenAICompatibleProvider as saveOpenAICompatibleProviderRequest,
  saveTextProviderKey as saveTextProviderKeyRequest,
  saveTextSettings,
  saveVoiceProviderKey as saveVoiceProviderKeyRequest,
  saveVoiceSettings,
  setApiBaseUrl,
  type LiveKitSettings,
  type TextProviderSettings,
  type TextSettings,
  type VoiceProviderSettings,
  type VoiceProviderSummary,
  type VoiceSettings,
} from "./api";
import { DEFAULT_AGENT_NAME } from "./appConstants";
import type {
  ActiveConnectionSettings,
  AppPage,
  AuthFormMode,
  ConnectionSettings,
  ConnectionSettingsUpdater,
  ImageGenerationPreferencesFormValues,
  LiveKitSettingsFormValues,
  LogEntry,
  OpenAICompatibleProviderFormValues,
  TextProviderKeyFormValues,
  TextPreferencesFormValues,
  VoiceProviderKeyFormValues,
  VoicePreferencesFormValues,
} from "./appTypes";
import {
  activeProviderSummary,
  buildLiveKitSessionOptions,
  agentConfigKey,
  authErrorMessage,
  buildTokenSource,
  createId,
  liveKitSettingsKey,
  providerLabel,
  textConfigKey,
  validateSettings,
} from "./appUtils";
import { AppShell } from "./layout/AppShell";
import { ServerSelector } from "./components/ServerSelector";
import {
  BrowserLocationSharing,
  locationSharingStorageKey,
} from "./location";
import { AccountSettingsPage } from "./pages/AccountSettingsPage";
import { AgentKnowledgePage } from "./pages/AgentKnowledgePage";
import { AgentSettingsPage } from "./pages/AgentSettingsPage";
import { AuthGate } from "./pages/AuthGate";
import { BrowserAccountsPage } from "./pages/BrowserAccountsPage";
import { ChatPage } from "./pages/ChatPage";
import { ConnectionSettingsPage } from "./pages/ConnectionSettingsPage";
import { ContactsPage } from "./pages/ContactsPage";
import { CodexCloudPage } from "./pages/CodexCloudPage";
import { OperationsPage } from "./pages/OperationsPage";
import { ProviderSettingsPage } from "./pages/ProviderSettingsPage";
import { IntegrationsPage } from "./pages/IntegrationsPage";
import { McpSettingsPage } from "./pages/McpSettingsPage";
import { ToolsSettingsPage } from "./pages/ToolsSettingsPage";
import { VoicePage } from "./pages/VoicePage";
import {
  pageFromPathname,
  pagePaths,
  parseKnowledgePath,
  parseOperationsPath,
} from "./routing";
import {
  fetchServerConfig,
  loadServerSelection,
  rememberServer,
  type ActiveServer,
  type ServerSelection,
} from "./serverConfig";

function createInitialSettings(
  apiBaseUrl: string,
  agentName = DEFAULT_AGENT_NAME,
): ConnectionSettings {
  return {
    mode: "endpoint",
    tokenEndpoint: `${apiBaseUrl}/api/livekit/token`,
    sandboxId: "",
    livekitUrl: "",
    participantToken: "",
    agentName,
    roomName: createId("voxos-dev"),
    participantIdentity: createId("tester"),
    userId: "",
    livekitStreamingEnabled: true,
    startMicrophone: false,
  };
}

export function App() {
  const location = useLocation();
  const currentPage = pageFromPathname(location.pathname);
  const [serverSelection, setServerSelection] = useState<ServerSelection>(() =>
    loadServerSelection(defaultApiBaseUrl),
  );
  const initialServerUrl = useRef(serverSelection.activeUrl);
  const [activeServer, setActiveServer] = useState<ActiveServer | null>(null);
  const [isServerPickerOpen, setIsServerPickerOpen] = useState(false);
  const [isServerWorking, setIsServerWorking] = useState(true);
  const [serverError, setServerError] = useState<string | null>(null);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [settings, setSettings] = useState<ConnectionSettings>(() =>
    createInitialSettings(defaultApiBaseUrl),
  );
  const [activeSettings, setActiveSettings] =
    useState<ActiveConnectionSettings | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [firebaseUser, setFirebaseUser] = useState<User | null>(null);
  const locationSharing = useMemo(
    () =>
      new BrowserLocationSharing(
        locationSharingStorageKey(
          activeServer?.baseUrl ?? serverSelection.activeUrl,
          firebaseUser?.uid ?? "signed-out",
        ),
      ),
    [activeServer?.baseUrl, firebaseUser?.uid, serverSelection.activeUrl],
  );
  const [isAuthReady, setIsAuthReady] = useState(false);
  const [isAuthWorking, setIsAuthWorking] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [voiceSettings, setVoiceSettings] = useState<VoiceSettings | null>(null);
  const [voiceProviderSettings, setVoiceProviderSettings] =
    useState<VoiceProviderSettings | null>(null);
  const [isVoiceSettingsLoading, setIsVoiceSettingsLoading] = useState(false);
  const [isVoiceCredentialSaving, setIsVoiceCredentialSaving] = useState(false);
  const [isVoicePreferencesSaving, setIsVoicePreferencesSaving] = useState(false);
  const [voiceSettingsError, setVoiceSettingsError] = useState<string | null>(
    null,
  );
  const [textSettings, setTextSettings] = useState<TextSettings | null>(null);
  const [textProviderSettings, setTextProviderSettings] =
    useState<TextProviderSettings | null>(null);
  const [isTextSettingsLoading, setIsTextSettingsLoading] = useState(false);
  const [isTextCredentialSaving, setIsTextCredentialSaving] = useState(false);
  const [isTextPreferencesSaving, setIsTextPreferencesSaving] = useState(false);
  const [textSettingsError, setTextSettingsError] = useState<string | null>(
    null,
  );
  const [isImageGenerationSettingsSaving, setIsImageGenerationSettingsSaving] =
    useState(false);
  const [imageGenerationSettingsError, setImageGenerationSettingsError] = useState<
    string | null
  >(null);
  const [liveKitSettings, setLiveKitSettings] = useState<LiveKitSettings | null>(
    null,
  );
  const [isLiveKitSettingsLoading, setIsLiveKitSettingsLoading] = useState(false);
  const [isLiveKitSettingsSaving, setIsLiveKitSettingsSaving] = useState(false);
  const [liveKitSettingsError, setLiveKitSettingsError] = useState<string | null>(
    null,
  );

  const activateServer = useCallback(async (endpoint: string) => {
    setIsServerWorking(true);
    setServerError(null);
    setActiveSettings(null);
    try {
      const server = await fetchServerConfig(endpoint);
      await configureFirebase(server.firebase);
      setApiBaseUrl(server.baseUrl);
      setSettings(createInitialSettings(server.baseUrl, server.voiceAgentName));
      setServerSelection((current) => rememberServer(current, server.baseUrl));
      setFirebaseUser(null);
      setIsAuthReady(false);
      setAuthError(null);
      setActiveServer(server);
      setIsServerPickerOpen(false);
    } catch (error) {
      setServerError(
        error instanceof Error ? error.message : "Could not connect to that server.",
      );
      setIsServerPickerOpen(true);
    } finally {
      setIsServerWorking(false);
    }
  }, []);

  useEffect(() => {
    void activateServer(initialServerUrl.current);
  }, [activateServer]);

  useEffect(() => {
    if (activeServer === null) {
      return;
    }
    return subscribeToFirebaseUser((user) => {
      setFirebaseUser(user);
      setIsAuthReady(true);
      setVoiceSettings(null);
      setVoiceProviderSettings(null);
      setVoiceSettingsError(null);
      setTextSettings(null);
      setTextProviderSettings(null);
      setTextSettingsError(null);
      setLiveKitSettings(null);
      setLiveKitSettingsError(null);
    });
  }, [activeServer]);

  useEffect(() => {
    if (firebaseUser === null) {
      return;
    }

    let cancelled = false;

    const fetchSettings = async () => {
      setIsVoiceSettingsLoading(true);
      setVoiceSettingsError(null);

      try {
        const loadedSettings = await loadVoiceProviderSettings();
        if (!cancelled) {
          setVoiceProviderSettings(loadedSettings);
          setVoiceSettings(loadedSettings.active_settings);
        }
      } catch (error) {
        if (!cancelled) {
          setVoiceSettingsError(authErrorMessage(error));
        }
      } finally {
        if (!cancelled) {
          setIsVoiceSettingsLoading(false);
        }
      }
    };

    void fetchSettings();

    return () => {
      cancelled = true;
    };
  }, [firebaseUser]);

  useEffect(() => {
    if (firebaseUser === null) {
      return;
    }

    let cancelled = false;

    const fetchSettings = async () => {
      setIsTextSettingsLoading(true);
      setTextSettingsError(null);

      try {
        const loadedSettings = await loadTextProviderSettings();
        if (!cancelled) {
          setTextProviderSettings(loadedSettings);
          setTextSettings(loadedSettings.active_settings);
        }
      } catch (error) {
        if (!cancelled) {
          setTextSettingsError(authErrorMessage(error));
        }
      } finally {
        if (!cancelled) {
          setIsTextSettingsLoading(false);
        }
      }
    };

    void fetchSettings();

    return () => {
      cancelled = true;
    };
  }, [firebaseUser]);

  useEffect(() => {
    if (firebaseUser === null) {
      return;
    }

    let cancelled = false;

    const fetchSettings = async () => {
      setIsLiveKitSettingsLoading(true);
      setLiveKitSettingsError(null);

      try {
        const loadedSettings = await loadLiveKitSettings();
        if (!cancelled) {
          setLiveKitSettings(loadedSettings);
          if (loadedSettings.livekit_url !== null) {
            setSettings((current) =>
              current.livekitUrl.trim() === ""
                ? { ...current, livekitUrl: loadedSettings.livekit_url ?? "" }
                : current,
            );
          }
        }
      } catch (error) {
        if (!cancelled) {
          setLiveKitSettingsError(authErrorMessage(error));
        }
      } finally {
        if (!cancelled) {
          setIsLiveKitSettingsLoading(false);
        }
      }
    };

    void fetchSettings();

    return () => {
      cancelled = true;
    };
  }, [firebaseUser]);

  const addLog = useCallback((message: string, level: LogEntry["level"] = "info") => {
    setLogs((current) => [
      {
        id: crypto.randomUUID(),
        level,
        message,
      },
      ...current.slice(0, 11),
    ]);
  }, []);

  const updateSettings: ConnectionSettingsUpdater = (key, value) => {
    setSettings((current) => ({ ...current, [key]: value }));
  };

  const resetRoom = () => {
    setSettings((current) => ({
      ...current,
      roomName: createId("voxos-dev"),
      participantIdentity: createId("tester"),
    }));
  };

  const runAuthAction = async (action: () => Promise<void>) => {
    setIsAuthWorking(true);
    setAuthError(null);

    try {
      await action();
    } catch (error) {
      setAuthError(authErrorMessage(error));
    } finally {
      setIsAuthWorking(false);
    }
  };

  const submitEmailAuth = async (
    mode: AuthFormMode,
    email: string,
    password: string,
  ) => {
    await runAuthAction(async () => {
      if (mode === "create") {
        await createEmailAccount(email, password);
        return;
      }

      await signInWithEmail(email, password);
    });
  };

  const signOutUser = async () => {
    await runAuthAction(signOutOfFirebase);
    setActiveSettings(null);
  };

  const saveVoiceProviderKey = async ({
    apiKey,
    provider,
  }: VoiceProviderKeyFormValues): Promise<boolean> => {
    setIsVoiceCredentialSaving(true);
    setVoiceSettingsError(null);

    try {
      const savedSettings = await saveVoiceProviderKeyRequest({
        activate: false,
        api_key: apiKey,
        provider,
      });
      setVoiceProviderSettings(savedSettings);
      setVoiceSettings(savedSettings.active_settings);
      try {
        const refreshedTextSettings = await loadTextProviderSettings();
        setTextProviderSettings(refreshedTextSettings);
        setTextSettings(refreshedTextSettings.active_settings);
      } catch (refreshError) {
        const message = authErrorMessage(refreshError);
        setTextSettingsError(`Key saved, but text provider availability could not refresh: ${message}`);
        addLog(`Text provider availability refresh failed: ${message}`, "error");
      }
      addLog(`${providerLabel(savedSettings, provider)} key saved.`);
      return true;
    } catch (error) {
      const message = authErrorMessage(error);
      setVoiceSettingsError(message);
      addLog(`Voice provider key save failed: ${message}`, "error");
      return false;
    } finally {
      setIsVoiceCredentialSaving(false);
    }
  };

  const saveTextProviderKey = async ({
    apiKey,
    provider,
  }: TextProviderKeyFormValues): Promise<boolean> => {
    setIsTextCredentialSaving(true);
    setTextSettingsError(null);

    try {
      const savedSettings = await saveTextProviderKeyRequest({
        activate: false,
        api_key: apiKey,
        provider,
      });
      setTextProviderSettings(savedSettings);
      setTextSettings(savedSettings.active_settings);
      const label =
        savedSettings.providers.find((summary) => summary.provider === provider)?.label ??
        provider;
      addLog(`${label} key saved.`);
      return true;
    } catch (error) {
      const message = authErrorMessage(error);
      setTextSettingsError(message);
      addLog(`Text provider key save failed: ${message}`, "error");
      return false;
    } finally {
      setIsTextCredentialSaving(false);
    }
  };

  const saveOpenAICompatibleProvider = async ({
    apiKey,
    baseUrl,
    models,
  }: OpenAICompatibleProviderFormValues): Promise<boolean> => {
    setIsTextCredentialSaving(true);
    setTextSettingsError(null);

    try {
      const savedSettings = await saveOpenAICompatibleProviderRequest({
        api_key: apiKey.trim() || null,
        available_models: models,
        base_url: baseUrl,
      });
      setTextProviderSettings(savedSettings);
      setTextSettings(savedSettings.active_settings);
      addLog("OpenAI-compatible endpoint saved.");
      return true;
    } catch (error) {
      const message = authErrorMessage(error);
      setTextSettingsError(message);
      addLog(`OpenAI-compatible endpoint save failed: ${message}`, "error");
      return false;
    } finally {
      setIsTextCredentialSaving(false);
    }
  };

  const saveVoicePreferences = async ({
    agentInstructions,
    initialReplyInstructions,
    model,
    preferredLanguage,
    provider,
    voice,
  }: VoicePreferencesFormValues): Promise<boolean> => {
    setIsVoicePreferencesSaving(true);
    setVoiceSettingsError(null);

    try {
      const savedSettings = await saveVoiceSettings({
        agent_instructions: agentInstructions,
        initial_reply_instructions: initialReplyInstructions || null,
        model,
        preferred_language: preferredLanguage,
        provider,
        voice,
      });
      setVoiceSettings(savedSettings);
      setVoiceProviderSettings((current) =>
        current === null
          ? current
          : {
              ...current,
              active_provider: savedSettings.provider,
              active_settings: savedSettings,
              providers: current.providers.map((provider) => ({
                ...provider,
                active: provider.provider === savedSettings.provider,
              })),
            },
      );
      addLog("Voice agent config saved.");
      return true;
    } catch (error) {
      const message = authErrorMessage(error);
      setVoiceSettingsError(message);
      addLog(`Voice config save failed: ${message}`, "error");
      return false;
    } finally {
      setIsVoicePreferencesSaving(false);
    }
  };

  const saveTextPreferences = async ({
    agentInstructions,
    allowedSubagentModels,
    model,
    preferredLanguage,
    provider,
    subagentModel,
    temperature,
  }: TextPreferencesFormValues): Promise<boolean> => {
    setIsTextPreferencesSaving(true);
    setTextSettingsError(null);

    try {
      const savedSettings = await saveTextSettings({
        agent_instructions: agentInstructions,
        allowed_subagent_models: allowedSubagentModels,
        model,
        preferred_language: preferredLanguage,
        provider,
        subagent_model: subagentModel,
        temperature,
      });
      setTextSettings(savedSettings);
      setTextProviderSettings((current) =>
        current === null
          ? current
          : {
              ...current,
              active_provider: savedSettings.provider,
              active_settings: savedSettings,
              providers: current.providers.map((providerSummary) => ({
                ...providerSummary,
                active: providerSummary.provider === savedSettings.provider,
              })),
            },
      );
      addLog("Text agent defaults saved.");
      return true;
    } catch (error) {
      const message = authErrorMessage(error);
      setTextSettingsError(message);
      addLog(`Text defaults save failed: ${message}`, "error");
      return false;
    } finally {
      setIsTextPreferencesSaving(false);
    }
  };

  const saveImageGenerationPreferences = async ({
    model,
    provider,
  }: ImageGenerationPreferencesFormValues): Promise<boolean> => {
    setIsImageGenerationSettingsSaving(true);
    setImageGenerationSettingsError(null);

    try {
      const savedSettings = await saveImageGenerationSettings({
        model,
        provider,
      });
      setTextSettings(savedSettings);
      setTextProviderSettings((current) =>
        current === null
          ? current
          : {
              ...current,
              active_settings: savedSettings,
            },
      );
      addLog("Image generation defaults saved.");
      return true;
    } catch (error) {
      const message = authErrorMessage(error);
      setImageGenerationSettingsError(message);
      addLog(`Image generation defaults save failed: ${message}`, "error");
      return false;
    } finally {
      setIsImageGenerationSettingsSaving(false);
    }
  };

  const saveLiveKitCredentials = async ({
    livekitApiKey,
    livekitApiSecret,
    livekitUrl,
  }: LiveKitSettingsFormValues): Promise<boolean> => {
    setIsLiveKitSettingsSaving(true);
    setLiveKitSettingsError(null);

    try {
      const savedSettings = await saveLiveKitSettingsRequest({
        livekit_api_key: livekitApiKey,
        livekit_api_secret: livekitApiSecret,
        livekit_url: livekitUrl,
      });
      setLiveKitSettings(savedSettings);
      if (savedSettings.livekit_url !== null) {
        setSettings((current) => ({
          ...current,
          livekitUrl: savedSettings.livekit_url ?? current.livekitUrl,
        }));
      }
      addLog("LiveKit credentials saved.");
      return true;
    } catch (error) {
      const message = authErrorMessage(error);
      setLiveKitSettingsError(message);
      addLog(`LiveKit credential save failed: ${message}`, "error");
      return false;
    } finally {
      setIsLiveKitSettingsSaving(false);
    }
  };

  const startSession = async () => {
    if (firebaseUser === null) {
      addLog("Sign in before starting a voice session.", "error");
      return;
    }

    const activeProvider = activeProviderSummary(voiceProviderSettings, voiceSettings);
    if (settings.mode === "endpoint" && voiceSettings?.configured !== true) {
      addLog(
        `Save your ${activeProvider?.label ?? "voice provider"} key before starting voice.`,
        "error",
      );
      return;
    }

    if (settings.mode === "endpoint" && activeProvider?.runtime_supported === false) {
      addLog(
        `${activeProvider.label} is saved but still needs a backend voice adapter.`,
        "error",
      );
      return;
    }

    const error = validateSettings(settings);
    if (error) {
      addLog(error, "error");
      return;
    }

    let firebaseIdToken: string | null = null;
    if (settings.mode === "endpoint") {
      try {
        firebaseIdToken = await getFirebaseIdToken();
      } catch (tokenError) {
        addLog(authErrorMessage(tokenError), "error");
        return;
      }
    }

    const currentLocation = await locationSharing.snapshotForAction();
    if (
      locationSharing.isEnabled &&
      currentLocation === null &&
      locationSharing.lastError !== null
    ) {
      addLog(
        `Location unavailable; starting voice without it: ${locationSharing.lastError.message}`,
        "error",
      );
    }

    setActiveSettings({
      ...settings,
      currentLocation,
      firebaseIdToken,
    });
  };

  const finishSession = () => {
    setActiveSettings(null);
  };

  if (activeServer === null || isServerPickerOpen) {
    return (
      <ServerSelector
        activeUrl={activeServer?.baseUrl ?? serverSelection.activeUrl}
        error={serverError}
        isWorking={isServerWorking}
        onCancel={
          activeServer === null
            ? undefined
            : () => {
                setServerError(null);
                setIsServerPickerOpen(false);
              }
        }
        onSelect={activateServer}
        servers={serverSelection.servers}
      />
    );
  }

  if (!isAuthReady || firebaseUser === null) {
    return (
      <AuthGate
        authError={authError}
        isAuthReady={isAuthReady}
        isAuthWorking={isAuthWorking}
        onChangeServer={() => setIsServerPickerOpen(true)}
        onApple={() => runAuthAction(signInWithApple)}
        onEmailSubmit={submitEmailAuth}
        onGoogle={() => runAuthAction(signInWithGoogle)}
        serverUrl={activeServer.baseUrl}
      />
    );
  }

  const activeProvider = activeProviderSummary(voiceProviderSettings, voiceSettings);
  const commonPageProps: CommonPageProps = {
    activeProvider,
    addLog,
    changeServer: () => setIsServerPickerOpen(true),
    firebaseUser,
    imageGenerationSettingsError,
    isAuthWorking,
    isImageGenerationSettingsSaving,
    isLiveKitSettingsLoading,
    isLiveKitSettingsSaving,
    isTextCredentialSaving,
    isTextPreferencesSaving,
    isTextSettingsLoading,
    isVoiceCredentialSaving,
    isVoicePreferencesSaving,
    isVoiceSettingsLoading,
    liveKitSettings,
    liveKitSettingsError,
    logs,
    locationSharing,
    resetRoom,
    saveLiveKitCredentials,
    saveImageGenerationPreferences,
    saveOpenAICompatibleProvider,
    saveTextProviderKey,
    saveTextPreferences,
    saveVoiceProviderKey,
    saveVoicePreferences,
    serverUrl: activeServer.baseUrl,
    signOutUser,
    textProviderSettings,
    textSettings,
    textSettingsError,
    updateSettings,
    voiceProviderSettings,
    voiceSettings,
    voiceSettingsError,
  };

  if (activeSettings) {
    return (
      <ActiveSessionApp
        commonPageProps={commonPageProps}
        currentPage={currentPage}
        firebaseUser={firebaseUser}
        isSidebarCollapsed={isSidebarCollapsed}
        onSessionFinished={finishSession}
        onToggleSidebar={() => setIsSidebarCollapsed((collapsed) => !collapsed)}
        settings={activeSettings}
      />
    );
  }

  return (
    <AppShell
      connectionState={ConnectionState.Disconnected}
      currentPage={currentPage}
      firebaseUser={firebaseUser}
      isConnected={false}
      isSidebarCollapsed={isSidebarCollapsed}
      onToggleSidebar={() => setIsSidebarCollapsed((collapsed) => !collapsed)}
    >
      <AuthenticatedRoutes
        {...commonPageProps}
        canEdit={true}
        connectionState={ConnectionState.Disconnected}
        endSession={async () => undefined}
        isConnected={false}
        isConnecting={false}
        settings={settings}
        startSession={startSession}
      />
    </AppShell>
  );
}

type ActiveSessionAppProps = {
  commonPageProps: CommonPageProps;
  currentPage: AppPage;
  firebaseUser: User;
  isSidebarCollapsed: boolean;
  onSessionFinished: () => void;
  onToggleSidebar: () => void;
  settings: ActiveConnectionSettings;
};

type CommonPageProps = Omit<
  PageRenderProps,
  | "canEdit"
  | "connectionState"
  | "endSession"
  | "isConnected"
  | "isConnecting"
  | "settings"
  | "startSession"
>;

function ActiveSessionApp({
  commonPageProps,
  currentPage,
  firebaseUser,
  isSidebarCollapsed,
  onSessionFinished,
  onToggleSidebar,
  settings,
}: ActiveSessionAppProps) {
  const tokenSource = useMemo(() => buildTokenSource(settings), [settings]);
  const sessionOptions = useMemo(
    () => buildLiveKitSessionOptions(settings),
    [settings],
  );
  const session = useSession(tokenSource, sessionOptions);
  const sessionRef = useRef(session);
  const settingsRef = useRef(settings);
  const addLogRef = useRef(commonPageProps.addLog);
  const onSessionFinishedRef = useRef(onSessionFinished);
  const hasStartedSessionRef = useRef(false);
  const isFinishingSessionRef = useRef(false);

  useEffect(() => {
    sessionRef.current = session;
    settingsRef.current = settings;
    addLogRef.current = commonPageProps.addLog;
    onSessionFinishedRef.current = onSessionFinished;
  });

  const finishActiveSession = useCallback((logMessage?: string) => {
    if (isFinishingSessionRef.current) {
      return;
    }

    isFinishingSessionRef.current = true;
    if (logMessage !== undefined) {
      addLogRef.current(logMessage);
    }
    onSessionFinishedRef.current();
  }, []);

  useEffect(() => {
    let cancelled = false;

    const connect = async () => {
      const currentSettings = settingsRef.current;
      const currentSession = sessionRef.current;

      try {
        addLogRef.current(
          `Connecting ${currentSettings.participantIdentity.trim()} to ${currentSettings.roomName.trim()}.`,
        );
        await currentSession.start({
          tracks: {
            microphone: {
              enabled: currentSettings.startMicrophone,
            },
          },
          roomConnectOptions: {
            autoSubscribe: true,
          },
        });

        if (!cancelled && !isFinishingSessionRef.current) {
          hasStartedSessionRef.current = true;
          addLogRef.current("Session connected.");

          if (sessionRef.current.connectionState === ConnectionState.Disconnected) {
            finishActiveSession("Session disconnected.");
          }
        }
      } catch (error) {
        if (cancelled || isFinishingSessionRef.current) {
          return;
        }

        const message = error instanceof Error ? error.message : String(error);
        addLogRef.current(`Connect failed: ${message}`, "error");
        finishActiveSession();
      }
    };

    void connect();

    return () => {
      cancelled = true;
      void sessionRef.current.end();
    };
  }, [finishActiveSession]);

  useEffect(() => {
    if (
      !hasStartedSessionRef.current ||
      session.connectionState !== ConnectionState.Disconnected
    ) {
      return;
    }

    finishActiveSession("Session disconnected.");
  }, [finishActiveSession, session.connectionState]);

  const endSession = async () => {
    if (isFinishingSessionRef.current) {
      return;
    }

    isFinishingSessionRef.current = true;
    try {
      await session.end();
      commonPageProps.addLog("Session disconnected.");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      commonPageProps.addLog(`Disconnect failed: ${message}`, "error");
    } finally {
      onSessionFinished();
    }
  };

  return (
    <SessionProvider session={session}>
      <AppShell
        connectionState={session.connectionState}
        currentPage={currentPage}
        firebaseUser={firebaseUser}
        isConnected={session.isConnected}
        isSidebarCollapsed={isSidebarCollapsed}
        onToggleSidebar={onToggleSidebar}
      >
        <AuthenticatedRoutes
          {...commonPageProps}
          canEdit={false}
          connectionState={session.connectionState}
          endSession={endSession}
          isConnected={session.isConnected}
          isConnecting={session.connectionState === ConnectionState.Connecting}
          settings={settings}
          startSession={async () => undefined}
        />
      </AppShell>
      <RoomAudioRenderer />
    </SessionProvider>
  );
}

type PageRenderProps = {
  activeProvider: VoiceProviderSummary | null;
  addLog: (message: string, level?: LogEntry["level"]) => void;
  canEdit: boolean;
  changeServer: () => void;
  connectionState: ConnectionState;
  endSession: () => Promise<void>;
  firebaseUser: User;
  imageGenerationSettingsError: string | null;
  isAuthWorking: boolean;
  isImageGenerationSettingsSaving: boolean;
  isConnected: boolean;
  isConnecting: boolean;
  isLiveKitSettingsLoading: boolean;
  isLiveKitSettingsSaving: boolean;
  isTextCredentialSaving: boolean;
  isTextPreferencesSaving: boolean;
  isTextSettingsLoading: boolean;
  isVoiceCredentialSaving: boolean;
  isVoicePreferencesSaving: boolean;
  isVoiceSettingsLoading: boolean;
  liveKitSettings: LiveKitSettings | null;
  liveKitSettingsError: string | null;
  logs: LogEntry[];
  locationSharing: BrowserLocationSharing;
  resetRoom: () => void;
  saveLiveKitCredentials: (values: LiveKitSettingsFormValues) => Promise<boolean>;
  saveImageGenerationPreferences: (
    values: ImageGenerationPreferencesFormValues,
  ) => Promise<boolean>;
  saveOpenAICompatibleProvider: (
    values: OpenAICompatibleProviderFormValues,
  ) => Promise<boolean>;
  saveTextProviderKey: (values: TextProviderKeyFormValues) => Promise<boolean>;
  saveTextPreferences: (values: TextPreferencesFormValues) => Promise<boolean>;
  saveVoiceProviderKey: (values: VoiceProviderKeyFormValues) => Promise<boolean>;
  saveVoicePreferences: (values: VoicePreferencesFormValues) => Promise<boolean>;
  serverUrl: string;
  settings: ConnectionSettings;
  signOutUser: () => Promise<void>;
  startSession: () => Promise<void>;
  textProviderSettings: TextProviderSettings | null;
  textSettings: TextSettings | null;
  textSettingsError: string | null;
  updateSettings: ConnectionSettingsUpdater;
  voiceProviderSettings: VoiceProviderSettings | null;
  voiceSettings: VoiceSettings | null;
  voiceSettingsError: string | null;
};

function AuthenticatedRoutes({
  activeProvider,
  addLog,
  canEdit,
  changeServer,
  connectionState,
  endSession,
  firebaseUser,
  imageGenerationSettingsError,
  isAuthWorking,
  isImageGenerationSettingsSaving,
  isConnected,
  isConnecting,
  isLiveKitSettingsLoading,
  isLiveKitSettingsSaving,
  isTextCredentialSaving,
  isTextPreferencesSaving,
  isTextSettingsLoading,
  isVoiceCredentialSaving,
  isVoicePreferencesSaving,
  isVoiceSettingsLoading,
  liveKitSettings,
  liveKitSettingsError,
  logs,
  locationSharing,
  resetRoom,
  saveLiveKitCredentials,
  saveImageGenerationPreferences,
  saveOpenAICompatibleProvider,
  saveTextProviderKey,
  saveTextPreferences,
  saveVoiceProviderKey,
  saveVoicePreferences,
  serverUrl,
  settings,
  signOutUser,
  startSession,
  textProviderSettings,
  textSettings,
  textSettingsError,
  updateSettings,
  voiceProviderSettings,
  voiceSettings,
  voiceSettingsError,
}: PageRenderProps) {
  return (
    <Routes>
      <Route element={<Navigate replace to={pagePaths.chat} />} index />
      <Route
        path="chat"
        element={
          <ChatPage
            addLog={addLog}
            canEdit={canEdit}
            locationSharing={locationSharing}
          />
        }
      />
      <Route
        path="voice"
        element={
          <VoicePage
            canEdit={canEdit}
            connectionState={connectionState}
            endSession={endSession}
            isConnected={isConnected}
            isConnecting={isConnecting}
            logs={logs}
            provider={activeProvider}
            resetRoom={resetRoom}
            settings={settings}
            startSession={startSession}
            updateSettings={updateSettings}
            voiceSettings={voiceSettings}
          />
        }
      />
      <Route
        path="agent"
        element={
          <AgentSettingsPage
            canEdit={canEdit}
            imageGenerationSettingsError={imageGenerationSettingsError}
            isImageGenerationSaving={isImageGenerationSettingsSaving}
            isTextLoading={isTextSettingsLoading}
            isTextSaving={isTextPreferencesSaving}
            isVoiceLoading={isVoiceSettingsLoading}
            isVoiceSaving={isVoicePreferencesSaving}
            key={`${agentConfigKey(voiceSettings)}|${textConfigKey(textSettings)}`}
            onSaveImageGeneration={saveImageGenerationPreferences}
            onSaveText={saveTextPreferences}
            onSaveVoice={saveVoicePreferences}
            provider={activeProvider}
            providerSettings={voiceProviderSettings}
            settings={voiceSettings}
            settingsError={voiceSettingsError}
            textProviderSettings={textProviderSettings}
            textSettings={textSettings}
            textSettingsError={textSettingsError}
          />
        }
      />
      <Route
        path="knowledge/*"
        element={<AgentKnowledgeRoute canEdit={canEdit} onLog={addLog} />}
      />
      <Route
        path="operations/*"
        element={<OperationsRoute canEdit={canEdit} onLog={addLog} />}
      />
      <Route
        path="codex-cloud"
        element={<CodexCloudPage canEdit={canEdit} onLog={addLog} />}
      />
      <Route
        path="providers"
        element={
          <ProviderSettingsPage
            canEdit={canEdit}
            isTextCredentialSaving={isTextCredentialSaving}
            isTextSettingsLoading={isTextSettingsLoading}
            isVoiceCredentialSaving={isVoiceCredentialSaving}
            isVoiceSettingsLoading={isVoiceSettingsLoading}
            saveOpenAICompatibleProvider={saveOpenAICompatibleProvider}
            saveTextProviderKey={saveTextProviderKey}
            saveVoiceProviderKey={saveVoiceProviderKey}
            textProviderSettings={textProviderSettings}
            textSettingsError={textSettingsError}
            voiceProviderSettings={voiceProviderSettings}
            voiceSettingsError={voiceSettingsError}
          />
        }
      />
      <Route
        path="integrations"
        element={<IntegrationsPage canEdit={canEdit} onLog={addLog} />}
      />
      <Route
        path="contacts"
        element={<ContactsPage canEdit={canEdit} onLog={addLog} />}
      />
      <Route
        path="connection"
        element={
          <ConnectionSettingsPage
            canEdit={canEdit}
            isLiveKitSettingsLoading={isLiveKitSettingsLoading}
            isLiveKitSettingsSaving={isLiveKitSettingsSaving}
            key={liveKitSettingsKey(liveKitSettings)}
            liveKitSettings={liveKitSettings}
            liveKitSettingsError={liveKitSettingsError}
            saveLiveKitCredentials={saveLiveKitCredentials}
            settings={settings}
            updateSettings={updateSettings}
          />
        }
      />
      <Route
        path="tools"
        element={<ToolsSettingsPage canEdit={canEdit} onLog={addLog} />}
      />
      <Route
        path="mcp"
        element={<McpSettingsPage canEdit={canEdit} onLog={addLog} />}
      />
      <Route
        path="browser-accounts"
        element={<BrowserAccountsPage canEdit={canEdit} onLog={addLog} />}
      />
      <Route
        path="account"
        element={
          <AccountSettingsPage
            changeServer={changeServer}
            firebaseUser={firebaseUser}
            isAuthWorking={isAuthWorking}
            key={locationSharing.storageKey}
            locationSharing={locationSharing}
            serverUrl={serverUrl}
            signOutUser={signOutUser}
          />
        }
      />
      <Route path="*" element={<Navigate replace to={pagePaths.chat} />} />
    </Routes>
  );
}

type AgentKnowledgeRouteProps = {
  canEdit: boolean;
  onLog: (message: string, level?: LogEntry["level"]) => void;
};

function AgentKnowledgeRoute({ canEdit, onLog }: AgentKnowledgeRouteProps) {
  const params = useParams();
  const parsed = parseKnowledgePath(params["*"]);

  if (parsed === null) {
    return <Navigate replace to={pagePaths.knowledge} />;
  }

  if (parsed.shouldRedirect) {
    return <Navigate replace to={parsed.canonicalPath} />;
  }

  return <AgentKnowledgePage activeTab={parsed.tab} canEdit={canEdit} onLog={onLog} />;
}

type OperationsRouteProps = {
  canEdit: boolean;
  onLog: (message: string, level?: LogEntry["level"]) => void;
};

function OperationsRoute({ canEdit, onLog }: OperationsRouteProps) {
  const params = useParams();
  const parsed = parseOperationsPath(params["*"]);

  if (parsed === null) {
    return <Navigate replace to={pagePaths.operations} />;
  }

  if (parsed.shouldRedirect) {
    return <Navigate replace to={parsed.canonicalPath} />;
  }

  return <OperationsPage activeTab={parsed.tab} canEdit={canEdit} onLog={onLog} />;
}
