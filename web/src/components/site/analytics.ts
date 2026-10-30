// Google Analytics behind an opt-in choice. Nothing from Google loads until the
// visitor allows analytics, and only on the site's own host, so dev servers,
// previews and forks never send data.

type Choice = "granted" | "denied";

declare global {
  interface Window {
    dataLayer?: unknown[];
    gtag?: (...args: unknown[]) => void;
    /** While true, Google Analytics sends nothing for that measurement ID. */
    [optOut: `ga-disable-${string}`]: boolean | undefined;
  }
}

type GtagWindow = Pick<Window, "dataLayer" | "gtag" | `ga-disable-${string}`>;

const STORAGE_KEY = "voxos-analytics-consent";

function readChoice(): Choice | undefined {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "granted" || value === "denied" ? value : undefined;
  } catch {
    return undefined;
  }
}

function saveChoice(choice: Choice): void {
  try {
    localStorage.setItem(STORAGE_KEY, choice);
  } catch {
    // Blocked storage keeps the choice for this page view only.
  }
}

/** Google Analytics for one measurement ID, following the visitor's current choice. */
export class GoogleAnalytics {
  private readonly page: GtagWindow;
  private readonly measurementId: string;
  private readonly loadScript: (src: string) => void;
  private allowed = false;

  constructor(page: GtagWindow, measurementId: string, loadScript: (src: string) => void) {
    this.page = page;
    this.measurementId = measurementId;
    this.loadScript = loadScript;
  }

  /** Whether gtag.js has loaded on this page; a loaded tag stays in memory until the page reloads. */
  get loaded(): boolean {
    return this.page.gtag !== undefined;
  }

  /** Starts collection: the first time by loading gtag.js, afterwards by granting consent again. */
  allow(): void {
    this.allowed = true;
    this.page[`ga-disable-${this.measurementId}`] = false;
    if (this.page.gtag) {
      this.page.gtag("consent", "update", { analytics_storage: "granted" });
      return;
    }
    const dataLayer = (this.page.dataLayer ??= []);
    this.page.gtag = function gtag() {
      // gtag.js expects each call's arguments object, not an array.
      // eslint-disable-next-line prefer-rest-params
      dataLayer.push(arguments);
    };
    this.page.gtag("consent", "default", {
      ad_storage: "denied",
      ad_user_data: "denied",
      ad_personalization: "denied",
      analytics_storage: "granted",
    });
    this.page.gtag("js", new Date());
    this.page.gtag("config", this.measurementId, { allow_google_signals: false, allow_ad_personalization_signals: false });
    this.loadScript(`https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(this.measurementId)}`);
  }

  /**
   * Stops collection: denies consent, sets the measurement ID's opt-out flag,
   * and sends no more events. A loaded tag can still send cookieless pings
   * until the page reloads, so callers reload when `loaded` was true.
   */
  decline(): void {
    this.allowed = false;
    this.page[`ga-disable-${this.measurementId}`] = true;
    this.page.gtag?.("consent", "update", { analytics_storage: "denied" });
  }

  /** Sends an event only while the visitor allows analytics. */
  track(name: string, params: Record<string, string>): void {
    if (this.allowed) this.page.gtag?.("event", name, params);
  }
}

function loadScript(src: string): void {
  const script = document.createElement("script");
  script.async = true;
  script.src = src;
  document.head.append(script);
}

/** The host and each parent domain a cookie for it may have been set on. */
export function cookieDomains(hostname: string): string[] {
  const labels = hostname.split(".");
  if (labels.length < 2 || /^[\d.]+$/.test(hostname) || hostname.includes(":")) return [hostname];
  return labels.slice(0, -1).map((_, index) => labels.slice(index).join("."));
}

function deleteAnalyticsCookies(): void {
  const names = document.cookie.split(";").map((cookie) => cookie.split("=")[0]?.trim() ?? "");
  for (const name of names.filter((cookie) => cookie.startsWith("_ga"))) {
    document.cookie = `${name}=; Max-Age=0; path=/`;
    for (const domain of cookieDomains(location.hostname)) {
      document.cookie = `${name}=; Max-Age=0; path=/; domain=${domain}`;
    }
  }
}

function closest<T extends Element>(target: EventTarget | null, selector: string): T | null {
  return target instanceof Element ? target.closest<T>(selector) : null;
}

export function initAnalytics(): void {
  const banner = document.querySelector<HTMLElement>("[data-analytics-consent]");
  const measurementId = banner?.dataset.measurementId;
  if (!banner || !measurementId || location.host !== banner.dataset.siteHost) return;

  const analytics = new GoogleAnalytics(window, measurementId, loadScript);
  const choice = readChoice();
  if (choice === "granted") analytics.allow();
  if (choice === undefined) banner.hidden = false;

  document.addEventListener("click", (event) => {
    const choiceButton = closest<HTMLButtonElement>(event.target, "[data-consent-choice]");
    const value = choiceButton?.dataset.consentChoice;
    if (value === "granted" || value === "denied") {
      saveChoice(value);
      banner.hidden = true;
      if (value === "granted") {
        analytics.allow();
        return;
      }
      const wasLoaded = analytics.loaded;
      analytics.decline();
      deleteAnalyticsCookies();
      // gtag.js cannot be unloaded, so reload; the reloaded page never loads it.
      if (wasLoaded) location.reload();
      return;
    }
    if (closest(event.target, "[data-consent-open]")) {
      banner.hidden = false;
      banner.querySelector("button")?.focus();
      return;
    }
    const tracked = closest<HTMLElement>(event.target, "[data-analytics-event]");
    const name = tracked?.dataset.analyticsEvent;
    if (name) analytics.track(name, tracked instanceof HTMLAnchorElement ? { link_url: tracked.href } : {});
  });
}
