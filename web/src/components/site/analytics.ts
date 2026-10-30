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

function loadGoogleAnalytics(measurementId: string): void {
  if (window.gtag) return;
  window.dataLayer = window.dataLayer ?? [];
  window.gtag = function gtag() {
    // gtag.js expects each call's arguments object, not an array.
    // eslint-disable-next-line prefer-rest-params
    window.dataLayer?.push(arguments);
  };
  window.gtag("consent", "default", {
    ad_storage: "denied",
    ad_user_data: "denied",
    ad_personalization: "denied",
    analytics_storage: "granted",
  });
  window.gtag("js", new Date());
  window.gtag("config", measurementId, { allow_google_signals: false, allow_ad_personalization_signals: false });
  const script = document.createElement("script");
  script.async = true;
  script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`;
  document.head.append(script);
}

/** The host and each parent domain a cookie for it may have been set on. */
export function cookieDomains(hostname: string): string[] {
  const labels = hostname.split(".");
  if (labels.length < 2 || /^[\d.]+$/.test(hostname) || hostname.includes(":")) return [hostname];
  return labels.slice(0, -1).map((_, index) => labels.slice(index).join("."));
}

function stopGoogleAnalytics(): void {
  window.gtag?.("consent", "update", { analytics_storage: "denied" });
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
  if (choice === "granted") loadGoogleAnalytics(measurementId);
  if (choice === undefined) banner.hidden = false;

  document.addEventListener("click", (event) => {
    const choiceButton = closest<HTMLButtonElement>(event.target, "[data-consent-choice]");
    const value = choiceButton?.dataset.consentChoice;
    if (value === "granted" || value === "denied") {
      saveChoice(value);
      banner.hidden = true;
      if (value === "granted") loadGoogleAnalytics(measurementId);
      else stopGoogleAnalytics();
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
