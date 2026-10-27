import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  type AgentRunEvent,
  completeWhatsAppMobileRegistration,
  defaultApiBaseUrl,
  loadWhatsAppMobileRegistration,
  loadWhatsAppStatus,
  setApiBaseUrl,
  startWhatsAppMobileRegistration,
  streamRunEvents,
} from "./api";
import { getFirebaseIdToken } from "./firebase";

vi.mock("./firebase", () => ({
  getFirebaseIdToken: vi.fn().mockResolvedValue("test-id-token"),
}));

describe("API errors", () => {
  afterEach(() => {
    setApiBaseUrl(defaultApiBaseUrl);
    vi.unstubAllGlobals();
  });

  it("preserves the response status for JSON requests", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: "WhatsApp integration not found.",
          }),
          {
            headers: {
              "Content-Type": "application/json",
            },
            status: 404,
          },
        ),
      ),
    );

    await expect(loadWhatsAppStatus("missing-integration")).rejects.toEqual(
      expect.objectContaining({
        message: "WhatsApp integration not found.",
        status: 404,
      }),
    );
  });

  it("uses no-store requests for the temporary registration code flow", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            candidates: [],
            capture_expires_at: "2026-07-20T12:10:00Z",
            phone_number: "+447418606704",
            registration_id: "registration-1",
            source_resource_id: "resource-1",
            state: "armed",
          }),
          {
            headers: {
              "Content-Type": "application/json",
            },
            status: 200,
          },
        ),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await startWhatsAppMobileRegistration({
      channel: "sms",
      source_resource_id: "resource-1",
    });
    await loadWhatsAppMobileRegistration("registration-1");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://localhost:7100/api/integrations/whatsapp/mobile-registration",
      expect.objectContaining({
        cache: "no-store",
        method: "POST",
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://localhost:7100/api/integrations/whatsapp/mobile-registration/registration-1",
      expect.objectContaining({
        cache: "no-store",
        method: "GET",
      }),
    );
  });

  it("posts the selected SMS candidate when mobile registration is complete", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          challenge_expires_at: null,
          connected_at: null,
          integration_id: "whatsapp-1",
          phone_number: "+447418606704",
          source_resource_id: "resource-1",
          state: "awaiting_link",
          status_message: null,
          validated_at: null,
        }),
        {
          headers: {
            "Content-Type": "application/json",
          },
          status: 200,
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await completeWhatsAppMobileRegistration("registration-1", {
      candidate_id: "message-1",
      method: "qr",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:7100/api/integrations/whatsapp/mobile-registration/registration-1/complete",
      expect.objectContaining({
        body: JSON.stringify({
          candidate_id: "message-1",
          method: "qr",
        }),
        method: "POST",
      }),
    );
  });

  it("routes requests through the selected server", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          integration_id: null,
          phone_number: null,
          source_resource_id: null,
          state: "not_configured",
          status_message: null,
          connected_at: null,
          validated_at: null,
          challenge_expires_at: null,
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    setApiBaseUrl("https://two.example.com");

    await loadWhatsAppStatus("integration-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "https://two.example.com/api/integrations/whatsapp/integration-1/status",
      expect.any(Object),
    );
  });
});

