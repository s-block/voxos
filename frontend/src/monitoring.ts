import * as Sentry from "@sentry/react";
import { publicRuntimeConfig } from "./runtimeConfig";

export function sanitizeEvent<T extends Sentry.Event>(event: T): T {
  // Provider errors and console arguments can contain chats, keys and signed URLs.
  delete event.request;
  delete event.user;
  delete event.extra;
  delete event.breadcrumbs;
  delete event.message;
  delete event.logentry;
  delete event.tags;
  event.contexts = event.contexts?.trace ? { trace: event.contexts.trace } : {};
  if (event.contexts.trace) {
    delete event.contexts.trace.data;
    delete event.contexts.trace.description;
  }
  for (const exception of event.exception?.values ?? []) {
    delete exception.value;
    if (exception.mechanism) delete exception.mechanism.data;
    for (const frame of exception.stacktrace?.frames ?? []) {
      delete frame.vars;
    }
  }
  if (event.transaction) event.transaction = "web.navigation";
  if ("spans" in event) {
    for (const span of event.spans ?? []) {
      span.description = span.op;
      span.data = {};
    }
  }
  return event;
}

export function initializeMonitoring(): void {
  const dsn = publicRuntimeConfig.sentryDsn ?? import.meta.env.VITE_SENTRY_DSN;
  if (!dsn?.trim()) return;
  const rate = publicRuntimeConfig.sentryTracesSampleRate ?? 0.01;
  if (!Number.isFinite(rate) || rate < 0 || rate > 1) {
    throw new Error("Sentry trace sample rate must be between 0 and 1.");
  }
  Sentry.init({
    dsn,
    environment: publicRuntimeConfig.sentryEnvironment ?? (import.meta.env.PROD ? "production" : "development"),
    release: publicRuntimeConfig.sentryRelease,
    defaultIntegrations: false,
    integrations: [
      Sentry.globalHandlersIntegration(),
      Sentry.browserApiErrorsIntegration(),
      Sentry.linkedErrorsIntegration(),
      Sentry.dedupeIntegration(),
      Sentry.eventFiltersIntegration(),
      Sentry.browserTracingIntegration(),
    ],
    sampleRate: 1,
    tracesSampleRate: rate,
    tracePropagationTargets: [],
    dataCollection: {
      userInfo: false,
      cookies: false,
      httpHeaders: false,
      httpBodies: [],
      urlQueryParams: false,
      stackFrameVariables: false,
      genAI: { inputs: false, outputs: false },
    },
    beforeSendLog: () => null,
    beforeSendMetric: () => null,
    maxBreadcrumbs: 0,
    profileSessionSampleRate: 0,
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 0,
    beforeSend: sanitizeEvent,
    beforeSendSpan: (span) => ({
      ...span,
      name: String(span.attributes["sentry.op"] ?? "web.operation"),
      attributes: Object.fromEntries(
        Object.entries(span.attributes).filter(([key]) => [
          "sentry.op", "sentry.origin", "sentry.kind", "sentry.sample_rate",
        ].includes(key)),
      ),
      links: undefined,
    }),
  });
}
