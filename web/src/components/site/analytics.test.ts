import { describe, expect, it } from "vitest";
import { cookieDomains, GoogleAnalytics } from "./analytics";

type Page = ConstructorParameters<typeof GoogleAnalytics>[0];

const ID = "G-TEST123";
const CLICK = { link_url: "https://github.com/" };

function calls(page: Page, command: string): unknown[][] {
  return (page.dataLayer ?? [])
    .map((call) => Array.from(call as ArrayLike<unknown>))
    .filter(([name]) => name === command);
}

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

describe("GoogleAnalytics", () => {
  it("loads nothing and sends nothing before the visitor allows analytics", () => {
    const page: Page = {};
    const scripts: string[] = [];
    const analytics = new GoogleAnalytics(page, ID, (src) => scripts.push(src));

    analytics.track("github_click", CLICK);

    expect(scripts).toEqual([]);
    expect(page.dataLayer).toBeUndefined();
    expect(analytics.loaded).toBe(false);
  });

  it("stops sending events and opts the measurement ID out when consent is withdrawn", () => {
    const page: Page = {};
    const analytics = new GoogleAnalytics(page, ID, () => undefined);

    analytics.allow();
    analytics.track("github_click", CLICK);
    analytics.decline();
    analytics.track("github_click", CLICK);

    expect(calls(page, "event")).toEqual([["event", "github_click", CLICK]]);
    expect(calls(page, "consent").at(-1)).toEqual(["consent", "update", { analytics_storage: "denied" }]);
    expect(page[`ga-disable-${ID}`]).toBe(true);
    expect(analytics.loaded).toBe(true);
  });

  it("resumes collection when the visitor allows analytics again after declining", () => {
    const page: Page = {};
    const scripts: string[] = [];
    const analytics = new GoogleAnalytics(page, ID, (src) => scripts.push(src));

    analytics.allow();
    analytics.decline();
    analytics.allow();
    analytics.track("github_click", CLICK);

    expect(scripts).toEqual([`https://www.googletagmanager.com/gtag/js?id=${ID}`]);
    expect(calls(page, "consent")).toEqual([
      [
        "consent",
        "default",
        { ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied", analytics_storage: "granted" },
      ],
      ["consent", "update", { analytics_storage: "denied" }],
      ["consent", "update", { analytics_storage: "granted" }],
    ]);
    expect(page[`ga-disable-${ID}`]).toBe(false);
    expect(calls(page, "event")).toEqual([["event", "github_click", CLICK]]);
  });
});
