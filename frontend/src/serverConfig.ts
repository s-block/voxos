export type FirebaseServerConfig = {
  apiKey: string;
  authDomain: string | null;
  messagingSenderId: string;
  projectId: string;
  storageBucket: string | null;
  webAppId: string | null;
};

export type ActiveServer = {
  baseUrl: string;
  firebase: FirebaseServerConfig;
  voiceAgentName: string;
};

export type ServerSelection = {
  activeUrl: string;
  servers: string[];
};

const storageKey = "voxos_servers_v1";
const requestTimeoutMilliseconds = 15_000;

export function normalizeServerUrl(input: string): string {
  const trimmed = input.trim();
  if (trimmed === "") {
    throw new Error("Enter your VoxOS server address.");
  }

  const withScheme = /^[a-z][a-z\d+.-]*:\/\//i.test(trimmed)
    ? trimmed
    : `https://${trimmed}`;
  let url: URL;
  try {
    url = new URL(withScheme);
  } catch {
    throw new Error("Enter a valid server address.");
  }

  const isLocalHttp =
    url.protocol === "http:" &&
    (url.hostname === "localhost" ||
      url.hostname === "127.0.0.1" ||
      url.hostname === "[::1]");
  if (url.protocol !== "https:" && !isLocalHttp) {
    throw new Error("The server address must use HTTPS.");
  }
  if (
    url.username !== "" ||
    url.password !== "" ||
    url.search !== "" ||
    url.hash !== ""
  ) {
    throw new Error(
      "Use only the server address, without credentials, a query, or a fragment.",
    );
  }
  if (url.pathname !== "" && url.pathname !== "/") {
    throw new Error("Use the deployment root, without an API path.");
  }

  return url.origin;
}

export function loadServerSelection(
  defaultUrl: string,
  storage: Storage = window.localStorage,
): ServerSelection {
  const normalizedDefault = normalizeServerUrl(defaultUrl);
  const servers = [normalizedDefault];
  let storedActiveUrl: string | null = null;

  try {
    const stored = storage.getItem(storageKey);
    if (stored !== null) {
      const decoded = JSON.parse(stored) as unknown;
      if (isRecord(decoded)) {
        if (Array.isArray(decoded.servers)) {
          for (const value of decoded.servers) {
            if (typeof value !== "string") {
              continue;
            }
            try {
              const normalized = normalizeServerUrl(value);
              if (!servers.includes(normalized)) {
                servers.push(normalized);
              }
            } catch {
              // Ignore only the malformed entry and retain the other servers.
            }
          }
        }
        if (typeof decoded.activeUrl === "string") {
          storedActiveUrl = normalizeServerUrl(decoded.activeUrl);
        }
      }
    }
  } catch {
    // A malformed or unavailable local store falls back to the deployment default.
  }

  const activeUrl =
    storedActiveUrl !== null && servers.includes(storedActiveUrl)
      ? storedActiveUrl
      : normalizedDefault;
  return { activeUrl, servers };
}

export function rememberServer(
  selection: ServerSelection,
  input: string,
  storage: Storage = window.localStorage,
): ServerSelection {
  const activeUrl = normalizeServerUrl(input);
  const servers = selection.servers.includes(activeUrl)
    ? [...selection.servers]
    : [...selection.servers, activeUrl];
  const updated = { activeUrl, servers };
  try {
    storage.setItem(storageKey, JSON.stringify(updated));
  } catch {
    // Keep the in-memory selection usable when browser storage is unavailable.
  }
  return updated;
}

export async function fetchServerConfig(input: string): Promise<ActiveServer> {
  const baseUrl = normalizeServerUrl(input);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), requestTimeoutMilliseconds);
  let response: Response;
  try {
    response = await fetch(`${baseUrl}/api/client-config`, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
  } catch {
    throw new Error(
      "Could not reach that VoxOS server. Check the address and try again.",
    );
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    throw new Error(await serverError(response));
  }

  let decoded: unknown;
  try {
    decoded = await response.json();
  } catch {
    throw new Error("That server returned invalid client configuration.");
  }
  if (!isRecord(decoded) || decoded.schema_version !== 1) {
    throw new Error("That server returned invalid client configuration.");
  }
  const firebase = decoded.firebase;
  const voice = decoded.voice;
  if (!isRecord(firebase) || !isRecord(voice)) {
    throw new Error("That server returned invalid client configuration.");
  }

  return {
    baseUrl,
    firebase: {
      apiKey: requiredString(firebase, "api_key"),
      authDomain: optionalString(firebase, "auth_domain"),
      messagingSenderId: requiredString(firebase, "messaging_sender_id"),
      projectId: requiredString(firebase, "project_id"),
      storageBucket: optionalString(firebase, "storage_bucket"),
      webAppId: optionalString(firebase, "web_app_id"),
    },
    voiceAgentName: requiredString(voice, "agent_name"),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(value: Record<string, unknown>, key: string): string {
  const field = value[key];
  if (typeof field !== "string" || field.trim() === "") {
    throw new Error("That server returned invalid client configuration.");
  }
  return field.trim();
}

function optionalString(
  value: Record<string, unknown>,
  key: string,
): string | null {
  const field = value[key];
  if (field === undefined || field === null || field === "") {
    return null;
  }
  if (typeof field !== "string") {
    throw new Error("That server returned invalid client configuration.");
  }
  return field.trim() || null;
}

async function serverError(response: Response): Promise<string> {
  if (response.status === 404) {
    return "That deployment does not support server switching yet.";
  }
  try {
    const decoded = (await response.json()) as unknown;
    if (isRecord(decoded) && typeof decoded.detail === "string") {
      const detail = decoded.detail.trim();
      if (detail !== "") {
        return detail;
      }
    }
  } catch {
    // Use the safe fallback below.
  }
  return "The VoxOS server could not provide client configuration.";
}
