import { useCallback, useEffect, useState } from "react";
import {
  loadDefaultAgent,
  loadToolRoutes,
  saveToolRoutes,
  type Agent,
  type ToolRouteCandidate,
  type ToolRouteDefinition,
  type ToolRoutePreference,
  type ToolRoutePreferenceListResponse,
  type ToolRoutePreferenceUpdate,
} from "./api";
import type { LogEntry } from "./appTypes";

type UseToolRoutesParams = {
  onLog: (message: string, level?: LogEntry["level"]) => void;
};

export type ToolRouteState = {
  definitions: ToolRouteDefinition[];
  preferences: ToolRoutePreference[];
  candidates: ToolRouteCandidate[];
  isLoading: boolean;
  isSaving: boolean;
  error: string | null;
  refreshRoutes: () => Promise<void>;
  setRoute: (preference: ToolRoutePreferenceUpdate) => Promise<void>;
};

export function useToolRoutes({ onLog }: UseToolRoutesParams): ToolRouteState {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [definitions, setDefinitions] = useState<ToolRouteDefinition[]>([]);
  const [preferences, setPreferences] = useState<ToolRoutePreference[]>([]);
  const [candidates, setCandidates] = useState<ToolRouteCandidate[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applyResponse = useCallback(
    (response: ToolRoutePreferenceListResponse): void => {
      setDefinitions(response.definitions);
      setPreferences(response.preferences);
      setCandidates(response.candidates);
    },
    [],
  );

  const refreshRoutes = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const loadedAgent = await loadDefaultAgent();
      const response = await loadToolRoutes(loadedAgent.id);
      setAgent(loadedAgent);
      applyResponse(response);
    } catch (loadError) {
      const message = errorMessage(loadError);
      setError(message);
      onLog(`Tool routes failed: ${message}`, "error");
    } finally {
      setIsLoading(false);
    }
  }, [applyResponse, onLog]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void refreshRoutes();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [refreshRoutes]);

  const setRoute = async (
    preference: ToolRoutePreferenceUpdate,
  ): Promise<void> => {
    if (agent === null) {
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      applyResponse(await saveToolRoutes(agent.id, [preference]));
      onLog("Tool route saved for the default agent.");
    } catch (saveError) {
      const message = errorMessage(saveError);
      setError(message);
      onLog(`Tool route save failed: ${message}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  return {
    candidates,
    definitions,
    error,
    isLoading,
    isSaving,
    preferences,
    refreshRoutes,
    setRoute,
  };
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Unknown tool route error";
}