describe("run event stream recovery", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(getFirebaseIdToken).mockReset().mockResolvedValue("test-id-token");
  });

  afterEach(() => {
    setApiBaseUrl(defaultApiBaseUrl);
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it.each([429, 503])("retries admission status %s after Retry-After and refreshes authentication", async (status) => {
    const controller = new AbortController();
    const event = runEvent("event-1");
    vi.mocked(getFirebaseIdToken)
      .mockResolvedValueOnce("first-id-token")
      .mockResolvedValueOnce("refreshed-id-token");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(admissionResponse(status, "3"))
      .mockResolvedValueOnce(eventResponse(event));
    vi.stubGlobal("fetch", fetchMock);
    const onEvent = vi.fn(() => controller.abort());
    const stream = streamRunEvents({ signal: controller.signal, onEvent });
    const aborted = expect(stream).rejects.toMatchObject({ name: "AbortError" });

    await vi.advanceTimersByTimeAsync(2_999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await aborted;

    expect(onEvent).toHaveBeenCalledWith(event);
    expect(getFirebaseIdToken).toHaveBeenNthCalledWith(1, false);
    expect(getFirebaseIdToken).toHaveBeenNthCalledWith(2, true);
    expect(fetchMock).toHaveBeenNthCalledWith(2, expect.any(String), expect.objectContaining({
      headers: { Authorization: "Bearer refreshed-id-token" },
      signal: controller.signal,
    }));
  });

  it("honors a Retry-After HTTP date", async () => {
    vi.setSystemTime(new Date("2026-10-01T12:00:00Z"));
    const controller = new AbortController();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(admissionResponse(503, "Thu, 01 Oct 2026 12:00:04 GMT"))
      .mockResolvedValueOnce(eventResponse(runEvent("event-1")));
    vi.stubGlobal("fetch", fetchMock);
    const stream = streamRunEvents({ signal: controller.signal, onEvent: () => controller.abort() });
    const aborted = expect(stream).rejects.toMatchObject({ name: "AbortError" });

    await vi.advanceTimersByTimeAsync(3_999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await aborted;
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops a pending retry immediately when its caller unmounts", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(admissionResponse(429, "10"));
    vi.stubGlobal("fetch", fetchMock);
    const stream = streamRunEvents({ signal: controller.signal, onEvent: vi.fn() });
    const aborted = expect(stream).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(0);

    controller.abort();
    await aborted;
    await vi.advanceTimersByTimeAsync(60_000);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getFirebaseIdToken).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not retry a permanent authentication failure", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "Sign in again." }),
      { status: 401, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    await expect(streamRunEvents({ onEvent: vi.fn() })).rejects.toMatchObject({
      message: "Sign in again.",
      status: 401,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("reconnects after EOF using the last delivered event ID", async () => {
    const controller = new AbortController();
    const first = runEvent("event-1");
    const second = runEvent("event-2");
    const third = runEvent("event-3");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(eventResponse(first, second))
      .mockResolvedValueOnce(eventResponse(third));
    vi.stubGlobal("fetch", fetchMock);
    const onEvent = vi.fn((event: AgentRunEvent) => {
      if (event.id === third.id) {
        controller.abort();
      }
    });
    const stream = streamRunEvents({
      runId: "run-1",
      sinceEventId: "event-0",
      signal: controller.signal,
      onEvent,
    });
    const aborted = expect(stream).rejects.toMatchObject({ name: "AbortError" });

    await vi.advanceTimersByTimeAsync(1_000);
    await aborted;

    expect(onEvent.mock.calls.map(([event]) => event.id)).toEqual(["event-1", "event-2", "event-3"]);
    expect(fetchMock).toHaveBeenNthCalledWith(1,
      "http://localhost:7100/api/runs/run-1/events/stream?since_event_id=event-0", expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(2,
      "http://localhost:7100/api/runs/run-1/events/stream?since_event_id=event-2", expect.any(Object));
  });

  it("recovers an interrupted reader without replaying delivered events", async () => {
    const controller = new AbortController();
    const first = runEvent("event-1");
    const second = runEvent("event-2");
    const body = new ReadableStream<Uint8Array>({
      start(streamController) {
        streamController.enqueue(new TextEncoder().encode(sseFrame(first)));
        setTimeout(() => streamController.error(new TypeError("Network connection lost")), 10);
      },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(body))
      .mockResolvedValueOnce(eventResponse(second));
    vi.stubGlobal("fetch", fetchMock);
    const onEvent = vi.fn((event: AgentRunEvent) => {
      if (event.id === second.id) {
        controller.abort();
      }
    });
    const stream = streamRunEvents({ signal: controller.signal, onEvent });
    const aborted = expect(stream).rejects.toMatchObject({ name: "AbortError" });

    await vi.advanceTimersByTimeAsync(1_010);
    await aborted;

    expect(fetchMock).toHaveBeenNthCalledWith(2,
      "http://localhost:7100/api/runs/events/stream?since_event_id=event-1", expect.any(Object));
    expect(onEvent.mock.calls.map(([event]) => event.id)).toEqual(["event-1", "event-2"]);
    expect(body.locked).toBe(false);
  });

  it("does not hide errors from an event handler as a dropped connection", async () => {
    const error = new TypeError("Unable to render event");
    const fetchMock = vi.fn().mockResolvedValue(eventResponse(runEvent("event-1")));
    vi.stubGlobal("fetch", fetchMock);

    await expect(streamRunEvents({ onEvent: () => { throw error; } })).rejects.toBe(error);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("caps retry delays and applies backoff when Retry-After is invalid", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(admissionResponse(429, "not-a-date"))
      .mockResolvedValueOnce(admissionResponse(503, "120"))
      .mockResolvedValueOnce(eventResponse(runEvent("event-1")));
    vi.stubGlobal("fetch", fetchMock);
    const stream = streamRunEvents({ signal: controller.signal, onEvent: () => controller.abort() });
    const aborted = expect(stream).rejects.toMatchObject({ name: "AbortError" });

    await vi.advanceTimersByTimeAsync(999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(29_999);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    await aborted;
  });
});

function runEvent(id: string): AgentRunEvent {
  return {
    id,
    run_id: "run-1",
    user_id: "user-1",
    agent_id: "agent-1",
    kind: "run_started",
    payload_json: {},
    delivery_state: "streamed",
    created_at: "2026-10-01T12:00:00Z",
  };
}

function sseFrame(event: AgentRunEvent): string {
  return `id: ${event.id}\ndata: ${JSON.stringify(event)}\n\n`;
}

function eventResponse(...events: AgentRunEvent[]): Response {
  return new Response(events.map(sseFrame).join(""), {
    headers: { "Content-Type": "text/event-stream" },
  });
}

function admissionResponse(status: number, retryAfter: string): Response {
  return new Response(JSON.stringify({ detail: "Try again shortly." }), {
    status,
    headers: { "Content-Type": "application/json", "Retry-After": retryAfter },
  });
}
