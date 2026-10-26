import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError,
  cancelRun,
  createChat,
  loadChat,
  loadChatDelegatedRuns,
  loadChats,
  loadTextProviderSettings,
  streamChatMessage,
  uploadChatAttachment,
  type AgentRunDetail,
  type ChatDetail,
  type ChatSummary,
  type TextProviderSettings,
} from "../api";
import { ChatPage } from "./ChatPage";
import { BrowserLocationSharing } from "../location";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    cancelRun: vi.fn(),
    createChat: vi.fn(),
    loadChat: vi.fn(),
    loadChatDelegatedRuns: vi.fn(),
    loadChats: vi.fn(),
    loadTextProviderSettings: vi.fn(),
    streamChatMessage: vi.fn(),
    uploadChatAttachment: vi.fn(),
  };
});

describe("ChatPage model defaults", () => {
  it("keeps a failed partial reply visible after reloading durable history", async () => {
    const user = userEvent.setup();
    vi.mocked(loadChat).mockImplementation(async () =>
      vi.mocked(streamChatMessage).mock.calls.length === 0
        ? legacyChatDetail
        : {
            ...legacyChatDetail,
            messages: [{
              id: "partial-reply",
              role: "assistant",
              source: "text_output",
              text: "I found Ben, but the call could not be completed.",
              interrupted: true,
              is_final: false,
              created_at: "2026-10-02T19:27:42Z",
              attachments: [],
              place_cards: [],
            }],
          },
    );
    vi.mocked(streamChatMessage).mockImplementation(async (_chatId, _text, options) => {
      options.onEvent({kind: "message_delta", payload: {delta: "I found Ben, but the call could not be completed."}});
      options.onEvent({kind: "run_failed", payload: {error_message: "Provider failed"}});
    });
    render(<ChatPage addLog={vi.fn()} canEdit />);
    await screen.findByDisplayValue("gpt-4.1-mini");
    await user.type(screen.getByPlaceholderText("Message your agent"), "Call Ben");
    await user.click(screen.getByTitle("Send message"));
    expect(await screen.findByText("Partial reply")).toBeVisible();
    expect(screen.getByText("I found Ben, but the call could not be completed.")).toBeVisible();
  });
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/chat");
    vi.mocked(loadTextProviderSettings).mockResolvedValue(providerSettings);
    vi.mocked(createChat).mockResolvedValue(newChat);
    vi.mocked(loadChats).mockImplementation(async () =>
      vi.mocked(createChat).mock.calls.length === 0
        ? [legacyChat]
        : [newChat, legacyChat],
    );
    vi.mocked(loadChat).mockImplementation(async (chatId) =>
      chatId === newChat.id ? newChatDetail : legacyChatDetail,
    );
    vi.mocked(loadChatDelegatedRuns).mockResolvedValue([]);
    vi.mocked(streamChatMessage).mockResolvedValue();
    vi.mocked(cancelRun).mockResolvedValue(cancelledRun);
    vi.mocked(uploadChatAttachment).mockImplementation(
      async (_chatId, file) => ({
        byte_size: file.size,
        height: 100,
        id: `attachment-${file.name}`,
        mime_type: file.type === "image/png" ? "image/png" : "image/jpeg",
        position: null,
        width: 100,
      }),
    );
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn((file: File) => `blob:${file.name}`),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("uses the active agent model when starting a chat after viewing history", async () => {
    const user = userEvent.setup();

    render(<ChatPage addLog={vi.fn()} canEdit />);

    expect(await screen.findByDisplayValue("gpt-4.1-mini")).toBeInTheDocument();

    await user.click(screen.getByTitle("New chat"));

    expect(screen.getByDisplayValue("gpt-5.4-nano")).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("Message your agent"), "Hello");
    await user.click(screen.getByTitle("Send message"));

    await waitFor(() => {
      expect(createChat).toHaveBeenCalledWith({
        model: "gpt-5.4-nano",
        provider: "openai",
        title: "Hello",
      });
    });
  });

  it("sends messages without a per-message browser account selector", async () => {
    const user = userEvent.setup();

    render(<ChatPage addLog={vi.fn()} canEdit />);

    await screen.findByDisplayValue("gpt-4.1-mini");
    expect(
      screen.queryByLabelText("Signed-in browser (optional)"),
    ).not.toBeInTheDocument();
    await user.type(
      screen.getByPlaceholderText("Message your agent"),
      "Read the latest invoice total",
    );
    await user.click(screen.getByTitle("Send message"));

    await waitFor(() => {
      expect(streamChatMessage).toHaveBeenCalled();
    });
  });

  it("keeps streamed Google Maps cards after durable message reconciliation", async () => {
    const user = userEvent.setup();
    const placeUrl =
      "https://www.google.com/maps/place/?q=place_id:ChIJ123";
    vi.mocked(loadChat).mockImplementation(async () =>
      vi.mocked(streamChatMessage).mock.calls.length === 0
        ? legacyChatDetail
        : {
            ...legacyChatDetail,
            messages: [
              {
                attachments: [],
                created_at: "2026-08-30T09:00:00Z",
                id: "assistant-1",
                place_cards: [],
                role: "assistant",
                source: "text_output",
                text: "Try Konoba Stari Grad.",
              },
              {
                attachments: [],
                created_at: "2026-08-30T09:00:01Z",
                id: "assistant-later",
                place_cards: [],
                role: "assistant",
                source: "text_output",
                text: "A later assistant update.",
              },
            ],
          },
    );
    vi.mocked(streamChatMessage).mockImplementation(
      async (_chatId, _text, options) => {
        options.onEvent({
          kind: "message_completed",
          payload: {
            message_id: "assistant-1",
            place_cards: [
              {
                attribution_url: placeUrl,
                directions_url: null,
                latitude: null,
                longitude: null,
                place_id: "ChIJ123",
                place_url: placeUrl,
                position: 0,
                provider: "google_maps",
                title: "Konoba Stari Grad",
              },
            ],
            text: "Try Konoba Stari Grad.",
          },
        });
      },
    );

    render(<ChatPage addLog={vi.fn()} canEdit />);

    await screen.findByDisplayValue("gpt-4.1-mini");
    await user.type(
      screen.getByPlaceholderText("Message your agent"),
      "Restaurants near me",
    );
    await user.click(screen.getByTitle("Send message"));

    const link = await screen.findByRole("link", {
      name: "Open Konoba Stari Grad in Google Maps (opens in a new tab)",
    });
    expect(link).toHaveAttribute("href", placeUrl);
    expect(
      within(screen.getByText("Try Konoba Stari Grad.").closest("article")!).getByRole(
        "link",
        { name: "Open Konoba Stari Grad in Google Maps (opens in a new tab)" },
      ),
    ).toBe(link);
    expect(
      within(screen.getByText("A later assistant update.").closest("article")!).queryByRole(
        "link",
      ),
    ).not.toBeInTheDocument();
  });

  it("adds an enabled foreground location to the message request", async () => {
    const user = userEvent.setup();
    const locationSharing = new BrowserLocationSharing("test-location", {
      geolocation: {
        getCurrentPosition: (success) =>
          success({
            coords: {
              accuracy: 25,
              altitude: null,
              altitudeAccuracy: null,
              heading: null,
              latitude: 42.4304,
              longitude: 19.2594,
              speed: null,
              toJSON: () => ({}),
            },
            timestamp: Date.now(),
            toJSON: () => ({}),
          }),
      },
      storage: null,
    });
    await locationSharing.enable();

    render(
      <ChatPage
        addLog={vi.fn()}
        canEdit
        locationSharing={locationSharing}
      />,
    );

    await screen.findByDisplayValue("gpt-4.1-mini");
    await user.type(
      screen.getByPlaceholderText("Message your agent"),
      "Restaurants near me",
    );
    await user.click(screen.getByTitle("Send message"));

    await waitFor(() => expect(streamChatMessage).toHaveBeenCalledTimes(1));
    expect(vi.mocked(streamChatMessage).mock.calls[0]?.[2].location).toMatchObject({
      latitude: 42.4304,
      longitude: 19.2594,
      horizontal_accuracy_m: 25,
    });
  });

  it("uses a fresh turn identifier after a failed send", async () => {
    const user = userEvent.setup();
    vi.mocked(streamChatMessage)
      .mockRejectedValueOnce(new ApiRequestError(409, "Idempotency key conflict"))
      .mockResolvedValueOnce();

    render(<ChatPage addLog={vi.fn()} canEdit />);

    await screen.findByDisplayValue("gpt-4.1-mini");
    await user.type(screen.getByPlaceholderText("Message your agent"), "Retry me");
    await user.click(screen.getByTitle("Send message"));
    await waitFor(() => expect(streamChatMessage).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTitle("Send message")).toBeEnabled());

    await user.click(screen.getByTitle("Send message"));
    await waitFor(() => expect(streamChatMessage).toHaveBeenCalledTimes(2));

    const firstTurnId = vi.mocked(streamChatMessage).mock.calls[0]?.[2].clientTurnId;
    const secondTurnId = vi.mocked(streamChatMessage).mock.calls[1]?.[2].clientTurnId;
    expect(firstTurnId).toEqual(expect.any(String));
    expect(secondTurnId).toEqual(expect.any(String));
    expect(secondTurnId).not.toBe(firstTurnId);
  });

  it("batches rapid message deltas into one animation-frame render", async () => {
    const user = userEvent.setup();
    let scheduledFrame: FrameRequestCallback | undefined;
    let finishStream: (() => void) | undefined;
    const requestFrame = vi
      .spyOn(window, "requestAnimationFrame")
      .mockImplementation((callback) => {
        scheduledFrame = callback;
        return 1;
      });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => undefined);
    vi.mocked(streamChatMessage).mockImplementation(
      async (_chatId, _text, options) => {
        options.onEvent({ kind: "message_delta", payload: { delta: "Rapid " } });
        options.onEvent({ kind: "message_delta", payload: { delta: "response" } });
        await new Promise<void>((resolve) => {
          finishStream = resolve;
        });
      },
    );

    render(<ChatPage addLog={vi.fn()} canEdit />);

    await screen.findByDisplayValue("gpt-4.1-mini");
    await user.type(screen.getByPlaceholderText("Message your agent"), "Start");
    await user.click(screen.getByTitle("Send message"));
    await waitFor(() => expect(streamChatMessage).toHaveBeenCalledTimes(1));

    expect(requestFrame).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Rapid response")).not.toBeInTheDocument();
    act(() => scheduledFrame?.(0));
    expect(await screen.findByText("Rapid response")).toBeInTheDocument();

    act(() => finishStream?.());
    await waitFor(() =>
      expect(screen.queryByTitle("Stop run")).not.toBeInTheDocument(),
    );
  });

  it("shows configured tool activity in the waiting response", async () => {
    const user = userEvent.setup();
    let finishStream: (() => void) | undefined;
    vi.mocked(streamChatMessage).mockImplementation(
      async (_chatId, _text, options) => {
        options.onEvent({
          kind: "message_delta",
          payload: { delta: "I’ll check that now." },
        });
        options.onEvent({
          kind: "tool_call_started",
          payload: {
            activity: {
              detail: "Exa",
              icon: "web",
              label: "Searching the web",
              logo: {
                domain: "exa.ai",
                label: "Exa",
                source: "domain",
              },
            },
            call_id: "call-search",
            name: "mcp_1234567890_web_search_exa",
          },
        });
        await new Promise<void>((resolve) => {
          finishStream = resolve;
        });
      },
    );

    const { container } = render(<ChatPage addLog={vi.fn()} canEdit />);

    await screen.findByDisplayValue("gpt-4.1-mini");
    await user.type(
      screen.getByPlaceholderText("Message your agent"),
      "What changed today?",
    );
    await user.click(screen.getByTitle("Send message"));

    expect(
      await screen.findByRole("status", { name: "Searching the web" }),
    ).toBeInTheDocument();
    expect(screen.getByText("I’ll check that now.")).toBeInTheDocument();
    expect(screen.getByText("Exa")).toBeInTheDocument();
    expect(container.querySelector(".chat-message-activity-icon")).not.toBeNull();
    expect(container.querySelector(".chat-message-activity-logo")).toBeNull();

    act(() => finishStream?.());
    await waitFor(() =>
      expect(screen.queryByTitle("Stop run")).not.toBeInTheDocument(),
    );
  });

  it("generates an image through normal chat and renders the tool result", async () => {
    const user = userEvent.setup();
    let finishStream: (() => void) | undefined;
    vi.mocked(streamChatMessage).mockImplementation(
      async (_chatId, _text, options) => {
        options.onEvent({
          kind: "tool_call_completed",
          payload: {
            attachments: [
              {
                byte_size: 2048,
                height: 1024,
                id: "generated-image-1",
                mime_type: "image/jpeg",
                position: 0,
                width: 1024,
              },
            ],
            call_id: "image-call-1",
            is_error: false,
            name: "generate_image",
          },
        });
        await new Promise<void>((resolve) => {
          finishStream = resolve;
        });
      },
    );

    render(<ChatPage addLog={vi.fn()} canEdit />);

    await screen.findByDisplayValue("gpt-4.1-mini");
    expect(
      screen.queryByRole("button", { name: "Image" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Chat" }),
    ).not.toBeInTheDocument();
    await user.type(
      screen.getByPlaceholderText("Message your agent"),
      "A tiny library on the moon",
    );
    await user.click(screen.getByTitle("Send message"));

    await waitFor(() => {
      expect(streamChatMessage).toHaveBeenCalledWith(
        legacyChat.id,
        "A tiny library on the moon",
        expect.objectContaining({
          attachmentIds: [],
          clientTurnId: expect.any(String),
        }),
      );
    });
    expect(
      await screen.findByRole("button", { name: "Open attached image 1" }),
    ).toBeInTheDocument();
    finishStream?.();
    await waitFor(() =>
      expect(screen.queryByTitle("Stop run")).not.toBeInTheDocument(),
    );
  });

  it("selects NVIDIA's fast default and exposes its multimodal image route", async () => {
    const user = userEvent.setup();
    vi.mocked(loadTextProviderSettings).mockResolvedValue({
      ...providerSettings,
      providers: [...providerSettings.providers, nvidiaProvider],
    });

    render(<ChatPage addLog={vi.fn()} canEdit />);

    await screen.findByDisplayValue("gpt-4.1-mini");
    await user.selectOptions(screen.getByDisplayValue("OpenAI"), "nvidia");

    expect(
      screen.getByDisplayValue("openai/gpt-oss-20b"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "openai/gpt-oss-120b" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "openai/gpt-oss-20b" }),
    ).toBeInTheDocument();
    expect(screen.getByTitle("Add images · Uses Step 3.7 Flash")).toBeEnabled();
    expect(screen.queryByPlaceholderText("NVIDIA API key")).not.toBeInTheDocument();
  });

  it("cancels the durable run and aborts the active stream", async () => {
    const user = userEvent.setup();
    vi.mocked(streamChatMessage).mockImplementation(
      async (_chatId, _text, options) => {
        options.onEvent({
          kind: "run_started",
          payload: { model: "gpt-5.4-nano", provider: "openai" },
          run_id: "run-1",
        });
        await new Promise<void>((_resolve, reject) => {
          options.signal?.addEventListener("abort", () => {
            reject(new DOMException("Stopped", "AbortError"));
          });
        });
      },
    );

    render(<ChatPage addLog={vi.fn()} canEdit />);

    await screen.findByDisplayValue("gpt-4.1-mini");
    await user.click(screen.getByTitle("New chat"));
    await user.type(screen.getByPlaceholderText("Message your agent"), "Keep working");
    await user.click(screen.getByTitle("Send message"));
    await user.click(await screen.findByTitle("Stop run"));

    await waitFor(() => {
      expect(cancelRun).toHaveBeenCalledWith("run-1", "Stopped from Chat");
    });
    expect(await screen.findByText("Run cancelled")).toBeInTheDocument();
    const streamOptions = vi.mocked(streamChatMessage).mock.calls[0]?.[2];
    expect(streamOptions?.signal?.aborted).toBe(true);
  });

  it("keeps only the latest token usage snapshot in live activity", async () => {
    const user = userEvent.setup();
    let finishStream: (() => void) | undefined;
    vi.mocked(streamChatMessage).mockImplementation(
      async (_chatId, _text, options) => {
        options.onEvent({
          kind: "usage",
          payload: { input_tokens: 80, output_tokens: 20, total_tokens: 100 },
          run_id: "run-usage",
        });
        options.onEvent({
          kind: "usage",
          payload: { input_tokens: 90, output_tokens: 30, total_tokens: 120 },
          run_id: "run-usage",
        });
        await new Promise<void>((resolve) => {
          finishStream = resolve;
        });
      },
    );

    render(<ChatPage addLog={vi.fn()} canEdit />);

    await screen.findByDisplayValue("gpt-4.1-mini");
    await user.type(screen.getByPlaceholderText("Message your agent"), "Hello");
    await user.click(screen.getByTitle("Send message"));

    expect(await screen.findByText("120 tokens")).toBeInTheDocument();
    expect(screen.getAllByText("Token usage")).toHaveLength(1);
    expect(screen.queryByText("100 tokens")).not.toBeInTheDocument();
    finishStream?.();
    await waitFor(() =>
      expect(screen.queryByTitle("Stop run")).not.toBeInTheDocument(),
    );
  });

  it("labels delegated transcript entries without attributing them to the user", async () => {
    vi.mocked(loadChat).mockResolvedValue({
      ...legacyChatDetail,
      messages: [
        {
          attachments: [],
          created_at: "2026-07-29T12:00:00Z",
          id: "delegated-task",
          place_cards: [],
          role: "user",
          source: "subagent_task",
          text: "Inspect the inbox.",
        },
        {
          attachments: [],
          created_at: "2026-07-29T12:00:01Z",
          id: "delegated-output",
          place_cards: [],
          role: "assistant",
          source: "subagent_output",
          text: "Inspection complete.",
        },
      ],
    });

    render(<ChatPage addLog={vi.fn()} canEdit />);

    expect(await screen.findByText("Delegated task")).toBeInTheDocument();
    expect(screen.getByText("Subagent")).toBeInTheDocument();
    expect(screen.queryByText("user")).not.toBeInTheDocument();
  });

  it("projects delegated output and tool activity into the parent chat", async () => {
    vi.mocked(loadChat).mockResolvedValue({
      ...legacyChatDetail,
      delegated_runs: [
        {
          chat_id: "child-chat",
          completed_at: "2026-08-02T19:48:23Z",
          created_at: "2026-08-02T19:48:09Z",
          id: "child-run",
          model: "gpt-5.4-nano",
          output_created_at: "2026-08-02T19:48:23Z",
          output_text: "The current screenings are listed here.",
          provider: "openai",
          runtime_events: [
            {
              created_at: "2026-08-02T19:48:20Z",
              id: "child-tool-complete",
              kind: "tool_call_completed",
              payload_json: {
                call_id: "call-exa",
                is_error: false,
                name: "mcp_1234567890_web_search_exa",
              },
            },
          ],
          status: "succeeded",
          task_prompt: "Research current BFI IMAX screenings.",
          tool_events: [
            {
              arguments_json: { query: "BFI IMAX screenings" },
              call_id: "call-exa",
              created_at: "2026-08-02T19:48:20Z",
              id: "child-tool",
              is_error: false,
              name: "mcp_1234567890_web_search_exa",
              output_text: "results",
            },
          ],
        },
      ],
    });

    render(<ChatPage addLog={vi.fn()} canEdit />);

    expect(
      await screen.findByText("The current screenings are listed here."),
    ).toBeInTheDocument();
    expect(screen.getByText("Subagent")).toBeInTheDocument();
    expect(screen.getByText("Delegated task queued")).toBeInTheDocument();
    expect(screen.getByText("Tool completed")).toBeInTheDocument();
    expect(screen.getAllByText("web_search_exa").length).toBeGreaterThan(0);
  });

  it("does not duplicate a delegated output already delivered to the parent transcript", async () => {
    vi.mocked(loadChat).mockResolvedValue({
      ...legacyChatDetail,
      messages: [
        {
          attachments: [],
          client_turn_id: "run:child-run:complete",
          created_at: "2026-08-02T19:48:23Z",
          id: "delivered-child-output",
          place_cards: [],
          role: "assistant",
          source: "subagent_output",
          text: "The current screenings are listed here.",
        },
      ],
      delegated_runs: [
        {
          chat_id: "child-chat",
          completed_at: "2026-08-02T19:48:23Z",
          created_at: "2026-08-02T19:48:09Z",
          id: "child-run",
          model: "gpt-5.4-nano",
          output_created_at: "2026-08-02T19:48:23Z",
          output_text: "The current screenings are listed here.",
          provider: "openai",
          runtime_events: [],
          status: "succeeded",
          task_prompt: "Research current BFI IMAX screenings.",
          tool_events: [],
        },
      ],
    });

    render(<ChatPage addLog={vi.fn()} canEdit />);

    expect(
      await screen.findAllByText("The current screenings are listed here."),
    ).toHaveLength(1);
  });

  it("opens the chat selected in the query string", async () => {
    window.history.replaceState({}, "", `/chat?chat=${newChat.id}`);
    vi.mocked(loadChats).mockResolvedValue([legacyChat]);

    render(<ChatPage addLog={vi.fn()} canEdit />);

    await waitFor(() => expect(loadChat).toHaveBeenCalledWith(newChat.id));
  });

  it("falls back only when a linked chat no longer exists", async () => {
    window.history.replaceState({}, "", "/chat?chat=missing-chat");
    vi.mocked(loadChat).mockImplementation(async (chatId) => {
      if (chatId === "missing-chat") {
        throw new ApiRequestError(404, "Chat was not found");
      }
      return legacyChatDetail;
    });

    render(<ChatPage addLog={vi.fn()} canEdit />);

    expect(
      await screen.findByText(
        "The linked chat is unavailable. Showing your most recent chat instead.",
      ),
    ).toBeInTheDocument();
    expect(loadChat).toHaveBeenNthCalledWith(1, "missing-chat");
    expect(loadChat).toHaveBeenNthCalledWith(2, legacyChat.id);
  });

  it("sends repeated multi-selection with text in one ordered stream request", async () => {
    const user = userEvent.setup();
    render(<ChatPage addLog={vi.fn()} canEdit />);
    await screen.findByDisplayValue("gpt-4.1-mini");
    await user.click(screen.getByTitle("New chat"));
    const input = screen.getByLabelText("Choose images");
    const one = new File(["one"], "one.jpg", { type: "image/jpeg" });
    const two = new File(["two"], "two.png", { type: "image/png" });
    const three = new File(["three"], "three.jpg", { type: "image/jpeg" });

    await user.upload(input, [one, two]);
    await user.upload(input, three);
    await user.click(screen.getByLabelText("Move three.jpg left"));
    await user.type(
      screen.getByPlaceholderText("Message your agent"),
      "Compare these",
    );
    await user.click(screen.getByTitle("Send message"));

    await waitFor(() => expect(streamChatMessage).toHaveBeenCalledTimes(1));
    expect(vi.mocked(uploadChatAttachment).mock.calls.map((call) => call[1].name)).toEqual([
      "one.jpg",
      "three.jpg",
      "two.png",
    ]);
    expect(vi.mocked(streamChatMessage).mock.calls[0]?.[1]).toBe(
      "Compare these",
    );
    expect(
      vi.mocked(streamChatMessage).mock.calls[0]?.[2].attachmentIds,
    ).toEqual([
      "attachment-one.jpg",
      "attachment-three.jpg",
      "attachment-two.png",
    ]);
    await waitFor(() => expect(screen.getByTitle("Add images")).toBeEnabled());
  });

  it("adds dropped images through the same chat draft and upload path", async () => {
    const user = userEvent.setup();
    render(<ChatPage addLog={vi.fn()} canEdit />);
    await screen.findByDisplayValue("gpt-4.1-mini");
    await user.click(screen.getByTitle("New chat"));
    const chat = screen.getByLabelText("Chat conversation");
    const image = new File(["dropped"], "dropped.png", {
      type: "image/png",
    });
    const dataTransfer = { files: [image], types: ["Files"] };

    fireEvent.dragEnter(chat, { dataTransfer });
    expect(screen.getByText("Drop images to attach")).toBeInTheDocument();
    fireEvent.drop(chat, { dataTransfer });

    expect(await screen.findByAltText("Selected image 1")).toBeInTheDocument();
    expect(screen.queryByText("Drop images to attach")).not.toBeInTheDocument();

    await user.click(screen.getByTitle("Send message"));
    await waitFor(() => expect(streamChatMessage).toHaveBeenCalledTimes(1));
    expect(uploadChatAttachment).toHaveBeenCalledWith(
      newChat.id,
      image,
      expect.any(String),
    );
    expect(vi.mocked(streamChatMessage).mock.calls[0]?.[2].attachmentIds).toEqual([
      "attachment-dropped.png",
    ]);
  });

  it("adds pasted images through the same chat draft and upload path", async () => {
    const user = userEvent.setup();
    render(<ChatPage addLog={vi.fn()} canEdit />);
    await screen.findByDisplayValue("gpt-4.1-mini");
    await user.click(screen.getByTitle("New chat"));
    const composer = screen.getByPlaceholderText("Message your agent");
    const image = new File(["pasted"], "pasted.png", {
      type: "image/png",
    });

    fireEvent.paste(composer, {
      clipboardData: {
        items: [
          {
            getAsFile: () => image,
            kind: "file",
            type: "image/png",
          },
        ],
      },
    });

    expect(await screen.findByAltText("Selected image 1")).toBeInTheDocument();
    await user.click(screen.getByTitle("Send message"));
    await waitFor(() => expect(streamChatMessage).toHaveBeenCalledTimes(1));
    expect(uploadChatAttachment).toHaveBeenCalledWith(
      newChat.id,
      image,
      expect.any(String),
    );
    expect(vi.mocked(streamChatMessage).mock.calls[0]?.[2].attachmentIds).toEqual([
      "attachment-pasted.png",
    ]);
  });

  it("falls back to clipboard files when pasted items are unavailable", async () => {
    render(<ChatPage addLog={vi.fn()} canEdit />);
    await screen.findByDisplayValue("gpt-4.1-mini");
    const composer = screen.getByPlaceholderText("Message your agent");
    const image = new File(["pasted"], "clipboard-file.png", {
      type: "image/png",
    });

    fireEvent.paste(composer, {
      clipboardData: {
        files: [image],
        items: [],
      },
    });

    expect(await screen.findByAltText("Selected image 1")).toBeInTheDocument();
  });

  it("allows image editing attachments for a text-only chat model", async () => {
    render(<ChatPage addLog={vi.fn()} canEdit />);

    await screen.findByDisplayValue("gpt-4.1-mini");

    expect(screen.getByTitle("Add images")).toBeEnabled();
  });

  it("hides image selection when the rollout flag is disabled", async () => {
    const user = userEvent.setup();
    vi.mocked(loadTextProviderSettings).mockResolvedValue({
      ...providerSettings,
      image_input_enabled: false,
    });

    render(<ChatPage addLog={vi.fn()} canEdit />);
    await screen.findByDisplayValue("gpt-4.1-mini");
    await user.click(screen.getByTitle("New chat"));

    expect(screen.queryByTitle("Add images")).not.toBeInTheDocument();
  });

  it("keeps the full image-only draft and does not stream after an upload failure", async () => {
    const user = userEvent.setup();
    vi.mocked(uploadChatAttachment).mockImplementation(
      async (_chatId, file) => {
        if (file.name === "bad.png") {
          throw new Error("Upload failed");
        }
        return {
          byte_size: file.size,
          height: 100,
          id: `attachment-${file.name}`,
          mime_type: "image/jpeg",
          position: null,
          width: 100,
        };
      },
    );
    render(<ChatPage addLog={vi.fn()} canEdit />);
    await screen.findByDisplayValue("gpt-4.1-mini");
    await user.click(screen.getByTitle("New chat"));
    const input = screen.getByLabelText("Choose images");
    await user.upload(input, [
      new File(["good"], "good.jpg", { type: "image/jpeg" }),
      new File(["bad"], "bad.png", { type: "image/png" }),
    ]);
    await user.click(screen.getByTitle("Send message"));

    expect((await screen.findAllByText("Upload failed")).length).toBeGreaterThan(0);
    expect(streamChatMessage).not.toHaveBeenCalled();
    expect(screen.getByAltText("Selected image 1")).toBeInTheDocument();
    expect(screen.getByAltText("Selected image 2")).toBeInTheDocument();
    expect(screen.getByTitle("Send message")).toBeEnabled();
  });

  it("shows errored tool results with click-to-toggle diagnostics", async () => {
    const user = userEvent.setup();
    vi.mocked(loadChat).mockResolvedValue({
      chat: legacyChat,
      delegated_runs: [],
      messages: [],
      runtime_events: [
        {
          created_at: "2026-07-29T11:01:47Z",
          id: "runtime-usage-latest",
          kind: "usage",
          payload_json: {
            input_tokens: 24800,
            input_tokens_details: { cached_tokens: 22016 },
            output_tokens: 300,
            output_tokens_details: { reasoning_tokens: 41 },
            total_tokens: 25100,
          },
        },
        {
          created_at: "2026-07-29T11:01:45Z",
          id: "runtime-tool-error",
          kind: "tool_call_completed",
          payload_json: {
            call_id: "call-airbnb",
            is_error: true,
            name: "mcp_799e9a43ae_airbnb_search",
            output:
              '{"error":"checkin/checkout cannot be combined with flexible or monthly date filters."}',
          },
        },
        {
          created_at: "2026-07-29T11:01:46Z",
          id: "runtime-usage",
          kind: "usage",
          payload_json: {
            input_tokens: 24769,
            input_tokens_details: { cached_tokens: 22016 },
            output_tokens: 268,
            output_tokens_details: { reasoning_tokens: 41 },
            total_tokens: 25037,
          },
        },
      ],
      tool_events: [
        {
          arguments_json: {
            checkin: "2026-08-07",
            checkout: "2026-08-09",
            location: "Greater London",
          },
          call_id: "call-airbnb",
          created_at: "2026-07-29T11:01:45Z",
          id: "tool-airbnb",
          is_error: true,
          name: "mcp_799e9a43ae_airbnb_search",
          output_text:
            '{"error":"checkin/checkout cannot be combined with flexible or monthly date filters."}',
        },
      ],
      usage_events: [],
    });

    render(<ChatPage addLog={vi.fn()} canEdit />);

    const failureLabel = await screen.findByText("Tool failed");
    const failureSummary = failureLabel.closest("summary");
    const failureCard = failureLabel.closest("details");
    expect(failureSummary).not.toBeNull();
    expect(failureCard).not.toBeNull();
    expect(failureCard).toHaveClass("error", "has-details");
    expect(failureCard).not.toHaveAttribute("open");
    expect(failureSummary).toHaveAttribute(
      "aria-label",
      "Tool failed. Click to expand or collapse details.",
    );
    expect(failureCard).toHaveTextContent(
      "airbnb_search: checkin/checkout cannot be combined with flexible or monthly date filters.",
    );
    expect(failureCard).toHaveTextContent("Arguments");
    expect(failureCard).toHaveTextContent('"checkin": "2026-08-07"');
    expect(failureCard).toHaveTextContent("Runtime name");
    expect(failureCard).toHaveTextContent(
      "mcp_799e9a43ae_airbnb_search",
    );
    expect(await screen.findByText("25,100 tokens")).toBeInTheDocument();
    expect(screen.getAllByText("Token usage")).toHaveLength(1);
    expect(screen.queryByText("25,037 tokens")).not.toBeInTheDocument();
    expect(screen.getByText("Cached tokens")).toBeInTheDocument();
    expect(screen.getByText("Reasoning tokens")).toBeInTheDocument();

    await user.click(failureSummary!);
    expect(failureCard).toHaveAttribute("open");

    await user.click(failureSummary!);
    expect(failureCard).not.toHaveAttribute("open");
  });
});

