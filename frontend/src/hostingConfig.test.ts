import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { firebaseOptions } from "./firebaseConfig";
import type { FirebaseServerConfig } from "./serverConfig";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(frontendRoot, "..");
const config: FirebaseServerConfig = {
  apiKey: "public-test-key", // pragma: allowlist secret
  authDomain: "auth.example.com",
  messagingSenderId: "123",
  projectId: "example-project",
  storageBucket: "example-project.firebasestorage.app",
  webAppId: "test-web-app",
};

describe("hosting authentication configuration", () => {
  it("allows the Firebase popup helper script", () => {
    const hostingConfig = readFileSync(resolve(repositoryRoot, "firebase.json"), "utf8");
    expect(hostingConfig).toContain("script-src 'self' https://apis.google.com");
  });

  it("uses the selected server's custom auth domain and app configuration", () => {
    expect(firebaseOptions(config)).toMatchObject({
      appId: "test-web-app",
      authDomain: "auth.example.com",
      projectId: "example-project",
      storageBucket: "example-project.firebasestorage.app",
    });
  });

  it("derives the default Firebase auth domain from the selected project", () => {
    expect(firebaseOptions({ ...config, authDomain: null }).authDomain).toBe(
      "example-project.firebaseapp.com",
    );
  });

  it("requires the server's web app ID instead of borrowing another project's ID", () => {
    expect(() => firebaseOptions({ ...config, webAppId: null })).toThrow("web app configuration");
  });
});
