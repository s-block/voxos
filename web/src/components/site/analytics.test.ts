import { describe, expect, it } from "vitest";
import { cookieDomains, disableGoogleAnalytics, enableGoogleAnalytics } from "./analytics";

describe("cookieDomains", () => {
  it("lists the host and its parent domains", () => {
    expect(cookieDomains("docs.example.com")).toEqual(["docs.example.com", "example.com"]);
    expect(cookieDomains("example.com")).toEqual(["example.com"]);
  });

  it("keeps hosts without parent domains as they are", () => {
    expect(cookieDomains("localhost")).toEqual(["localhost"]);
    expect(cookieDomains("127.0.0.1")).toEqual(["127.0.0.1"]);
  });
});

describe("Google Analytics consent", () => {
  it("grants consent again when a visitor allows analytics after declining on the same page", () => {
    const page: { dataLayer?: unknown[]; gtag?: (...args: unknown[]) => void } = {};
    const scripts: string[] = [];
    const loadScript = (src: string) => scripts.push(src);

    enableGoogleAnalytics(page, "G-TEST123", loadScript);
    disableGoogleAnalytics(page);
    enableGoogleAnalytics(page, "G-TEST123", loadScript);

    const consentCalls = (page.dataLayer ?? [])
      .map((call) => Array.from(call as ArrayLike<unknown>))
      .filter(([command]) => command === "consent");
    expect(scripts).toEqual(["https://www.googletagmanager.com/gtag/js?id=G-TEST123"]);
    expect(consentCalls).toEqual([
      [
        "consent",
        "default",
        { ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied", analytics_storage: "granted" },
      ],
      ["consent", "update", { analytics_storage: "denied" }],
      ["consent", "update", { analytics_storage: "granted" }],
    ]);
  });
});
