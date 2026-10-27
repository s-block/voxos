import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchServerConfig,
  loadServerSelection,
  normalizeServerUrl,
  rememberServer,
} from "./serverConfig";

describe("server configuration", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("normalizes roots and rejects unsafe endpoints", () => {
    expect(normalizeServerUrl(" VOXOS.example.com:443/ ")).toBe(
      "https://voxos.example.com",
    );
    expect(normalizeServerUrl("http://localhost:7100/")).toBe(
      "http://localhost:7100",
    );
    expect(() => normalizeServerUrl("http://voxos.example.com")).toThrow(
      "must use HTTPS",
    );
    expect(() => normalizeServerUrl("https://voxos.example.com/api")).toThrow(
      "deployment root",
    );
  });

  it("stores unique servers and remembers the selected server", () => {
    let selection = loadServerSelection("http://localhost:7100");
    selection = rememberServer(selection, "https://one.example.com");
    selection = rememberServer(selection, "HTTPS://ONE.EXAMPLE.COM:443/");

    expect(selection).toEqual({
      activeUrl: "https://one.example.com",
      servers: ["http://localhost:7100", "https://one.example.com"],
    });
    expect(loadServerSelection("http://localhost:7100")).toEqual(selection);
  });

  it("fetches the public configuration needed by the active server", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          schema_version: 1,
          firebase: {
            api_key: "public-api-key", // pragma: allowlist secret
            auth_domain: "auth.example.com",
            messaging_sender_id: "123",
            project_id: "voxos-project",
            storage_bucket: "storage.example.com",
            web_app_id: "web-app-id",
          },
          voice: { agent_name: "voxos-agent" },
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchServerConfig("https://one.example.com/")).resolves.toEqual({
      baseUrl: "https://one.example.com",
      firebase: {
        apiKey: "public-api-key", // pragma: allowlist secret
        authDomain: "auth.example.com",
        messagingSenderId: "123",
        projectId: "voxos-project",
        storageBucket: "storage.example.com",
        webAppId: "web-app-id",
      },
      voiceAgentName: "voxos-agent",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://one.example.com/api/client-config",
      expect.objectContaining({ headers: { Accept: "application/json" } }),
    );
  });
});
