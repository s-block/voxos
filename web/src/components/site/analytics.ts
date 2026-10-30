// Google Analytics behind an opt-in choice. Nothing from Google loads until the
// visitor allows analytics, and only on the site's own host, so dev servers,
// previews and forks never send data.

type Choice = "granted" | "denied";

declare global {
  interface Window {
    dataLayer?: unknown[];
    gtag?: (...args: unknown[]) => void;
  }
}

type GtagWindow = Pick<Window, "dataLayer" | "gtag">;

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

/**
 * Turns Google Analytics on. The first call queues the consent defaults and
 * config and loads gtag.js; a later call, after the visitor declined in the
 * meantime, grants consent again.
 */
export function enableGoogleAnalytics(page: GtagWindow, measurementId: string, loadScript: (src: string) => void): void {
  if (page.gtag) {
    page.gtag("consent", "update", { analytics_storage: "granted" });
    return;
  }
  const dataLayer = (page.dataLayer ??= []);
  page.gtag = function gtag() {
    // gtag.js expects each call's arguments object, not an array.
    // eslint-disable-next-line prefer-rest-params
    dataLayer.push(arguments);
  };
  page.gtag("consent", "default", {
    ad_storage: "denied",
    ad_user_data: "denied",
    ad_personalization: "denied",
    analytics_storage: "granted",
  });
  page.gtag("js", new Date());
  page.gtag("config", measurementId, { allow_google_signals: false, allow_ad_personalization_signals: false });
  loadScript(`https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`);
}

/** Withdraws consent, so a loaded Google Analytics stops sending data and setting cookies. */
export function disableGoogleAnalytics(page: GtagWindow): void {
  page.gtag?.("consent", "update", { analytics_storage: "denied" });
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

  const choice = readChoice();
  if (choice === "granted") enableGoogleAnalytics(window, measurementId, loadScript);
  if (choice === undefined) banner.hidden = false;

  document.addEventListener("click", (event) => {
    const choiceButton = closest<HTMLButtonElement>(event.target, "[data-consent-choice]");
    const value = choiceButton?.dataset.consentChoice;
    if (value === "granted" || value === "denied") {
      saveChoice(value);
      banner.hidden = true;
      if (value === "granted") {
        enableGoogleAnalytics(window, measurementId, loadScript);
      } else {
        disableGoogleAnalytics(window);
        deleteAnalyticsCookies();
      }
      return;
    }
    if (closest(event.target, "[data-consent-open]")) {
      banner.hidden = false;
      banner.querySelector("button")?.focus();
      return;
    }
    const tracked = closest<HTMLElement>(event.target, "[data-analytics-event]");
    const name = tracked?.dataset.analyticsEvent;
    if (name && window.gtag) {
      window.gtag("event", name, tracked instanceof HTMLAnchorElement ? { link_url: tracked.href } : {});
    }
  });
}
