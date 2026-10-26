import { afterEach, describe, expect, it, vi } from "vitest";
import type { BrandLogo } from "./api";
import { logoImageUrl, logoInitials } from "./serviceLogos";

const openAiLogo: BrandLogo = {
  domain: "OpenAI.com",
  label: "OpenAI",
  source: "domain",
};

describe("logoImageUrl", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("builds a Logo.dev image URL with the publishable key", () => {
    vi.stubEnv("VITE_LOGODEV_PUBLISHABLE_KEY", "pk_test");

    expect(logoImageUrl(openAiLogo)).toBe(
      "https://img.logo.dev/openai.com?format=png&size=64&token=pk_test",
    );
  });

  it("returns null when the publishable key is missing", () => {
    vi.stubEnv("VITE_LOGODEV_PUBLISHABLE_KEY", "");

    expect(logoImageUrl(openAiLogo)).toBeNull();
  });

  it("rejects unsafe domains", () => {
    vi.stubEnv("VITE_LOGODEV_PUBLISHABLE_KEY", "pk_test");

    expect(
      logoImageUrl({
        domain: "https://openai.com/?token=secret",
        label: "OpenAI",
        source: "domain",
      }),
    ).toBeNull();
  });
});

describe("logoInitials", () => {
  it("builds deterministic initials from labels", () => {
    expect(logoInitials("Google Workspace")).toBe("GW");
    expect(logoInitials("xAI")).toBe("XA");
    expect(logoInitials("")).toBe("?");
  });
});
