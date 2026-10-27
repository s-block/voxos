import { afterEach, describe, expect, it, vi } from "vitest";
import * as Sentry from "@sentry/react";
import { initializeMonitoring, sanitizeEvent } from "./monitoring";

vi.mock("@sentry/react", async (importOriginal) => ({
  ...await importOriginal<typeof Sentry>(),
  init: vi.fn(),
}));

afterEach(() => vi.unstubAllEnvs());

describe("Sentry monitoring", () => {
  it("does not initialize without a DSN", () => {
    vi.stubEnv("VITE_SENTRY_DSN", "");
    initializeMonitoring();
    expect(Sentry.init).not.toHaveBeenCalled();
  });

  it("prioritizes errors and limits trace collection", () => {
    vi.stubEnv("VITE_SENTRY_DSN", "https://public@example.test/1");
    initializeMonitoring();
    expect(Sentry.init).toHaveBeenCalledWith(expect.objectContaining({
      sampleRate: 1,
      tracesSampleRate: 0.01,
      defaultIntegrations: false,
      profileSessionSampleRate: 0,
      replaysOnErrorSampleRate: 0,
      replaysSessionSampleRate: 0,
      tracePropagationTargets: [],
      dataCollection: expect.objectContaining({ httpBodies: [], userInfo: false }),
    }));
  });

  it("keeps stack locations and removes tenant payloads", () => {
    const privateValue = "private-tenant-content";
    const event: Sentry.Event = {
      user: { email: privateValue },
      request: { url: privateValue, data: privateValue },
      extra: { prompt: privateValue },
      breadcrumbs: [{ message: privateValue }],
      exception: { values: [{ type: "Error", value: privateValue, stacktrace: {
        frames: [{ filename: "app.js", function: "render", lineno: 42, vars: { token: privateValue } }],
      } }] },
      contexts: { conversation: { message: privateValue } },
    };
    const sanitized = sanitizeEvent(event);
    expect(JSON.stringify(sanitized)).not.toContain(privateValue);
    expect(sanitized.exception?.values?.[0].type).toBe("Error");
    expect(sanitized.exception?.values?.[0].stacktrace?.frames?.[0].lineno).toBe(42);
  });

  it("removes URLs and payloads from streamed performance spans", () => {
    vi.stubEnv("VITE_SENTRY_DSN", "https://public@example.test/1");
    initializeMonitoring();
    const options = vi.mocked(Sentry.init).mock.calls.at(-1)?.[0];
    const span = options?.beforeSendSpan?.({
      trace_id: "a".repeat(32),
      span_id: "b".repeat(16),
      name: "https://provider.test/?token=private-tenant-content",
      start_timestamp: 1,
      end_timestamp: 2,
      status: "ok",
      is_segment: false,
      attributes: {
        "sentry.op": "http.client",
        "http.request.body": "private-tenant-content",
        "url.full": "private-tenant-content",
      },
    });
    expect(JSON.stringify(span)).not.toContain("private-tenant-content");
    expect(span?.name).toBe("http.client");
    expect(span?.end_timestamp).toBe(2);
  });
});
