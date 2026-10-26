import {
  type ClipboardEvent as ReactClipboardEvent,
  type DragEvent as ReactDragEvent,
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Globe2,
  Loader2,
  MessageSquarePlus,
  Paperclip,
  PlugZap,
  Send,
  Square,
  Trash2,
  X,
  Wrench,
} from "lucide-react";
import {
  ApiRequestError,
  cancelRun,
  createChat,
  deleteChatAttachment,
  deleteChat,
  loadChatAttachment,
  loadChat,
  loadChatDelegatedRuns,
  loadChats,
  loadTextProviderSettings,
  streamChatMessage,
  uploadChatAttachment,
  type BrandLogo,
  type ChatAttachmentSummary,
  updateChat,
  type ChatDetail,
  type ChatDelegatedRun,
  type ChatMessage,
  type ChatPlaceCard,
  type ChatStreamEvent,
  type ChatSummary,
  type ChatToolEvent,
  type TextProvider,
  type TextProviderSettings,
} from "../api";
import { DEFAULT_TEXT_MODEL } from "../appConstants";
import type { LogEntry } from "../appTypes";
import { authErrorMessage } from "../appUtils";
import type { BrowserLocationSharing } from "../location";
import { MarkdownMessage } from "../components/MarkdownMessage";
import { ServiceLogo } from "../components/ServiceLogo";
import { selectableModelOptions } from "../modelOptions";

type ChatPageProps = {
  addLog: (message: string, level?: LogEntry["level"]) => void;
  canEdit: boolean;
  locationSharing?: BrowserLocationSharing;
};

type UiMessage = {
  id: string;
  role: ChatMessage["role"];
  source: ChatMessage["source"];
  clientTurnId?: string | null;
  text: string;
  attachments: UiAttachment[];
  placeCards: ChatPlaceCard[];
  createdAt?: string;
  isStreaming?: boolean;
  interrupted?: boolean;
  activity?: ChatActivityPresentation;
};

type ChatActivityPresentation = {
  label: string;
  detail: string | null;
  icon: "mcp" | "web" | null;
  logo: BrandLogo | null;
};

type UiAttachment = {
  id: string;
  localUrl?: string;
  summary?: ChatAttachmentSummary;
};

type PendingImage = {
  id: string;
  file: File;
  previewUrl: string;
  status: "selected" | "uploading" | "uploaded";
  error?: string;
  uploaded?: ChatAttachmentSummary;
};

type PendingMessageDelta = {
  assistantMessageId: string;
  text: string;
};

type StreamedPlaceCardBatch = {
  messageId: string;
  placeCards: ChatPlaceCard[];
};

type ActivityEntry = {
  id: string;
  kind: ChatStreamEvent["kind"];
  label: string;
  detail: string | null;
  level: "info" | "error" | "success";
  facts: ActivityFact[];
};

type ActivityFact = {
  label: string;
  value: string;
  code?: boolean;
};

const STREAMING_ACTIVITY_ID = "active-message-stream";
const USAGE_ACTIVITY_ID = "latest-token-usage";
const MAX_DRAFT_IMAGES = 4;
const MAX_SOURCE_IMAGE_BYTES = 25 * 1024 * 1024;
const ACCEPTED_IMAGE_TYPES = new Set([
  "image/jpeg",
  "image/png",
  "image/heic",
  "image/heif",
]);