const legacyChat: ChatSummary = {
  agent_id: "agent-1",
  id: "chat-legacy",
  model: "gpt-4.1-mini",
  provider: "openai",
  started_at: "2026-07-20T10:00:00Z",
  status: "active",
  supports_image_input: false,
  title: "Older chat",
  updated_at: "2026-07-20T10:00:00Z",
};

const newChat: ChatSummary = {
  ...legacyChat,
  id: "chat-new",
  model: "gpt-5.4-nano",
  title: "Hello",
  updated_at: "2026-07-22T15:00:00Z",
};

const legacyChatDetail: ChatDetail = {
  chat: legacyChat,
  delegated_runs: [],
  messages: [],
  runtime_events: [],
  tool_events: [],
  usage_events: [],
};

const newChatDetail: ChatDetail = {
  ...legacyChatDetail,
  chat: newChat,
};

const providerSettings: TextProviderSettings = {
  active_provider: "openai",
  image_input_enabled: true,
  active_settings: {
    agent_instructions: "You are a helpful assistant.",
    allowed_subagent_models: ["gpt-5.4-nano"],
    configured: true,
    image_generation_model: "gpt-image-2",
    image_generation_provider: "openai",
    model: "gpt-5.4-nano",
    preferred_language: "English",
    provider: "openai",
    subagent_model: "gpt-5.4-nano",
    temperature: null,
  },
  providers: [
    {
      active: true,
      allowed_subagent_models: ["gpt-5.4-nano"],
      api_key_label: "OpenAI API key", // pragma: allowlist secret
      configured: true,
      credential_last_four: "test",
      default_model: "gpt-5.6-sol",
      default_multimodal_model: "gpt-5.6-sol",
      default_browser_model: "gpt-5.6-sol",
      default_image_generation_model: "gpt-image-2",
      default_subagent_model: "gpt-5.6-terra",
      docs_url: "https://developers.openai.com/api/docs/models",
      label: "OpenAI",
      logo: { domain: "openai.com", label: "OpenAI", source: "domain" },
      image_generation_model_options: [
        {
          description: "OpenAI image generation and editing model.",
          docs_url: "https://developers.openai.com/api/docs/models/gpt-image-2",
          hosted_api_supports_image_editing: true,
          label: "GPT Image 2",
          model: "gpt-image-2",
          pricing_summary: null,
          recommended: true,
          supports_image_editing: true,
        },
      ],
      model_options: [
        {
          description: "Image-capable model.",
          label: "GPT-5.4 nano",
          model: "gpt-5.4-nano",
          pricing_summary: null,
          recommended: true,
          supports_image_input: true,
          supports_tool_calling: true,
          supports_browser_use: true,
        },
      ],
      provider: "openai",
      runtime_supported: true,
      suggested_models: ["gpt-5.6-sol", "gpt-5.4-nano"],
    },
  ],
};

