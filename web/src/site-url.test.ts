import { describe, expect, it } from "vitest";
import { LOCAL_SITE_URL, siteUrlFromEnv } from "./site-url";

describe("siteUrlFromEnv", () => {
  it("points at the dev server when the setting is missing or blank", () => {
    expect(siteUrlFromEnv(undefined).origin).toBe(LOCAL_SITE_URL);
    expect(siteUrlFromEnv("  ").origin).toBe(LOCAL_SITE_URL);
  });

  it("normalises the configured origin", () => {
    expect(siteUrlFromEnv(" https://Example.COM/ ").origin).toBe("https://example.com");
  });

  it.each([
    "example.com",
    "ftp://example.com",
    "https://example.com/docs/",
    "https://example.com/?ref=home",
    "https://example.com/#top",
    "https://deploy@example.com",
  ])("rejects %s", (value) => {
    expect(() => siteUrlFromEnv(value)).toThrow("WEB_SITE_URL");
  });
});