export function ChatPage({
  addLog,
  canEdit,
  locationSharing,
}: ChatPageProps) {
  const [providerSettings, setProviderSettings] =
    useState<TextProviderSettings | null>(null);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [selectedChat, setSelectedChat] = useState<ChatSummary | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [activities, setActivities] = useState<ActivityEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [draftId, setDraftId] = useState(() => crypto.randomUUID());
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<TextProvider>("openai");
  const [selectedModel, setSelectedModel] = useState(DEFAULT_TEXT_MODEL);
  const [titleDraft, setTitleDraft] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isDraggingImages, setIsDraggingImages] = useState(false);
  const [hasActiveDelegatedRuns, setHasActiveDelegatedRuns] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const activeRunIdRef = useRef<string | null>(null);
  const composerTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const imageDragDepthRef = useRef(0);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const pendingImagesRef = useRef<PendingImage[]>([]);
  const pendingMessageDeltaRef = useRef<PendingMessageDelta | null>(null);
  const messageDeltaFrameRef = useRef<number | null>(null);
  const streamedPlaceCardsRef = useRef<StreamedPlaceCardBatch | null>(null);

  const activeProvider = useMemo(
    () =>
      providerSettings?.providers.find(
        (provider) => provider.provider === selectedProvider,
      ) ?? null,
    [providerSettings, selectedProvider],
  );
  const modelOptions = useMemo(() => {
    return selectableModelOptions(
      selectedModel,
      activeProvider?.model_options ?? [],
      activeProvider?.default_model ?? selectedModel,
    );
  }, [activeProvider, selectedModel]);
  const imageInputEnabled = providerSettings?.image_input_enabled === true;
  const imageEditingEnabled =
    imageInputEnabled &&
    (providerSettings?.providers ?? []).some(
      (provider) =>
        provider.configured &&
        provider.image_generation_model_options.some(
          (option) => option.hosted_api_supports_image_editing,
        ),
    );
  const isBusy = isStreaming;
  const selectedModelOption = activeProvider?.model_options.find(
    (option) => option.model === selectedModel,
  );
  const routedImageModelOption =
    selectedModelOption?.supports_image_input === true
      ? selectedModelOption
      : activeProvider?.model_options.find(
          (option) =>
            option.model === activeProvider.default_multimodal_model &&
            option.supports_image_input,
        );
  const supportsImageInput =
    imageInputEnabled && routedImageModelOption !== undefined;
  const supportsComposerImageInput = supportsImageInput || imageEditingEnabled;
  const maxComposerImages = MAX_DRAFT_IMAGES;
  const imageAttachmentTitle =
    routedImageModelOption !== undefined &&
    routedImageModelOption.model !== selectedModel
      ? `Add images · Uses ${routedImageModelOption.label}`
      : "Add images";
  useEffect(() => {
    pendingImagesRef.current = pendingImages;
  }, [pendingImages]);

  useEffect(
    () => () => {
      for (const image of pendingImagesRef.current) {
        URL.revokeObjectURL(image.previewUrl);
      }
    },
    [],
  );

  const applyPendingMessageDelta = useCallback(() => {
    const pending = pendingMessageDeltaRef.current;
    pendingMessageDeltaRef.current = null;
    if (pending === null) {
      return;
    }
    setMessages((current) =>
      current.map((message) =>
        message.id === pending.assistantMessageId
          ? {
              ...message,
              activity: undefined,
              text: `${message.text}${pending.text}`,
            }
          : message,
      ),
    );
    setActivities((current) => upsertStreamingActivity(current));
  }, []);

  const flushPendingMessageDelta = useCallback(() => {
    if (messageDeltaFrameRef.current !== null) {
      window.cancelAnimationFrame(messageDeltaFrameRef.current);
      messageDeltaFrameRef.current = null;
    }
    applyPendingMessageDelta();
  }, [applyPendingMessageDelta]);

  const queueMessageDelta = useCallback(
    (assistantMessageId: string, delta: string) => {
      if (!delta) {
        return;
      }
      const pending = pendingMessageDeltaRef.current;
      if (pending !== null && pending.assistantMessageId !== assistantMessageId) {
        flushPendingMessageDelta();
      }
      const current = pendingMessageDeltaRef.current;
      pendingMessageDeltaRef.current = {
        assistantMessageId,
        text: `${current?.text ?? ""}${delta}`,
      };
      if (messageDeltaFrameRef.current === null) {
        messageDeltaFrameRef.current = window.requestAnimationFrame(() => {
          messageDeltaFrameRef.current = null;
          applyPendingMessageDelta();
        });
      }
    },
    [applyPendingMessageDelta, flushPendingMessageDelta],
  );

  useEffect(
    () => () => {
      if (messageDeltaFrameRef.current !== null) {
        window.cancelAnimationFrame(messageDeltaFrameRef.current);
      }
      messageDeltaFrameRef.current = null;
      pendingMessageDeltaRef.current = null;
    },
    [],
  );

  const refreshChats = useCallback(async () => {
    const loadedChats = await loadChats();
    setChats(loadedChats);
    setSelectedChat((current) =>
      current === null
        ? current
        : loadedChats.find((chat) => chat.id === current.id) ?? null,
    );
  }, []);

  const applyChatDetail = useCallback((
    detail: ChatDetail,
    transientPlaceCards?: StreamedPlaceCardBatch | null,
  ) => {
    const url = new URL(window.location.href);
    url.searchParams.set("chat", detail.chat.id);
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    const delegatedRuns = detail.delegated_runs ?? [];
    setSelectedChat(detail.chat);
    setSelectedProvider(detail.chat.provider);
    setSelectedModel(detail.chat.model);
    setTitleDraft(detail.chat.title ?? "New chat");
    const durableMessages = uiMessagesFromDetail(detail);
    setMessages((current) =>
      transientPlaceCards === undefined
        ? durableMessages
        : withRetainedPlaceCards(
            durableMessages,
            current,
            transientPlaceCards,
          ),
    );
    setActivities(activityEntriesFromDetail(detail));
    setHasActiveDelegatedRuns(
      delegatedRuns.some((run) =>
        ["queued", "running", "cancelling"].includes(run.status),
      ),
    );
  }, []);

  const discardPendingImages = useCallback(
    async (chatId: string | null = selectedChat?.id ?? null) => {
      const images = pendingImagesRef.current;
      setPendingImages([]);
      for (const image of images) {
        URL.revokeObjectURL(image.previewUrl);
      }
      if (chatId !== null) {
        await Promise.allSettled(
          images
            .filter(
              (
                image,
              ): image is PendingImage & { uploaded: ChatAttachmentSummary } =>
                image.uploaded !== undefined,
            )
            .map((image) => deleteChatAttachment(chatId, image.uploaded.id)),
        );
      }
      setDraftId(crypto.randomUUID());
    },
    [selectedChat?.id],
  );

  const clearSentImages = useCallback(() => {
    const images = pendingImagesRef.current;
    setPendingImages([]);
    for (const image of images) {
      URL.revokeObjectURL(image.previewUrl);
    }
    setDraftId(crypto.randomUUID());
  }, []);

  const selectChat = useCallback(
    async (chat: ChatSummary) => {
      setError(null);
      try {
        await discardPendingImages();
        const detail = await loadChat(chat.id);
        applyChatDetail(detail);
      } catch (selectError) {
        setError(authErrorMessage(selectError));
      }
    },
    [applyChatDetail, discardPendingImages],
  );

  useEffect(() => {
    resizeComposerTextarea(composerTextareaRef.current);
  }, [draft]);

  useEffect(() => {
    let cancelled = false;

    const fetchInitialState = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const [settings, loadedChats] = await Promise.all([
          loadTextProviderSettings(),
          loadChats(),
        ]);
        if (cancelled) {
          return;
        }
        setProviderSettings(settings);
        setSelectedProvider(settings.active_provider);
        setSelectedModel(settings.active_settings.model);
        setChats(loadedChats);
        const requestedChatId = new URLSearchParams(window.location.search).get("chat");
        let initialDetail: ChatDetail | null = null;
        if (requestedChatId !== null) {
          try {
            initialDetail = await loadChat(requestedChatId);
          } catch (requestedChatError) {
            if (!(requestedChatError instanceof ApiRequestError) || requestedChatError.status !== 404) {
              throw requestedChatError;
            }
            if (!cancelled) {
              setError("The linked chat is unavailable. Showing your most recent chat instead.");
            }
          }
        }
        if (cancelled) {
          return;
        }
        if (initialDetail !== null) {
          if (!loadedChats.some((chat) => chat.id === initialDetail.chat.id)) {
            setChats([initialDetail.chat, ...loadedChats]);
          }
          applyChatDetail(initialDetail);
        } else if (loadedChats.length > 0) {
          const fallbackDetail = await loadChat(loadedChats[0].id);
          if (!cancelled) {
            applyChatDetail(fallbackDetail);
          }
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(authErrorMessage(loadError));
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    };

    void fetchInitialState();

    return () => {
      cancelled = true;
    };
  }, [applyChatDetail]);

  useEffect(() => {
    const chatId = selectedChat?.id;
    if (chatId === undefined || !hasActiveDelegatedRuns || isStreaming) {
      return;
    }
    let cancelled = false;
    let loading = false;
    const refreshDelegatedRuns = async () => {
      if (loading) {
        return;
      }
      loading = true;
      try {
        const delegatedRuns = await loadChatDelegatedRuns(chatId);
        if (cancelled) {
          return;
        }
        const stillActive = delegatedRuns.some((run) =>
          ["queued", "running", "cancelling"].includes(run.status),
        );
        if (!stillActive) {
          const detail = await loadChat(chatId);
          if (!cancelled) {
            applyChatDetail(detail, null);
          }
        }
      } catch (pollError) {
        if (!cancelled) {
          setError(authErrorMessage(pollError));
        }
      } finally {
        loading = false;
      }
    };
    const intervalId = window.setInterval(() => {
      void refreshDelegatedRuns();
    }, 1_500);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [applyChatDetail, hasActiveDelegatedRuns, isStreaming, selectedChat?.id]);

  const startNewChat = async () => {
    if (providerSettings === null || isLoading) {
      return;
    }
    await discardPendingImages();
    setSelectedProvider(providerSettings.active_provider);
    setSelectedModel(providerSettings.active_settings.model);
    setSelectedChat(null);
    setMessages([]);
    setActivities([]);
    setHasActiveDelegatedRuns(false);
    setTitleDraft("");
    setDraft("");
    resizeComposerTextarea(composerTextareaRef.current);
  };

  const addImages = (files: FileList | readonly File[] | null) => {
    if (files === null || files.length === 0 || isBusy) {
      return;
    }
    const available = maxComposerImages - pendingImages.length;
    if (available <= 0) {
      setError(`This request can include at most ${maxComposerImages} images.`);
      return;
    }
    const accepted: PendingImage[] = [];
    for (const file of Array.from(files).slice(0, available)) {
      if (!ACCEPTED_IMAGE_TYPES.has(file.type.toLowerCase())) {
        setError(`${file.name} is not a JPEG, PNG, or HEIC image.`);
        continue;
      }
      if (file.size > MAX_SOURCE_IMAGE_BYTES) {
        setError(`${file.name} is larger than 25 MiB.`);
        continue;
      }
      accepted.push({
        id: crypto.randomUUID(),
        file,
        previewUrl: URL.createObjectURL(file),
        status: "selected",
      });
    }
    if (accepted.length > 0) {
      setPendingImages((current) => [...current, ...accepted]);
      setError(
        files.length > available
          ? `This request can include at most ${maxComposerImages} images.`
          : null,
      );
    }
    if (fileInputRef.current !== null) {
      fileInputRef.current.value = "";
    }
  };

  const resetImageDrag = () => {
    imageDragDepthRef.current = 0;
    setIsDraggingImages(false);
  };

  const handleImageDragEnter = (event: ReactDragEvent<HTMLElement>) => {
    if (!hasDraggedFiles(event)) {
      return;
    }
    event.preventDefault();
    imageDragDepthRef.current += 1;
    if (
      canEdit &&
      supportsComposerImageInput &&
      !isBusy &&
      pendingImages.length < maxComposerImages
    ) {
      setIsDraggingImages(true);
    }
  };

  const handleImageDragOver = (event: ReactDragEvent<HTMLElement>) => {
    if (!hasDraggedFiles(event)) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect =
      canEdit &&
      supportsComposerImageInput &&
      !isBusy &&
      pendingImages.length < maxComposerImages
        ? "copy"
        : "none";
  };

  const handleImageDragLeave = (event: ReactDragEvent<HTMLElement>) => {
    if (!hasDraggedFiles(event)) {
      return;
    }
    event.preventDefault();
    imageDragDepthRef.current = Math.max(0, imageDragDepthRef.current - 1);
    if (imageDragDepthRef.current === 0) {
      setIsDraggingImages(false);
    }
  };

  const handleImageDrop = (event: ReactDragEvent<HTMLElement>) => {
    if (!hasDraggedFiles(event)) {
      return;
    }
    event.preventDefault();
    resetImageDrag();
    if (!canEdit || isBusy) {
      return;
    }
    if (!imageInputEnabled) {
      setError("Image input is not enabled for this deployment.");
      return;
    }
    if (!supportsComposerImageInput) {
      setError("The selected model cannot analyze images and no image editor is enabled.");
      return;
    }
    addImages(event.dataTransfer.files);
  };

  const handleImagePaste = (
    event: ReactClipboardEvent<HTMLTextAreaElement>,
  ) => {
    const itemImages = Array.from(event.clipboardData.items)
      .filter(
        (item) => item.kind === "file" && item.type.startsWith("image/"),
      )
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null);
    const images =
      itemImages.length > 0
        ? itemImages
        : Array.from(event.clipboardData.files).filter((file) =>
            file.type.startsWith("image/"),
          );
    if (images.length === 0) {
      return;
    }
    event.preventDefault();
    if (!canEdit || isBusy) {
      return;
    }
    if (!imageInputEnabled) {
      setError("Image input is not enabled for this deployment.");
      return;
    }
    if (!supportsComposerImageInput) {
      setError(
        "The selected model cannot analyze images and no image editor is enabled.",
      );
      return;
    }
    addImages(images);
  };

  const removePendingImage = async (imageId: string) => {
    if (isBusy) {
      return;
    }
    const image = pendingImages.find((candidate) => candidate.id === imageId);
    if (image === undefined) {
      return;
    }
    setPendingImages((current) =>
      current.filter((candidate) => candidate.id !== imageId),
    );
    URL.revokeObjectURL(image.previewUrl);
    if (image.uploaded !== undefined && selectedChat !== null) {
      try {
        await deleteChatAttachment(selectedChat.id, image.uploaded.id);
      } catch (deleteError) {
        setError(authErrorMessage(deleteError));
      }
    }
  };

  const movePendingImage = (imageId: string, offset: -1 | 1) => {
    if (isBusy) {
      return;
    }
    setPendingImages((current) => {
      const index = current.findIndex((image) => image.id === imageId);
      const target = index + offset;
      if (index < 0 || target < 0 || target >= current.length) {
        return current;
      }
      const reordered = [...current];
      [reordered[index], reordered[target]] = [
        reordered[target],
        reordered[index],
      ];
      return reordered;
    });
  };

  const prepareChat = async (title: string): Promise<ChatSummary> => {
    let chat = selectedChat;
    if (chat === null) {
      chat = await createChat({
        model: selectedModel,
        provider: selectedProvider,
        title: title.slice(0, 64) || "Image",
      });
      setSelectedChat(chat);
      setTitleDraft(chat.title ?? "New chat");
    } else if (chat.provider !== selectedProvider || chat.model !== selectedModel) {
      chat = await updateChat(chat.id, {
        model: selectedModel,
        provider: selectedProvider,
      });
      setSelectedChat(chat);
    }
    return chat;
  };

  const uploadPendingImages = async (
    chat: ChatSummary,
    frozenImages: PendingImage[],
  ): Promise<ChatAttachmentSummary[]> => {
    setPendingImages((current) =>
      current.map((image) =>
        frozenImages.some(
          (frozen) => frozen.id === image.id && frozen.uploaded === undefined,
        )
          ? { ...image, error: undefined, status: "uploading" }
          : image,
      ),
    );
    return uploadImagesWithConcurrency(frozenImages, 2, async (image) => {
      if (image.uploaded !== undefined) {
        return image.uploaded;
      }
      try {
        const attachment = await uploadChatAttachment(
          chat.id,
          image.file,
          draftId,
        );
        setPendingImages((current) =>
          current.map((candidate) =>
            candidate.id === image.id
              ? {
                  ...candidate,
                  status: "uploaded",
                  uploaded: attachment,
                }
              : candidate,
          ),
        );
        return attachment;
      } catch (uploadError) {
        const message = authErrorMessage(uploadError);
        setPendingImages((current) =>
          current.map((candidate) =>
            candidate.id === image.id
              ? { ...candidate, error: message, status: "selected" }
              : candidate,
          ),
        );
        throw uploadError;
      }
    });
  };

  const sendMessage = async () => {
    const text = draft.trim();
    const frozenImages = [...pendingImages];
    if (
      (!text && frozenImages.length === 0) ||
      isLoading ||
      isBusy
    ) {
      return;
    }
    if (activeProvider?.configured !== true) {
      setError(
        `Save a ${activeProvider?.label ?? "provider"} key in Providers before chatting.`,
      );
      return;
    }

    setError(null);
    setIsStreaming(true);
    activeRunIdRef.current = null;
    const controller = new AbortController();
    abortControllerRef.current = controller;
    const userMessage: UiMessage = {
      id: crypto.randomUUID(),
      role: "user",
      source: "text_input",
      text,
      attachments: [],
      placeCards: [],
    };
    const assistantMessageId = crypto.randomUUID();
    streamedPlaceCardsRef.current = null;

    try {
      const chat = await prepareChat(text);
      const uploaded = await uploadPendingImages(chat, frozenImages);
      userMessage.attachments = frozenImages.map((image, index) => ({
        id: uploaded[index].id,
        localUrl: image.previewUrl,
        summary: uploaded[index],
      }));
      setMessages((current) => [
        ...current,
        userMessage,
        {
          id: assistantMessageId,
          role: "assistant",
          source: "text_output",
          text: "",
          attachments: [],
          placeCards: [],
          isStreaming: true,
          activity: thinkingActivity(),
        },
      ]);
      const currentLocation =
        (await locationSharing?.snapshotForAction()) ?? null;
      if (
        locationSharing?.isEnabled === true &&
        currentLocation === null &&
        locationSharing.lastError !== null
      ) {
        addLog(
          `Location unavailable; sending without it: ${locationSharing.lastError.message}`,
          "error",
        );
      }
      await streamChatMessage(chat.id, text, {
        attachmentIds: uploaded.map((attachment) => attachment.id),
        clientTurnId: draftId,
        location: currentLocation,
        signal: controller.signal,
        onEvent: (event) => handleStreamEvent(event, assistantMessageId),
      });
      flushPendingMessageDelta();
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantMessageId
            ? { ...message, activity: undefined, isStreaming: false }
            : message,
        ),
      );
      await refreshChats();
      const detail = await loadChat(chat.id);
      applyChatDetail(detail, streamedPlaceCardsRef.current);
      streamedPlaceCardsRef.current = null;
      setDraft("");
      clearSentImages();
    } catch (streamError) {
      flushPendingMessageDelta();
      setDraftId(crypto.randomUUID());
      if (streamError instanceof DOMException && streamError.name === "AbortError") {
        addLog("Text stream stopped.");
      } else {
        const message = authErrorMessage(streamError);
        setError(message);
        addLog(`Text stream failed: ${message}`, "error");
      }
      setMessages((current) =>
        current.filter((message) => {
          if (
            message.id === assistantMessageId &&
            message.text === "" &&
            activeRunIdRef.current === null
          ) {
            return false;
          }
          return true;
        }).map((message) =>
          message.id === assistantMessageId
            ? { ...message, activity: undefined, isStreaming: false }
            : message,
        ),
      );
    } finally {
      abortControllerRef.current = null;
      activeRunIdRef.current = null;
      setIsStreaming(false);
    }
  };

  const handleStreamEvent = (
    event: ChatStreamEvent,
    assistantMessageId: string,
  ) => {
    if (event.run_id) {
      activeRunIdRef.current = event.run_id;
    }
    if (
      event.kind === "run_completed" ||
      event.kind === "run_cancelled" ||
      event.kind === "run_failed"
    ) {
      activeRunIdRef.current = null;
    }
    if (event.kind === "message_delta") {
      const delta =
        typeof event.payload.delta === "string" ? event.payload.delta : "";
      queueMessageDelta(assistantMessageId, delta);
      return;
    }
    flushPendingMessageDelta();
    if (event.kind === "tool_call_started") {
      const activity =
        chatActivityFromEvent(event) ?? fallbackToolActivity(event);
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantMessageId
            ? { ...message, activity }
            : message,
        ),
      );
    } else if (
      event.kind === "tool_call_completed" ||
      event.kind === "tool_call_failed"
    ) {
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantMessageId
            ? { ...message, activity: thinkingActivity() }
            : message,
        ),
      );
    } else if (
      event.kind === "run_completed" ||
      event.kind === "run_cancelled" ||
      event.kind === "run_failed"
    ) {
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantMessageId
            ? { ...message, activity: undefined }
            : message,
        ),
      );
    }
    if (
      event.kind === "tool_call_completed" &&
      event.payload.name === "generate_image"
    ) {
      const attachments = attachmentSummariesFromEvent(event);
      if (attachments.length > 0) {
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantMessageId
              ? {
                  ...message,
                  attachments: attachments.map((summary) => ({
                    id: summary.id,
                    summary,
                  })),
                }
              : message,
          ),
        );
      }
    }
    if (event.kind === "message_completed") {
      const placeCards = placeCardsFromEvent(event);
      const messageId =
        typeof event.payload.message_id === "string" &&
        event.payload.message_id.trim() !== ""
          ? event.payload.message_id.trim()
          : null;
      streamedPlaceCardsRef.current =
        messageId === null || placeCards.length === 0
          ? null
          : { messageId, placeCards };
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantMessageId
            ? { ...message, placeCards }
            : message,
        ),
      );
    }

    setActivities((current) =>
      prependActivity(
        current,
        activityFromStreamEvent(event),
        event.kind === "message_completed" ||
          event.kind === "run_completed" ||
          event.kind === "run_cancelled" ||
          event.kind === "run_failed",
      ),
    );
  };

  const stopStreaming = async () => {
    const controller = abortControllerRef.current;
    if (controller === null) {
      return;
    }
    abortControllerRef.current = null;
    const runId = activeRunIdRef.current;
    const cancellationRequest =
      runId === null ? null : cancelRun(runId, "Stopped from Chat");
    controller.abort();
    if (cancellationRequest === null) {
      return;
    }
    try {
      const cancelledRun = await cancellationRequest;
      setActivities((current) =>
        prependActivity(
          current,
          {
            id: crypto.randomUUID(),
            kind: "run_cancelled",
            label:
              cancelledRun.status === "cancelled"
                ? "Run cancelled"
                : "Cancellation requested",
            detail: null,
            level: "info",
            facts: [],
          },
          true,
        ),
      );
    } catch (cancelError) {
      const message = authErrorMessage(cancelError);
      setError(message);
      addLog(`Run cancellation failed: ${message}`, "error");
    }
  };

  const renameChat = async () => {
    if (selectedChat === null || titleDraft.trim() === "") {
      return;
    }
    try {
      const chat = await updateChat(selectedChat.id, {
        title: titleDraft.trim(),
      });
      setSelectedChat(chat);
      await refreshChats();
    } catch (renameError) {
      setError(authErrorMessage(renameError));
    }
  };

  const removeChat = async () => {
    if (selectedChat === null) {
      return;
    }
    const confirmed = window.confirm("Delete this chat?");
    if (!confirmed) {
      return;
    }
    try {
      await deleteChat(selectedChat.id);
      await startNewChat();
      await refreshChats();
    } catch (deleteError) {
      setError(authErrorMessage(deleteError));
    }
  };

  return (
    <div className="chat-page">
      <aside className="chat-history-panel">
        <div className="section-heading-row">
          <div className="section-title">
            <p className="eyebrow">Text</p>
            <h2>Chats</h2>
          </div>
          <button
            className="icon-button light"
            disabled={
              !canEdit || providerSettings === null || isLoading || isBusy
            }
            onClick={() => void startNewChat()}
            title="New chat"
            type="button"
          >
            <MessageSquarePlus aria-hidden="true" size={18} />
          </button>
        </div>
        <div className="chat-history-list">
          {chats.map((chat) => (
            <button
              className={selectedChat?.id === chat.id ? "selected" : ""}
              key={chat.id}
              onClick={() => void selectChat(chat)}
              type="button"
            >
              <strong>{chat.title ?? "New chat"}</strong>
              <span>{chat.model}</span>
            </button>
          ))}
          {!isLoading && chats.length === 0 ? (
            <p className="muted">No saved chats.</p>
          ) : null}
        </div>
      </aside>

      <section
        aria-label="Chat conversation"
        className={`chat-main-panel${isDraggingImages ? " is-image-drop-target" : ""}`}
        onDragEnter={handleImageDragEnter}
        onDragLeave={handleImageDragLeave}
        onDragOver={handleImageDragOver}
        onDrop={handleImageDrop}
      >
        {isDraggingImages ? (
          <div aria-live="polite" className="chat-image-drop-overlay" role="status">
            <Paperclip aria-hidden="true" size={28} />
            <strong>Drop images to attach</strong>
            <span>JPEG, PNG, or HEIC · up to four images per message</span>
          </div>
        ) : null}
        <header className="chat-toolbar">
          <div className="chat-title-edit">
            <input
              disabled={selectedChat === null || !canEdit}
              onBlur={() => void renameChat()}
              onChange={(event) => setTitleDraft(event.currentTarget.value)}
              placeholder="New chat"
              value={titleDraft}
            />
          </div>
          <div className="chat-model-controls">
            {activeProvider !== null ? (
              <ServiceLogo
                label={activeProvider.label}
                logo={activeProvider.logo}
                size="sm"
              />
            ) : null}
            <select
              disabled={!canEdit || isBusy || pendingImages.length > 0}
              onChange={(event) => {
                const provider = event.currentTarget.value as TextProvider;
                const definition = providerSettings?.providers.find(
                  (candidate) => candidate.provider === provider,
                );
                setSelectedProvider(provider);
                setSelectedModel(definition?.default_model ?? DEFAULT_TEXT_MODEL);
              }}
              value={selectedProvider}
            >
              {(providerSettings?.providers ?? []).map((provider) => (
                <option key={provider.provider} value={provider.provider}>
                  {provider.label}
                </option>
              ))}
            </select>
            <select
              disabled={!canEdit || isBusy || pendingImages.length > 0}
              onChange={(event) => setSelectedModel(event.currentTarget.value)}
              value={selectedModel}
            >
              {modelOptions.map((model) => (
                <option key={model.value} value={model.value}>
                  {model.value}
                </option>
              ))}
            </select>
            <button
              className="danger"
              disabled={selectedChat === null || isBusy}
              onClick={() => void removeChat()}
              title="Delete chat"
              type="button"
            >
              <Trash2 aria-hidden="true" size={16} />
            </button>
          </div>
        </header>

        <div className="chat-transcript">
          {messages.length > 0 ? (
            messages.map((message) => (
              <article className={`chat-bubble ${message.role}`} key={message.id}>
                <span className="chat-bubble-role">{messageRoleLabel(message)}</span>
                {message.interrupted ? <span className="muted">Partial reply</span> : null}
                {message.attachments.length > 0 && selectedChat !== null ? (
                  <div
                    aria-label={`${message.attachments.length} attached ${
                      message.attachments.length === 1 ? "image" : "images"
                    }`}
                    className="chat-message-images"
                  >
                    {message.attachments.map((attachment, index) =>
                      attachment.localUrl !== undefined ? (
                        <img
                          alt={`Attached image ${index + 1}`}
                          key={attachment.id}
                          src={attachment.localUrl}
                        />
                      ) : attachment.summary !== undefined ? (
                        <AuthenticatedAttachmentImage
                          attachment={attachment.summary}
                          chatId={selectedChat.id}
                          index={index}
                          key={attachment.id}
                        />
                      ) : null,
                    )}
                  </div>
                ) : null}
                {message.role === "assistant" ? (
                  <>
                    {message.text === "" ? null : (
                      <MarkdownMessage
                        isStreaming={message.isStreaming}
                        placeCards={message.placeCards}
                        text={message.text}
                      />
                    )}
                    {message.isStreaming &&
                    (message.text === "" || message.activity !== undefined) ? (
                      <ChatMessageActivity
                        activity={message.activity ?? thinkingActivity()}
                      />
                    ) : null}
                  </>
                ) : message.text !== "" ? (
                  <p className="chat-message-content chat-message-plain">
                    {message.text}
                  </p>
                ) : null}
              </article>
            ))
          ) : (
            <div className="chat-empty-state">
              <h2>Start a conversation</h2>
              <p className="muted">
                Pick a provider and model above, then send a message. Replies and
                tool activity stream in live.
              </p>
            </div>
          )}
        </div>

        {error !== null ? <p className="error-text">{error}</p> : null}

        <form
          className="chat-composer"
          onSubmit={(event: FormEvent<HTMLFormElement>) => {
            event.preventDefault();
            void sendMessage();
          }}
        >
          {pendingImages.length > 0 ? (
            <div className="chat-draft-images">
              {pendingImages.map((image, index) => (
                <div className="chat-draft-image" key={image.id}>
                  <img alt={`Selected image ${index + 1}`} src={image.previewUrl} />
                  <div className="chat-draft-image-details">
                    <strong>{image.file.name}</strong>
                    <small>
                      {image.file.type || "image"} · {formatBytes(image.file.size)} ·{" "}
                      {image.status}
                    </small>
                    {image.error !== undefined ? (
                      <small className="error-text">{image.error}</small>
                    ) : null}
                  </div>
                  <div className="chat-draft-image-actions">
                    <button
                      aria-label={`Move ${image.file.name} left`}
                      disabled={isBusy || index === 0}
                      onClick={() => movePendingImage(image.id, -1)}
                      type="button"
                    >
                      <ChevronLeft aria-hidden="true" size={15} />
                    </button>
                    <button
                      aria-label={`Move ${image.file.name} right`}
                      disabled={isBusy || index === pendingImages.length - 1}
                      onClick={() => movePendingImage(image.id, 1)}
                      type="button"
                    >
                      <ChevronRight aria-hidden="true" size={15} />
                    </button>
                    <button
                      aria-label={`Remove ${image.file.name}`}
                      disabled={isBusy}
                      onClick={() => void removePendingImage(image.id)}
                      type="button"
                    >
                      <X aria-hidden="true" size={15} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : null}
          <div className="chat-composer-row">
            {imageInputEnabled ? (
              <>
                <input
                  accept="image/jpeg,image/png,image/heic,image/heif"
                  aria-label="Choose images"
                  className="visually-hidden"
                  disabled={
                    !canEdit ||
                    !supportsComposerImageInput ||
                    isBusy ||
                    pendingImages.length >= maxComposerImages
                  }
                  multiple
                  onChange={(event) => addImages(event.currentTarget.files)}
                  ref={fileInputRef}
                  type="file"
                />
                <button
                  aria-label="Add images"
                  disabled={
                    !canEdit ||
                    !supportsComposerImageInput ||
                    isBusy ||
                    pendingImages.length >= maxComposerImages
                  }
                  onClick={() => fileInputRef.current?.click()}
                  title={
                    supportsComposerImageInput
                      ? imageAttachmentTitle
                      : "The selected model does not support images and no image editor is enabled"
                  }
                  type="button"
                >
                  <Paperclip aria-hidden="true" size={17} />
                </button>
              </>
            ) : null}
          <textarea
            disabled={!canEdit || isBusy}
            onInput={(event) => resizeComposerTextarea(event.currentTarget)}
            onChange={(event) => setDraft(event.currentTarget.value)}
            onPaste={handleImagePaste}
            placeholder="Message your agent"
            ref={composerTextareaRef}
            rows={1}
            value={draft}
          />
          {isStreaming ? (
            <button
              onClick={() => void stopStreaming()}
              title="Stop run"
              type="button"
            >
              <Square aria-hidden="true" size={17} />
            </button>
          ) : (
            <button
              className="primary"
              disabled={
                !canEdit ||
                isLoading ||
                (draft.trim() === "" && pendingImages.length === 0)
              }
              title="Send message"
              type="submit"
            >
              <Send aria-hidden="true" size={17} />
            </button>
          )}
          </div>
        </form>
      </section>

      <aside className="chat-activity-panel">
        <div className="section-title">
          <p className="eyebrow">Run</p>
          <h2>Activity</h2>
        </div>
        <div className="chat-activity-list">
          {activities.length > 0 ? (
            activities.map((activity) => {
              const hasDetails = activity.facts.length > 0;
              const activitySummary = (
                <>
                  {activity.kind.startsWith("tool") ? (
                    <Wrench aria-hidden="true" size={16} />
                  ) : activity.level === "success" ? (
                    <CheckCircle2 aria-hidden="true" size={16} />
                  ) : (
                    <Loader2 aria-hidden="true" size={16} />
                  )}
                  <div className="chat-activity-copy">
                    <div className="chat-activity-title-row">
                      <strong>{activity.label}</strong>
                      {hasDetails ? (
                        <span
                          aria-hidden="true"
                          className="chat-activity-hint"
                        />
                      ) : null}
                    </div>
                    {activity.detail !== null ? (
                      <small>{activity.detail}</small>
                    ) : null}
                  </div>
                </>
              );
              if (!hasDetails) {
                return (
                  <article
                    aria-label={activity.label}
                    className={`chat-activity ${activity.level}`}
                    key={activity.id}
                  >
                    {activitySummary}
                  </article>
                );
              }
              return (
                <details
                  className={`chat-activity ${activity.level} has-details`}
                  key={activity.id}
                >
                  <summary
                    aria-label={`${activity.label}. Click to expand or collapse details.`}
                    className="chat-activity-summary"
                  >
                    {activitySummary}
                  </summary>
                  <dl className="chat-activity-details">
                    {activity.facts.map((fact) => (
                      <div key={`${fact.label}:${fact.value}`}>
                        <dt>{fact.label}</dt>
                        <dd>
                          {fact.code ? <code>{fact.value}</code> : fact.value}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </details>
              );
            })
          ) : (
            <p className="muted">Run events will appear here.</p>
          )}
        </div>
      </aside>
    </div>
  );
}

function ChatMessageActivity({
  activity,
}: {
  activity: ChatActivityPresentation;
}) {
  return (
    <div
      aria-label={activity.label}
      aria-live="polite"
      className="chat-message-activity-state"
      role="status"
    >
      {activity.icon === "web" ? (
        <Globe2
          aria-hidden="true"
          className="chat-message-activity-icon"
          size={20}
        />
      ) : activity.logo !== null ? (
        <ServiceLogo
          className="chat-message-activity-logo"
          decorative
          label={activity.logo.label}
          logo={activity.logo}
          size="sm"
        />
      ) : activity.icon === "mcp" ? (
        <PlugZap
          aria-hidden="true"
          className="chat-message-activity-icon"
          size={20}
        />
      ) : (
        <Loader2
          aria-hidden="true"
          className="chat-message-activity-spinner"
          size={20}
        />
      )}
      <span className="chat-message-activity-copy">
        <strong>{activity.label}</strong>
        {activity.detail === null ? null : <small>{activity.detail}</small>}
      </span>
    </div>
  );
}

function uiMessageFromRecord(message: ChatMessage): UiMessage {
  return {
    attachments: message.attachments.map((attachment) => ({
      id: attachment.id,
      summary: attachment,
    })),
    placeCards: message.place_cards,
    id: message.id,
    role: message.role,
    source: message.source,
    clientTurnId: message.client_turn_id,
    interrupted: message.interrupted,
    text: message.text,
    createdAt: message.created_at,
  };
}

function uiMessagesFromDetail(detail: ChatDetail): UiMessage[] {
  const messages = detail.messages.map(uiMessageFromRecord);
  const deliveredRunIds = new Set(
    messages.flatMap((message) => {
      const match = /^run:([^:]+):complete$/.exec(message.clientTurnId ?? "");
      return match === null ? [] : [match[1]];
    }),
  );
  const delegatedOutputs: UiMessage[] = (detail.delegated_runs ?? []).flatMap((run) =>
    run.output_text === null || deliveredRunIds.has(run.id)
      ? []
      : [
          {
            id: `delegated-output-${run.id}`,
            role: "assistant",
            source: "subagent_output",
            text: run.output_text,
            attachments: [],
            placeCards: [],
            createdAt:
              run.output_created_at ?? run.completed_at ?? run.created_at,
          },
        ],
  );
  return [...messages, ...delegatedOutputs].sort((left, right) =>
    (left.createdAt ?? "").localeCompare(right.createdAt ?? ""),
  );
}

function withRetainedPlaceCards(
  messages: UiMessage[],
  currentMessages: readonly UiMessage[],
  completedBatch: StreamedPlaceCardBatch | null,
): UiMessage[] {
  const placeCardsByMessageId = new Map<string, ChatPlaceCard[]>();
  for (const message of currentMessages) {
    if (message.placeCards.length > 0) {
      placeCardsByMessageId.set(message.id, [...message.placeCards]);
    }
  }
  if (completedBatch !== null) {
    placeCardsByMessageId.set(completedBatch.messageId, [
      ...completedBatch.placeCards,
    ]);
  }
  return messages.map((message) => {
    const placeCards = placeCardsByMessageId.get(message.id);
    return placeCards === undefined ? message : { ...message, placeCards };
  });
}

function attachmentSummariesFromEvent(
  event: ChatStreamEvent,
): ChatAttachmentSummary[] {
  const attachments = event.payload.attachments;
  if (!Array.isArray(attachments)) {
    return [];
  }
  return attachments.flatMap((attachment) => {
    if (
      typeof attachment !== "object" ||
      attachment === null ||
      typeof attachment.id !== "string" ||
      (attachment.mime_type !== "image/jpeg" &&
        attachment.mime_type !== "image/png") ||
      typeof attachment.byte_size !== "number" ||
      typeof attachment.width !== "number" ||
      typeof attachment.height !== "number" ||
      (attachment.position !== null && typeof attachment.position !== "number")
    ) {
      return [];
    }
    return [
      {
        id: attachment.id,
        mime_type: attachment.mime_type,
        byte_size: attachment.byte_size,
        width: attachment.width,
        height: attachment.height,
        position: attachment.position,
      },
    ];
  });
}

function placeCardsFromEvent(event: ChatStreamEvent): ChatPlaceCard[] {
  const placeCards = event.payload.place_cards;
  if (!Array.isArray(placeCards)) {
    return [];
  }
  return placeCards.flatMap((card) => {
    if (
      typeof card !== "object" ||
      card === null ||
      card.provider !== "google_maps" ||
      typeof card.place_id !== "string" ||
      typeof card.title !== "string" ||
      typeof card.place_url !== "string" ||
      typeof card.attribution_url !== "string" ||
      (card.directions_url !== null &&
        typeof card.directions_url !== "string") ||
      (card.latitude !== null && typeof card.latitude !== "number") ||
      (card.longitude !== null && typeof card.longitude !== "number") ||
      typeof card.position !== "number"
    ) {
      return [];
    }
    return [
      {
        provider: card.provider,
        place_id: card.place_id,
        title: card.title,
        place_url: card.place_url,
        attribution_url: card.attribution_url,
        directions_url: card.directions_url,
        latitude: card.latitude,
        longitude: card.longitude,
        position: card.position,
      },
    ];
  });
}

function messageRoleLabel(message: UiMessage): string {
  if (message.source === "subagent_task") {
    return "Delegated task";
  }
  if (message.source === "subagent_output") {
    return "Subagent";
  }
  return message.role;
}

function AuthenticatedAttachmentImage({
  attachment,
  chatId,
  index,
}: {
  attachment: ChatAttachmentSummary;
  chatId: string;
  index: number;
}) {
  const [thumbnailUrl, setThumbnailUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    void loadChatAttachment(chatId, attachment.id, "thumbnail")
      .then((blob) => {
        if (cancelled) {
          return;
        }
        objectUrl = URL.createObjectURL(blob);
        setThumbnailUrl(objectUrl);
      })
      .catch(() => {
        if (!cancelled) {
          setThumbnailUrl(null);
        }
      });
    return () => {
      cancelled = true;
      if (objectUrl !== null) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [attachment.id, chatId]);

  const openFullImage = async () => {
    const blob = await loadChatAttachment(chatId, attachment.id, "full");
    const objectUrl = URL.createObjectURL(blob);
    window.open(objectUrl, "_blank", "noopener,noreferrer");
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
  };

  return (
    <button
      aria-label={`Open attached image ${index + 1}`}
      className="chat-message-image"
      onClick={() => void openFullImage()}
      type="button"
    >
      {thumbnailUrl === null ? (
        <span>Loading image…</span>
      ) : (
        <img alt={`Attached image ${index + 1}`} src={thumbnailUrl} />
      )}
    </button>
  );
}

async function uploadImagesWithConcurrency(
  images: readonly PendingImage[],
  concurrency: number,
  upload: (image: PendingImage) => Promise<ChatAttachmentSummary>,
): Promise<ChatAttachmentSummary[]> {
  const results = new Array<ChatAttachmentSummary>(images.length);
  let nextIndex = 0;
  const worker = async () => {
    while (nextIndex < images.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await upload(images[index]);
    }
  };
  await Promise.all(
    Array.from(
      { length: Math.min(concurrency, images.length) },
      () => worker(),
    ),
  );
  return results;
}

function hasDraggedFiles(event: ReactDragEvent<HTMLElement>): boolean {
  return Array.from(event.dataTransfer.types).includes("Files");
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) {
    return `${Math.ceil(bytes / 1024)} KiB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function activityEntriesFromDetail(detail: ChatDetail): ActivityEntry[] {
  const toolEventsByCallId = new Map(
    detail.tool_events.map((event) => [event.call_id, event]),
  );
  const timedEntries = detail.runtime_events.map((event) => {
      const streamEvent: ChatStreamEvent = {
        id: event.id,
        kind: event.kind,
        payload: event.payload_json,
        created_at: event.created_at,
      };
      const callId = payloadString(streamEvent, "call_id");
      return {
        createdAt: event.created_at,
        entry: activityFromStreamEvent(
          streamEvent,
          callId === null ? null : (toolEventsByCallId.get(callId) ?? null),
        ),
      };
    });
  for (const run of detail.delegated_runs ?? []) {
    timedEntries.push({
      createdAt: run.created_at,
      entry: delegatedRunQueuedActivity(run),
    });
    const childToolsByCallId = new Map(
      run.tool_events.map((event) => [event.call_id, event]),
    );
    for (const event of run.runtime_events) {
      const streamEvent: ChatStreamEvent = {
        id: event.id,
        kind: event.kind,
        payload: event.payload_json,
        created_at: event.created_at,
      };
      const callId = payloadString(streamEvent, "call_id");
      const childEntry = activityFromStreamEvent(
        streamEvent,
        callId === null ? null : (childToolsByCallId.get(callId) ?? null),
      );
      timedEntries.push({
        createdAt: event.created_at,
        entry: {
          ...childEntry,
          facts: [
            { label: "Delegated run", value: run.id, code: true },
            ...childEntry.facts,
          ],
        },
      });
    }
  }
  return uniqueActivities(
    timedEntries
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt))
      .map(({ entry }) => entry),
  );
}

function uniqueActivities(entries: ActivityEntry[]): ActivityEntry[] {
  const seenIds = new Set<string>();
  return entries.filter((entry) => {
    if (seenIds.has(entry.id)) {
      return false;
    }
    seenIds.add(entry.id);
    return true;
  });
}

function delegatedRunQueuedActivity(run: ChatDelegatedRun): ActivityEntry {
  return {
    id: `delegated-run-${run.id}`,
    kind: "run_started",
    label: "Delegated task queued",
    detail:
      run.task_prompt.length <= 180
        ? run.task_prompt
        : `${run.task_prompt.slice(0, 177)}…`,
    level: "info",
    facts: [
      { label: "Run", value: run.id, code: true },
      { label: "Status", value: run.status },
      ...(run.model === null
        ? []
        : [{ label: "Model", value: run.model, code: true }]),
    ],
  };
}

function activityFromStreamEvent(
  event: ChatStreamEvent,
  toolEvent: ChatToolEvent | null = null,
): ActivityEntry {
  if (event.kind === "message_delta") {
    return streamingActivity();
  }
  const id = event.id ?? crypto.randomUUID();
  if (event.kind === "tool_call_started") {
    const toolName = toolNameFromEvent(event, toolEvent);
    return {
      id,
      kind: event.kind,
      label: "Calling tool",
      detail: displayToolName(toolName),
      level: "info",
      facts: toolActivityFacts(event, toolEvent),
    };
  }
  if (event.kind === "tool_call_completed") {
    const isError =
      payloadBoolean(event, "is_error") === true || toolEvent?.is_error === true;
    const toolName = toolNameFromEvent(event, toolEvent);
    const errorMessage = isError
      ? toolErrorMessage(event, toolEvent)
      : null;
    return {
      id,
      kind: event.kind,
      label: isError ? "Tool failed" : "Tool completed",
      detail:
        errorMessage === null
          ? displayToolName(toolName)
          : `${displayToolName(toolName) ?? "Tool"}: ${errorMessage}`,
      level: isError ? "error" : "success",
      facts: toolActivityFacts(event, toolEvent, isError),
    };
  }
  if (event.kind === "tool_call_failed" || event.kind === "run_failed") {
    const toolName = toolNameFromEvent(event, toolEvent);
    const errorMessage =
      payloadString(event, "error_message") ??
      toolErrorMessage(event, toolEvent);
    return {
      id,
      kind: event.kind,
      label: event.kind === "run_failed" ? "Run failed" : "Tool failed",
      detail:
        event.kind === "tool_call_failed" && toolName !== null
          ? `${displayToolName(toolName)}${errorMessage === null ? "" : `: ${errorMessage}`}`
          : errorMessage,
      level: "error",
      facts:
        event.kind === "tool_call_failed"
          ? toolActivityFacts(event, toolEvent, true)
          : eventFacts(event),
    };
  }
  if (event.kind === "usage") {
    return {
      id: USAGE_ACTIVITY_ID,
      kind: event.kind,
      label: "Token usage",
      detail: usageDetail(event),
      level: "info",
      facts: usageFacts(event),
    };
  }
  if (event.kind === "run_completed") {
    return {
      id,
      kind: event.kind,
      label: "Run completed",
      detail: null,
      level: "success",
      facts: eventFacts(event),
    };
  }
  if (event.kind === "run_cancelled") {
    return {
      id,
      kind: event.kind,
      label: "Run cancelled",
      detail: null,
      level: "info",
      facts: eventFacts(event),
    };
  }
  return {
    id,
    kind: event.kind,
    label: event.kind.replaceAll("_", " "),
    detail: null,
    level: "info",
    facts: eventFacts(event),
  };
}

function payloadString(event: ChatStreamEvent, key: string): string | null {
  const value = event.payload[key];
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function chatActivityFromEvent(
  event: ChatStreamEvent,
): ChatActivityPresentation | null {
  const value = event.payload.activity;
  if (!isUnknownRecord(value)) {
    return null;
  }
  const label = value.label;
  if (typeof label !== "string" || label.trim() === "") {
    return null;
  }
  const detail = value.detail;
  const rawIcon = value.icon;
  const rawLogo = value.logo;
  const logo = isUnknownRecord(rawLogo)
    ? brandLogoFromRecord(rawLogo)
    : null;
  return {
    label: label.trim(),
    detail:
      typeof detail === "string" && detail.trim() !== ""
        ? detail.trim()
        : null,
    icon: rawIcon === "mcp" || rawIcon === "web" ? rawIcon : null,
    logo,
  };
}

function brandLogoFromRecord(value: Record<string, unknown>): BrandLogo | null {
  return typeof value.domain === "string" &&
    typeof value.label === "string" &&
    value.source === "domain"
    ? {
        domain: value.domain,
        label: value.label,
        source: "domain",
      }
    : null;
}

function thinkingActivity(): ChatActivityPresentation {
  return { label: "Thinking…", detail: null, icon: null, logo: null };
}

function fallbackToolActivity(event: ChatStreamEvent): ChatActivityPresentation {
  return {
    label: "Using a tool",
    detail: displayToolName(payloadString(event, "name")),
    icon: null,
    logo: null,
  };
}

function payloadBoolean(event: ChatStreamEvent, key: string): boolean | null {
  const value = event.payload[key];
  return typeof value === "boolean" ? value : null;
}

function payloadNumber(event: ChatStreamEvent, key: string): number | null {
  const value = event.payload[key];
  return typeof value === "number" ? value : null;
}

function usageDetail(event: ChatStreamEvent): string | null {
  const totalTokens = payloadNumber(event, "total_tokens");
  return totalTokens === null
    ? null
    : `${totalTokens.toLocaleString()} tokens`;
}

function usageFacts(event: ChatStreamEvent): ActivityFact[] {
  const facts: ActivityFact[] = [];
  appendNumberFact(
    facts,
    "Input tokens",
    payloadNumber(event, "input_tokens") ??
      payloadNumber(event, "prompt_tokens"),
  );
  const inputDetails = event.payload.input_tokens_details;
  if (isUnknownRecord(inputDetails)) {
    appendNumberFact(
      facts,
      "Cached tokens",
      numberFromRecord(inputDetails, "cached_tokens"),
    );
  }
  appendNumberFact(
    facts,
    "Output tokens",
    payloadNumber(event, "output_tokens") ??
      payloadNumber(event, "completion_tokens"),
  );
  const outputDetails = event.payload.output_tokens_details;
  if (isUnknownRecord(outputDetails)) {
    appendNumberFact(
      facts,
      "Reasoning tokens",
      numberFromRecord(outputDetails, "reasoning_tokens"),
    );
  }
  appendNumberFact(
    facts,
    "Total tokens",
    payloadNumber(event, "total_tokens"),
  );
  appendTimestampFact(facts, event.created_at);
  return facts;
}

function toolActivityFacts(
  event: ChatStreamEvent,
  toolEvent: ChatToolEvent | null,
  isError = false,
): ActivityFact[] {
  const facts: ActivityFact[] = [];
  const toolName = toolNameFromEvent(event, toolEvent);
  if (toolName !== null) {
    facts.push({ label: "Tool", value: displayToolName(toolName) ?? toolName });
    if (displayToolName(toolName) !== toolName) {
      facts.push({ code: true, label: "Runtime name", value: toolName });
    }
  }
  const callId = payloadString(event, "call_id") ?? toolEvent?.call_id ?? null;
  if (callId !== null) {
    facts.push({ code: true, label: "Call ID", value: callId });
  }
  if (toolEvent !== null) {
    facts.push({
      code: true,
      label: "Arguments",
      value: formatActivityValue(toolEvent.arguments_json),
    });
  }
  const output =
    payloadString(event, "output") ?? toolEvent?.output_text ?? null;
  const errorMessage = toolErrorMessage(event, toolEvent);
  if (isError && errorMessage !== null) {
    facts.push({ label: "Error", value: errorMessage });
  } else if (output !== null) {
    facts.push({
      code: true,
      label: "Result preview",
      value: formatActivityValue(output),
    });
  }
  appendTimestampFact(facts, event.created_at ?? toolEvent?.created_at);
  return facts;
}

function eventFacts(event: ChatStreamEvent): ActivityFact[] {
  const facts: ActivityFact[] = [];
  const provider = payloadString(event, "provider");
  const model = payloadString(event, "model");
  if (provider !== null) {
    facts.push({ label: "Provider", value: provider });
  }
  if (model !== null) {
    facts.push({ label: "Model", value: model });
  }
  appendTimestampFact(facts, event.created_at);
  return facts;
}

function toolNameFromEvent(
  event: ChatStreamEvent,
  toolEvent: ChatToolEvent | null,
): string | null {
  return payloadString(event, "name") ?? toolEvent?.name ?? null;
}

function displayToolName(toolName: string | null): string | null {
  if (toolName === null) {
    return null;
  }
  return toolName.replace(/^mcp_[a-f0-9]{10}_/i, "");
}

function toolErrorMessage(
  event: ChatStreamEvent,
  toolEvent: ChatToolEvent | null,
): string | null {
  const explicitMessage = payloadString(event, "error_message");
  if (explicitMessage !== null) {
    return truncateActivityValue(explicitMessage);
  }
  const output =
    payloadString(event, "output") ?? toolEvent?.output_text ?? null;
  if (output === null) {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(output);
    if (isUnknownRecord(parsed)) {
      const error = parsed.error;
      if (typeof error === "string" && error.trim() !== "") {
        return truncateActivityValue(error.trim());
      }
    }
  } catch {
    // The tool output is plain text rather than JSON.
  }
  return truncateActivityValue(output.trim());
}

function formatActivityValue(value: unknown): string {
  if (typeof value === "string") {
    try {
      return truncateActivityValue(JSON.stringify(JSON.parse(value), null, 2));
    } catch {
      return truncateActivityValue(value);
    }
  }
  const serialized = JSON.stringify(value, null, 2);
  return truncateActivityValue(
    serialized === undefined ? String(value) : serialized,
  );
}

function truncateActivityValue(value: string): string {
  const limit = 1200;
  return value.length <= limit ? value : `${value.slice(0, limit - 1)}…`;
}

function appendNumberFact(
  facts: ActivityFact[],
  label: string,
  value: number | null,
): void {
  if (value !== null) {
    facts.push({ label, value: value.toLocaleString() });
  }
}

function appendTimestampFact(
  facts: ActivityFact[],
  value: string | undefined,
): void {
  if (value === undefined) {
    return;
  }
  const timestamp = new Date(value);
  facts.push({
    label: "Time",
    value: Number.isNaN(timestamp.getTime())
      ? value
      : timestamp.toLocaleString(undefined, {
          dateStyle: "medium",
          timeStyle: "medium",
        }),
  });
}

function isUnknownRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function numberFromRecord(
  value: Record<string, unknown>,
  key: string,
): number | null {
  const candidate = value[key];
  return typeof candidate === "number" ? candidate : null;
}

function streamingActivity(): ActivityEntry {
  return {
    id: STREAMING_ACTIVITY_ID,
    kind: "message_delta",
    label: "Streaming response",
    detail: "Receiving response",
    level: "info",
    facts: [],
  };
}

function upsertStreamingActivity(current: ActivityEntry[]): ActivityEntry[] {
  if (current.some((entry) => entry.id === STREAMING_ACTIVITY_ID)) {
    return current;
  }
  return prependActivity(current, streamingActivity(), false);
}

function prependActivity(
  current: ActivityEntry[],
  activity: ActivityEntry,
  removeStreaming: boolean,
): ActivityEntry[] {
  const filtered = current.filter(
    (entry) =>
      entry.id !== activity.id &&
      (!removeStreaming || entry.id !== STREAMING_ACTIVITY_ID),
  );
  return [activity, ...filtered].slice(0, 32);
}

function resizeComposerTextarea(textarea: HTMLTextAreaElement | null): void {
  if (textarea === null) {
    return;
  }
  textarea.style.height = "auto";
  textarea.style.height = `${textarea.scrollHeight}px`;
}
