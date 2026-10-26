import { useCallback, useEffect, useState } from "react";
import {
  loadAgents,
  loadDefaultAgent,
  loadToolGrants,
  saveToolGrants,
  type Agent,
  type ToolGrant,
  type ToolGrantListResponse,
  type ToolGrantUpdate,
  type ToolPolicy,
} from "./api";
import type { LogEntry } from "./appTypes";

type UseToolGrantsParams = {
  onLog: (message: string, level?: LogEntry["level"]) => void;
};

export type ToolGrantState = {
  grants: ToolGrant[];
  internalTools: ToolPolicy[];
  isLoading: boolean;
  isSaving: boolean;
  error: string | null;
  refreshGrants: () => Promise<void>;
  setGrant: (grant: ToolGrantUpdate) => Promise<void>;
  setGrantsForAllAgents: (grantUpdates: ToolGrantUpdate[]) => Promise<void>;
};

export function useToolGrants({ onLog }: UseToolGrantsParams): ToolGrantState {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [grants, setGrants] = useState<ToolGrant[]>([]);
  const [internalTools, setInternalTools] = useState<ToolPolicy[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshGrants = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const loadedAgent = await loadDefaultAgent();
      const loadedAgents = await loadAgents();
      const grantResponse = await loadToolGrants(loadedAgent.id);
      setAgent(loadedAgent);
      setAgents(mergeAgents(loadedAgent, loadedAgents));
      setGrants(grantResponse.grants);
      setInternalTools(grantResponse.internal_tools);
    } catch (loadError) {
      const message = errorMessage(loadError);
      setError(message);
      onLog(`Tool grants failed: ${message}`, "error");
    } finally {
      setIsLoading(false);
    }
  }, [onLog]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void refreshGrants();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [refreshGrants]);

  const setGrantsForAllAgents = async (
    grantUpdates: ToolGrantUpdate[],
  ): Promise<void> => {
    if (agent === null) {
      return;
    }
    const targetAgents = activeGrantTargets(agent, agents);
    if (targetAgents.length === 0 || grantUpdates.length === 0) {
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      let defaultAgentResponse: ToolGrantListResponse | null = null;
      for (const targetAgent of targetAgents) {
        const response = await saveToolGrants(targetAgent.id, grantUpdates);
        if (targetAgent.id === agent.id) {
          defaultAgentResponse = response;
        }
      }
      const response = defaultAgentResponse ?? (await loadToolGrants(agent.id));
      setGrants(response.grants);
      setInternalTools(response.internal_tools);
      onLog(`Tool grants saved for ${targetAgents.length} active agent(s).`);
    } catch (saveError) {
      const message = errorMessage(saveError);
      setError(message);
      onLog(`Tool grant save failed: ${message}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  const setGrant = async (grant: ToolGrantUpdate): Promise<void> => {
    await setGrantsForAllAgents([grant]);
  };

  return {
    error,
    grants,
    internalTools,
    isLoading,
    isSaving,
    refreshGrants,
    setGrant,
    setGrantsForAllAgents,
  };
}

export function isToolAllowed(
  grants: ToolGrant[],
  toolSource: ToolGrant["tool_source"],
  serverId: string | null,
  toolName: string,
): boolean {
  return grants.some(
    (grant) =>
      grant.tool_source === toolSource &&
      grant.mcp_server_id === serverId &&
      grant.tool_name === toolName &&
      grant.status === "allowed",
  );
}

function mergeAgents(defaultAgent: Agent, loadedAgents: Agent[]): Agent[] {
  const byId = new Map(loadedAgents.map((loadedAgent) => [loadedAgent.id, loadedAgent]));
  byId.set(defaultAgent.id, defaultAgent);
  return Array.from(byId.values());
}

function activeGrantTargets(defaultAgent: Agent, loadedAgents: Agent[]): Agent[] {
  const targets = mergeAgents(defaultAgent, loadedAgents).filter(
    (loadedAgent) => loadedAgent.status === "active",
  );
  return targets.length > 0 ? targets : [defaultAgent];
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