const nvidiaProvider: TextProviderSettings["providers"][number] = {
  active: false,
  allowed_subagent_models: ["openai/gpt-oss-20b"],
  api_key_label: "NVIDIA API key", // pragma: allowlist secret
  configured: false,
  credential_last_four: null,
  default_model: "openai/gpt-oss-20b",
  default_multimodal_model: "stepfun-ai/step-3.7-flash",
  default_browser_model: "stepfun-ai/step-3.7-flash",
  default_image_generation_model: "black-forest-labs/flux.2-klein-4b",
  default_subagent_model: "openai/gpt-oss-20b",
  docs_url: "https://docs.api.nvidia.com/nim/re/reference/llm-apis",
  label: "NVIDIA API Catalog",
  logo: { domain: "nvidia.com", label: "NVIDIA", source: "domain" },
  image_generation_model_options: [],
  model_options: [
    {
      description: "Text-only reasoning model.",
      label: "Nemotron 3 Nano 30B A3B",
      model: "nvidia/nemotron-3-nano-30b-a3b",
      pricing_summary: null,
      recommended: false,
      supports_image_input: false,
      supports_tool_calling: true,
      supports_browser_use: false,
    },
    {
      description: "General-purpose reasoning model.",
      label: "GPT-OSS 120B",
      model: "openai/gpt-oss-120b",
      pricing_summary: null,
      recommended: false,
      supports_image_input: false,
      supports_tool_calling: true,
      supports_browser_use: false,
    },
    {
      description: "Lower-latency reasoning model.",
      label: "GPT-OSS 20B",
      model: "openai/gpt-oss-20b",
      pricing_summary: null,
      recommended: true,
      supports_image_input: false,
      supports_tool_calling: true,
      supports_browser_use: false,
    },
    {
      description: "Multimodal browser and image model.",
      label: "Step 3.7 Flash",
      model: "stepfun-ai/step-3.7-flash",
      pricing_summary: null,
      recommended: false,
      supports_image_input: true,
      supports_tool_calling: true,
      supports_browser_use: true,
    },
  ],
  provider: "nvidia",
  runtime_supported: true,
  suggested_models: [
    "nvidia/nemotron-3-nano-30b-a3b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "stepfun-ai/step-3.7-flash",
  ],
};

const cancelledRun: AgentRunDetail = {
  agent_id: "agent-1",
  attempt: 1,
  cancel_reason: "Stopped from Chat",
  cancel_requested_at: "2026-07-27T13:00:00Z",
  chat_id: "chat-new",
  child_run_count: 0,
  completed_at: "2026-07-27T13:00:00Z",
  created_at: "2026-07-27T12:59:00Z",
  error_message: null,
  error_type: null,
  heartbeat_at: "2026-07-27T12:59:30Z",
  id: "run-1",
  kind: "text_chat_turn",
  lease_expires_at: null,
  lease_owner: null,
  max_attempts: 1,
  next_attempt_at: null,
  parent_run_id: null,
  payload_json: {},
  priority: 0,
  queue: "text-inline",
  result_json: {},
  root_run_id: "run-1",
  schedule_id: null,
  webhook_id: null,
  session_id: "chat-new",
  started_at: "2026-07-27T12:59:00Z",
  status: "cancelled",
  task_plan_id: null,
  updated_at: "2026-07-27T13:00:00Z",
  user_id: "local-user-123",
};
