import {
  Browsers,
  DisconnectReason,
  fetchLatestWaWebVersion,
  getContentType,
  isHostedLidUser,
  isHostedPnUser,
  isJidBroadcast,
  isJidGroup,
  isJidNewsletter,
  isJidStatusBroadcast,
  isLidUser,
  isPnUser,
  jidNormalizedUser,
  makeWASocket,
  normalizeMessageContent,
  type AuthenticationState,
  type AuthenticationCreds,
  type BaileysEventMap,
  type ConnectionState,
  type Contact,
  type GroupMetadata,
  type LIDMapping,
  type WAMessage,
  type WAVersion,
  type WASocket,
} from "baileys";
import pino from "pino";

import {
  BAILEYS_VERSION,
  BRIDGE_SESSION_HANDLE,
  BRIDGE_VERSION,
  BridgeError,
  type CommandFrame,
  MAX_FRAME_BYTES,
  MAX_TEXT_BYTES,
  PROTOCOL_VERSION,
  errorResponse,
  expectRecord,
  readBoolean,
  readInteger,
  readString,
  successResponse,
  type ResponseFrame,
} from "./protocol.js";
import { RemoteAuthStore } from "./remote-auth-store.js";
import type { JsonLineWriter } from "./protocol.js";
import {
  DurableEventQueue,
  type BridgeRpcPeer,
} from "./rpc-peer.js";

const MAX_SESSIONS = 256;
const MAX_RESPONSE_CACHE = 2048;
const MAX_LIFECYCLE_TOMBSTONES = 2048;
const MAX_SOCKET_EVENT_QUEUE_ITEMS = 64;
const QR_TTL_MS = 18_000;
const PAIRING_CODE_TTL_MS = 120_000;
const DEFAULT_SEND_TIMEOUT_MS = 20_000;
const MAX_SEND_TIMEOUT_MS = 60_000;
const MIN_SEND_TIMEOUT_MS = 1_000;
const MAX_RECONNECT_ATTEMPTS = 8;
const RECONNECT_DELAYS_MS = [500, 1_000, 2_000, 5_000, 10_000, 20_000, 30_000, 30_000];
const CLOSE_TIMEOUT_MS = 5_000;
const PAIRING_TIMEOUT_MS = 20_000;
const MAX_IDENTITY_TEXT_BYTES = 256;
const MAX_CONTACT_IDENTITIES = 4_096;
const MAX_GROUP_METADATA = 128;
const WA_WEB_VERSION_CACHE_TTL_MS = 60 * 60 * 1_000;
const PHONE_NUMBER_PATTERN = /^[1-9][0-9]{7,14}$/;
const ONE_TO_ONE_JID_PATTERN =
  /^(?:[1-9][0-9]{5,19})(?::[0-9]{1,3})?@(?:s\.whatsapp\.net|lid|hosted|hosted\.lid)$/;
const GROUP_JID_PATTERN = /^(?:[1-9][0-9]{5,19})(?:-[0-9]{1,20})?@g\.us$/;

export type SessionState =
  | "awaiting_link"
  | "connected"
  | "connecting"
  | "failed"
  | "needs_relink"
  | "reconnecting"
  | "stopped";

export type DisconnectClassification =
  | "needs_relink"
  | "restart_required"
  | "transient_reconnect";

export interface DisconnectDisposition {
  readonly classification: DisconnectClassification;
  readonly failureCode:
    | "bad_session"
    | "connection_closed"
    | "connection_lost"
    | "connection_replaced"
    | "forbidden"
    | "logged_out"
    | "multidevice_mismatch"
    | "restart_required"
    | "timed_out"
    | "unavailable_service"
    | "unknown_disconnect";
}

export interface SessionSnapshot {
  readonly state: SessionState;
  readonly registered: boolean;
  readonly linked: boolean;
  readonly reconnect_attempts: number;
}

export interface ReportedIdentity {
  readonly pn_jid: string | null;
  readonly lid_jid: string | null;
  readonly proof: "lid_only" | "pn" | "pn_and_lid" | "unresolved";
}

export interface InboundText {
  readonly provider_message_id: string;
  readonly chat_jid: string;
  readonly chat_kind: "direct" | "group";
  readonly sender_jid: string;
  readonly sender_jid_alt: string | null;
  readonly sender_username: string | null;
  readonly sender_pn_jid: string | null;
  readonly sender_lid_jid: string | null;
  readonly addressing_mode: "lid" | "pn" | null;
  readonly identity_proof: ReportedIdentity["proof"];
  readonly push_name: string | null;
  readonly body: string;
  readonly timestamp: string | null;
}

export type SocketFactory = (
  auth: AuthenticationState,
) => WASocket | Promise<WASocket>;
export type Delay = (milliseconds: number, signal: AbortSignal) => Promise<void>;
type LifecycleMode = "logout" | "stop";
type ChallengeMethod = "pairing_code" | "qr";

interface CachedWaWebVersion {
  readonly expiresAt: number;
  readonly version: WAVersion;
}

let cachedWaWebVersion: CachedWaWebVersion | null = null;
let waWebVersionRefresh: Promise<WAVersion> | null = null;

interface LifecycleTombstone {
  readonly mode: LifecycleMode;
  readonly error: BridgeError | null;
}

interface ChallengeRequestContext {
  readonly method: ChallengeMethod;
  readonly requestRevision: number;
}

export class SessionManager {
  private readonly sessions = new Map<string, SessionController>();
  private readonly sessionQueues = new Map<string, Promise<void>>();
  private readonly inFlightResponses = new Map<string, Promise<ResponseFrame>>();
  private readonly responseCache = new Map<string, ResponseFrame>();
  private readonly lifecycleTombstones = new Map<string, LifecycleTombstone>();
  private acceptingCommands = true;

  constructor(
    private readonly writer: JsonLineWriter,
    private readonly rpc: BridgeRpcPeer,
    private readonly socketFactory: SocketFactory = createBaileysSocket,
    private readonly delay: Delay = abortableDelay,
  ) {}

  async handleCommand(command: CommandFrame): Promise<void> {
    const cached = this.responseCache.get(command.request_id);
    if (cached !== undefined) {
      await this.writer.write(cached);
      return;
    }
    const inFlight = this.inFlightResponses.get(command.request_id);
    if (inFlight !== undefined) {
      await this.writer.write(await inFlight);
      return;
    }

    const responsePromise = this.executeSafely(command);
    this.inFlightResponses.set(command.request_id, responsePromise);
    const response = await responsePromise;
    this.inFlightResponses.delete(command.request_id);
    this.rememberResponse(command.request_id, response);
    await this.writer.write(response);
  }

  async abort(): Promise<void> {
    this.acceptingCommands = false;
    await Promise.allSettled(
      [...this.sessions.values()].map((controller) => controller.stop()),
    );
    this.rpc.cancelAll();
    this.sessions.clear();
  }

  private async executeSafely(command: CommandFrame): Promise<ResponseFrame> {
    try {
      const payload = await this.execute(command);
      return successResponse(command, payload);
    } catch (error) {
      return errorResponse(command, error);
    }
  }

  private async execute(command: CommandFrame): Promise<Record<string, unknown>> {
    if (!this.acceptingCommands && command.command !== "rpc_result") {
      throw new BridgeError("bridge_stopping");
    }
    if (command.command === "rpc_result") {
      this.rpc.resolveResult(command);
      return {};
    }
    if (command.command === "handshake") {
      assertBridgeCommand(command);
      return {
        protocol_version: PROTOCOL_VERSION,
        bridge_version: BRIDGE_VERSION,
        baileys_version: BAILEYS_VERSION,
        max_frame_bytes: MAX_FRAME_BYTES,
        max_sessions: MAX_SESSIONS,
        rpc_timeout_ms: this.rpc.requestTimeoutMs,
        capabilities: [
          "challenge_revisions",
          "durable_events",
          "linked_state",
          "logout_if_linked",
          "multi_session",
          "ordered_durable_events",
          "remote_auth",
          "qr",
          "pairing_code",
          "one_to_one_text",
          "pn_lid_identity",
        ],
      };
    }
    if (command.command === "shutdown") {
      assertBridgeCommand(command);
      this.acceptingCommands = false;
      await Promise.all([...this.sessions.values()].map((controller) => controller.stop()));
      this.sessions.clear();
      this.rpc.cancelAll();
      return { state: "stopped" };
    }
    return this.enqueueSessionCommand(command.session_handle, () =>
      this.executeSessionCommand(command),
    );
  }

  private async executeSessionCommand(
    command: CommandFrame,
  ): Promise<Record<string, unknown>> {
    switch (command.command) {
      case "start_session":
        return this.startSession(command);
      case "stop_session":
        return this.stopSession(command, false);
      case "logout_if_linked":
        return this.stopSession(command, true);
      case "request_challenge":
        return this.requireSession(command).requestChallenge(command.payload);
      case "send_text":
        return this.requireSession(command).sendText(command.payload);
      case "session_status":
        return { ...this.requireSession(command).snapshot() };
      case "handshake":
      case "rpc_result":
      case "shutdown":
        throw new BridgeError("invalid_command_scope");
    }
  }

  private async startSession(command: CommandFrame): Promise<Record<string, unknown>> {
    const existing = this.sessions.get(command.session_handle);
    if (existing !== undefined) {
      if (command.generation < existing.generation) {
        throw new BridgeError("stale_generation");
      }
      if (command.generation === existing.generation) {
        return { ...existing.snapshot(), resumed: true };
      }
      await existing.stop();
      const existingMode = existing.lifecycleMode() ?? "stop";
      this.rememberLifecycleTombstone(
        existing.sessionHandle,
        existing.generation,
        { mode: existingMode, error: null },
      );
      this.rpc.cancelSession(existing.sessionHandle, existing.generation);
      this.sessions.delete(command.session_handle);
    }
    if (this.sessions.size >= MAX_SESSIONS) {
      throw new BridgeError("session_capacity_exceeded", { retryable: true });
    }

    const auth = await RemoteAuthStore.create(
      this.rpc,
      command.session_handle,
      command.generation,
      command.payload.credentials,
    );
    const controller = new SessionController({
      sessionHandle: command.session_handle,
      generation: command.generation,
      auth,
      rpc: this.rpc,
      socketFactory: this.socketFactory,
      delay: this.delay,
    });
    this.sessions.set(command.session_handle, controller);
    try {
      await controller.start();
    } catch (error) {
      await controller.stop().catch(() => undefined);
      this.rpc.cancelSession(command.session_handle, command.generation);
      this.sessions.delete(command.session_handle);
      throw error;
    }
    this.lifecycleTombstones.delete(
      lifecycleKey(command.session_handle, command.generation),
    );
    return { ...controller.snapshot(), resumed: false };
  }

  private async stopSession(
    command: CommandFrame,
    logout: boolean,
  ): Promise<Record<string, unknown>> {
    const requestedMode: LifecycleMode = logout ? "logout" : "stop";
    const controller = this.sessions.get(command.session_handle);
    if (controller === undefined) {
      return this.replayLifecycleTombstone(command, requestedMode);
    }
    if (controller.generation !== command.generation) {
      throw new BridgeError(
        command.generation < controller.generation
          ? "stale_generation"
          : "generation_not_owned",
      );
    }
    let closureError: BridgeError | null = null;
    try {
      if (logout) {
        await controller.logoutIfLinked();
      } else {
        await controller.stop();
      }
    } catch (error) {
      if (controller.snapshot().state !== "stopped") {
        throw error;
      }
      closureError = normalizeLifecycleError(error);
    }
    const actualMode = controller.lifecycleMode() ?? requestedMode;
    this.rpc.cancelSession(command.session_handle, command.generation);
    if (this.sessions.get(command.session_handle) === controller) {
      this.sessions.delete(command.session_handle);
    }
    this.rememberLifecycleTombstone(
      command.session_handle,
      command.generation,
      {
        mode: actualMode,
        error: actualMode === requestedMode ? closureError : null,
      },
    );
    if (closureError !== null) {
      throw closureError;
    }
    return { state: "stopped" };
  }

  private replayLifecycleTombstone(
    command: CommandFrame,
    requestedMode: LifecycleMode,
  ): Record<string, unknown> {
    const tombstone = this.lifecycleTombstones.get(
      lifecycleKey(command.session_handle, command.generation),
    );
    if (tombstone === undefined) {
      throw new BridgeError("session_not_found");
    }
    if (requestedMode === "logout" && tombstone.mode !== "logout") {
      throw new BridgeError("logout_uncertain", { retryable: true });
    }
    if (requestedMode === tombstone.mode && tombstone.error !== null) {
      throw tombstone.error;
    }
    return { state: "stopped" };
  }

  private requireSession(command: CommandFrame): SessionController {
    const controller = this.sessions.get(command.session_handle);
    if (controller === undefined) {
      throw new BridgeError("session_not_found");
    }
    if (controller.generation !== command.generation) {
      throw new BridgeError(
        command.generation < controller.generation
          ? "stale_generation"
          : "generation_not_owned",
      );
    }
    return controller;
  }

  private enqueueSessionCommand<T>(
    sessionHandle: string,
    operation: () => Promise<T>,
  ): Promise<T> {
    const previous = this.sessionQueues.get(sessionHandle) ?? Promise.resolve();
    const result = previous.then(operation, operation);
    const settled = result.then(
      () => undefined,
      () => undefined,
    );
    this.sessionQueues.set(sessionHandle, settled);
    void settled.finally(() => {
      if (this.sessionQueues.get(sessionHandle) === settled) {
        this.sessionQueues.delete(sessionHandle);
      }
    });
    return result;
  }

  private rememberResponse(requestId: string, response: ResponseFrame): void {
    this.responseCache.set(requestId, response);
    if (this.responseCache.size <= MAX_RESPONSE_CACHE) {
      return;
    }
    const oldest = this.responseCache.keys().next().value;
    if (oldest !== undefined) {
      this.responseCache.delete(oldest);
    }
  }

  private rememberLifecycleTombstone(
    sessionHandle: string,
    generation: number,
    tombstone: LifecycleTombstone,
  ): void {
    const key = lifecycleKey(sessionHandle, generation);
    this.lifecycleTombstones.delete(key);
    this.lifecycleTombstones.set(key, tombstone);
    if (this.lifecycleTombstones.size <= MAX_LIFECYCLE_TOMBSTONES) {
      return;
    }
    const oldest = this.lifecycleTombstones.keys().next().value;
    if (oldest !== undefined) {
      this.lifecycleTombstones.delete(oldest);
    }
  }
}

interface SessionControllerOptions {
  readonly sessionHandle: string;
  readonly generation: number;
  readonly auth: RemoteAuthStore;
  readonly rpc: BridgeRpcPeer;
  readonly socketFactory: SocketFactory;
  readonly delay: Delay;
}

interface SocketListeners {
  readonly socket: WASocket;
  readonly epoch: number;
  readonly onCredentialsUpdate: (update: Partial<AuthenticationCreds>) => void;
  readonly onConnectionUpdate: (update: Partial<ConnectionState>) => void;
  readonly onContactsUpsert: (contacts: BaileysEventMap["contacts.upsert"]) => void;
  readonly onContactsUpdate: (contacts: BaileysEventMap["contacts.update"]) => void;
  readonly onGroupsUpsert: (groups: BaileysEventMap["groups.upsert"]) => void;
  readonly onGroupsUpdate: (groups: BaileysEventMap["groups.update"]) => void;
  readonly onGroupParticipantsUpdate: (
    update: BaileysEventMap["group-participants.update"],
  ) => void;
  readonly onHistorySet: (history: BaileysEventMap["messaging-history.set"]) => void;
  readonly onLidMappingUpdate: (mapping: BaileysEventMap["lid-mapping.update"]) => void;
  readonly onMessagesUpsert: (event: MessagesUpsertEvent) => void;
}

interface MessagesUpsertEvent {
  readonly messages: WAMessage[];
  readonly type: "append" | "notify";
  readonly requestId?: string;
}

class WhatsAppIdentityDirectory {
  private readonly contactsByJid = new Map<string, Contact>();
  private readonly groupsByJid = new Map<string, GroupMetadata>();
  private readonly pnByLid = new Map<string, string>();

  upsertContacts(contacts: readonly Partial<Contact>[]): void {
    for (const update of contacts) {
      const updateJids = contactJids(update);
      if (updateJids.length === 0) {
        continue;
      }
      const existing = updateJids
        .map((jid) => this.contactsByJid.get(jid))
        .find((contact) => contact !== undefined);
      const id = update.id ?? existing?.id;
      if (id === undefined) {
        continue;
      }
      const contact: Contact = {
        ...existing,
        ...update,
        id,
      };
      for (const jid of contactJids(contact)) {
        rememberBounded(this.contactsByJid, jid, contact, MAX_CONTACT_IDENTITIES);
      }
      const lid = normalizedLidJid(contact.lid);
      const pn = normalizedPnJid(contact.phoneNumber) ?? normalizedPnJid(contact.id);
      if (lid !== null && pn !== null) {
        this.rememberMapping({ lid, pn });
      }
    }
  }

  upsertGroups(groups: readonly Partial<GroupMetadata>[]): void {
    for (const update of groups) {
      const groupJid = normalizedGroupJid(update.id);
      if (groupJid === null) {
        continue;
      }
      const existing = this.groupsByJid.get(groupJid);
      const group = {
        ...existing,
        ...update,
        id: groupJid,
        owner: update.owner ?? existing?.owner,
        participants: update.participants ?? existing?.participants ?? [],
        subject: update.subject ?? existing?.subject ?? "",
      } satisfies GroupMetadata;
      rememberBounded(this.groupsByJid, groupJid, group, MAX_GROUP_METADATA);
      this.upsertContacts(group.participants);
    }
  }

  rememberMappings(mappings: readonly LIDMapping[]): void {
    for (const mapping of mappings) {
      this.rememberMapping(mapping);
    }
  }

  contactPnForLid(lidJid: string): string | null {
    const contact = this.contactsByJid.get(lidJid);
    return contact === undefined ? null : pnJidFromContact(contact);
  }

  async groupMetadata(
    socket: WASocket,
    groupJid: string,
  ): Promise<GroupMetadata | null> {
    const cached = this.groupsByJid.get(groupJid);
    if (cached !== undefined) {
      return cached;
    }
    try {
      const metadata = await socket.groupMetadata(groupJid);
      this.upsertGroups([metadata]);
      return metadata;
    } catch {
      // Identity enrichment is best effort; the inbound message remains valid
      // even when WhatsApp cannot return current group metadata.
      return null;
    }
  }

  groupParticipantPn(metadata: GroupMetadata, lidJid: string): string | null {
    const participant = metadata.participants.find((candidate) =>
      contactJids(candidate).includes(lidJid),
    );
    return participant === undefined ? null : pnJidFromContact(participant);
  }

  mappedPnForLid(lidJid: string): string | null {
    return this.pnByLid.get(lidJid) ?? null;
  }

  private rememberMapping(mapping: LIDMapping): void {
    const lid = normalizedLidJid(mapping.lid);
    const pn = normalizedPnJid(mapping.pn);
    if (lid === null || pn === null) {
      return;
    }
    rememberBounded(this.pnByLid, lid, pn, MAX_CONTACT_IDENTITIES);
  }
}

class SessionController {
  readonly sessionHandle: string;
  readonly generation: number;

  private readonly auth: RemoteAuthStore;
  private readonly rpc: BridgeRpcPeer;
  private readonly socketFactory: SocketFactory;
  private readonly delay: Delay;
  private readonly abortController = new AbortController();
  private readonly backgroundTasks = new Set<Promise<void>>();
  private readonly durableEvents: DurableEventQueue;
  private readonly identityDirectory = new WhatsAppIdentityDirectory();
  private readonly socketEventQueue: (() => Promise<void>)[] = [];
  private readonly deferredMessageUpserts: MessagesUpsertEvent[] = [];
  private socketEventWorker: Promise<void> | null = null;
  private socket: WASocket | null = null;
  private socketListeners: SocketListeners | null = null;
  private socketEpoch = 0;
  private connectionInvalidationRevision = 0;
  private connectedStatusAcknowledged = false;
  private state: SessionState = "connecting";
  private reconnectAttempts = 0;
  private latestQr: { readonly value: string; readonly expiresAt: number } | null = null;
  private challengeRequest: ChallengeRequestContext | null = null;
  private closePromise: Promise<void> | null = null;
  private closeMode: "logout" | "stop" | null = null;
  private stopping = false;
  private terminal = false;

  constructor(options: SessionControllerOptions) {
    this.sessionHandle = options.sessionHandle;
    this.generation = options.generation;
    this.auth = options.auth;
    this.rpc = options.rpc;
    this.socketFactory = options.socketFactory;
    this.delay = options.delay;
    this.durableEvents = new DurableEventQueue(
      this.rpc,
      this.sessionHandle,
      this.generation,
      this.abortController.signal,
    );
  }

  async start(): Promise<void> {
    await this.replaceSocket();
  }

  snapshot(): SessionSnapshot {
    return {
      state: this.state,
      registered: this.auth.state.creds.registered,
      linked: isLinked(this.auth.state.creds),
      reconnect_attempts: this.reconnectAttempts,
    };
  }

  async requestChallenge(
    payload: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    this.assertOperational();
    const method = readChallengeMethod(payload.method);
    const requestRevision = readInteger(
      payload.request_revision,
      "invalid_challenge_request_revision",
      { min: 1 },
    );
    if (isLinked(this.auth.state.creds)) {
      throw new BridgeError("already_linked");
    }
    const context = { method, requestRevision };
    this.challengeRequest = context;
    if (method === "pairing_code") {
      const phoneNumber = readString(
        payload.phone_number,
        "invalid_phone_number",
        {
          maxLength: 15,
          pattern: PHONE_NUMBER_PATTERN,
        },
      );
      const socket = this.requireSocket();
      let code: string;
      try {
        code = await withTimeout(
          socket.requestPairingCode(phoneNumber),
          PAIRING_TIMEOUT_MS,
          "pairing_code_timeout",
        );
      } catch (error) {
        if (error instanceof BridgeError) {
          throw error;
        }
        throw new BridgeError("pairing_code_failed", { retryable: true });
      }
      await this.auth.flush();
      const expiresAt = Date.now() + PAIRING_CODE_TTL_MS;
      await this.emitLinkChallenge(context, "pairing_code", code, expiresAt);
      return {
        status: "ready",
        method: "pairing_code",
        requested_method: context.method,
        request_revision: context.requestRevision,
        value: code,
        expires_at: expiresAt,
      };
    }

    const regenerate =
      payload.regenerate === undefined
        ? false
        : readBoolean(payload.regenerate, "invalid_regenerate_flag");
    const current = this.latestQr;
    if (!regenerate && current !== null && current.expiresAt > Date.now()) {
      await this.emitLinkChallenge(
        context,
        "qr",
        current.value,
        current.expiresAt,
      );
      return {
        status: "ready",
        method: "qr",
        requested_method: context.method,
        request_revision: context.requestRevision,
        value: current.value,
        expires_at: current.expiresAt,
      };
    }
    if (regenerate) {
      await this.replaceSocket();
    }
    return {
      status: "pending",
      method: "qr",
      requested_method: context.method,
      request_revision: context.requestRevision,
    };
  }

  async sendText(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    this.assertOperational();
    if (this.state !== "connected") {
      throw new BridgeError("session_not_connected", { retryable: true });
    }
    const target = readString(payload.to, "invalid_recipient", {
      maxLength: 128,
    });
    if (!isWhatsAppChatJid(target)) {
      throw new BridgeError("invalid_recipient");
    }
    const text = readString(payload.text, "invalid_message_text", {
      maxLength: MAX_TEXT_BYTES,
    });
    if (Buffer.byteLength(text, "utf8") > MAX_TEXT_BYTES) {
      throw new BridgeError("message_too_large");
    }
    const timeoutMs =
      payload.timeout_ms === undefined
        ? DEFAULT_SEND_TIMEOUT_MS
        : readInteger(payload.timeout_ms, "invalid_send_timeout", {
            min: MIN_SEND_TIMEOUT_MS,
            max: MAX_SEND_TIMEOUT_MS,
          });

    await this.auth.flush();
    const socket = this.requireSocket();
    const submission = socket.sendMessage(jidNormalizedUser(target), { text });
    let result: WAMessage | undefined;
    try {
      result = await withTimeout(submission, timeoutMs, "send_ambiguous");
    } catch {
      void submission.catch(() => undefined);
      throw new BridgeError("send_ambiguous", { ambiguous: true });
    }
    const externalMessageId = result?.key.id;
    if (typeof externalMessageId !== "string" || externalMessageId.length === 0) {
      throw new BridgeError("send_ambiguous", { ambiguous: true });
    }
    return {
      provider_message_id: externalMessageId,
    };
  }

  stop(): Promise<void> {
    if (this.closePromise === null) {
      this.closeMode = "stop";
      this.closePromise = this.performClose(false);
    }
    return this.closePromise;
  }

  logoutIfLinked(): Promise<void> {
    if (this.closePromise === null) {
      this.closeMode = "logout";
      this.closePromise = this.performClose(isLinked(this.auth.state.creds));
    } else if (this.closeMode !== "logout") {
      return this.closePromise.then(
        () => {
          throw new BridgeError("logout_uncertain", { retryable: true });
        },
        (error: unknown) => {
          throw error instanceof Error
            ? error
            : new BridgeError("session_close_failed", { retryable: true });
        },
      );
    }
    return this.closePromise;
  }

  lifecycleMode(): LifecycleMode | null {
    return this.closeMode;
  }

  private async performClose(logout: boolean): Promise<void> {
    this.stopping = true;
    this.terminal = true;
    this.abortController.abort();
    const socket = this.socket;
    this.detachCurrentSocket();
    let logoutUncertain = logout && socket === null;
    if (socket !== null) {
      if (logout) {
        try {
          await withTimeout(socket.logout(), CLOSE_TIMEOUT_MS, "logout_timeout");
        } catch {
          await closeSocket(socket);
          logoutUncertain = true;
        }
      } else {
        await closeSocket(socket);
      }
    }
    this.socketEventQueue.length = 0;
    this.deferredMessageUpserts.length = 0;
    this.durableEvents.stop();
    if (this.socketEventWorker !== null) {
      await this.socketEventWorker;
    }
    await this.durableEvents.flush();
    await Promise.allSettled(this.backgroundTasks);
    await this.auth.flush();
    this.state = "stopped";
    if (logoutUncertain) {
      throw new BridgeError("logout_uncertain", { retryable: true });
    }
  }

  private async replaceSocket(): Promise<void> {
    this.assertOperational();
    await this.closeCurrentSocket();
    this.latestQr = null;
    this.connectedStatusAcknowledged = false;
    this.state = "connecting";
    const socket = await this.socketFactory(this.auth.state);
    this.socket = socket;
    const epoch = ++this.socketEpoch;

    const onCredentialsUpdate = (
      update: Partial<AuthenticationCreds>,
    ): void => {
      Object.assign(this.auth.state.creds, update);
      this.runBackground(async () => {
        await this.auth.persistCredentials();
      });
    };
    const onConnectionUpdate = (update: Partial<ConnectionState>): void => {
      const invalidationRevision = this.connectionInvalidationRevision;
      if (update.connection === "close") {
        this.connectionInvalidationRevision += 1;
        this.connectedStatusAcknowledged = false;
      }
      this.enqueueSocketEvent(() =>
        this.handleConnectionUpdate(
          socket,
          epoch,
          invalidationRevision,
          update,
        ),
      );
    };
    const onMessagesUpsert = (event: MessagesUpsertEvent): void => {
      if (!this.connectedStatusAcknowledged) {
        this.deferMessageUpsert(event);
        return;
      }
      this.enqueueMessageUpsert(socket, epoch, event);
    };
    const onContactsUpsert = (
      contacts: BaileysEventMap["contacts.upsert"],
    ): void => {
      this.identityDirectory.upsertContacts(contacts);
    };
    const onContactsUpdate = (
      contacts: BaileysEventMap["contacts.update"],
    ): void => {
      this.identityDirectory.upsertContacts(contacts);
    };
    const onGroupsUpsert = (
      groups: BaileysEventMap["groups.upsert"],
    ): void => {
      this.identityDirectory.upsertGroups(groups);
    };
    const onGroupsUpdate = (
      groups: BaileysEventMap["groups.update"],
    ): void => {
      this.identityDirectory.upsertGroups(groups);
    };
    const onGroupParticipantsUpdate = (
      update: BaileysEventMap["group-participants.update"],
    ): void => {
      this.identityDirectory.upsertContacts(update.participants);
    };
    const onHistorySet = (
      history: BaileysEventMap["messaging-history.set"],
    ): void => {
      this.identityDirectory.upsertContacts(history.contacts);
      this.identityDirectory.rememberMappings(history.lidPnMappings ?? []);
    };
    const onLidMappingUpdate = (
      mapping: BaileysEventMap["lid-mapping.update"],
    ): void => {
      this.identityDirectory.rememberMappings([mapping]);
    };
    this.socketListeners = {
      socket,
      epoch,
      onCredentialsUpdate,
      onConnectionUpdate,
      onContactsUpsert,
      onContactsUpdate,
      onGroupsUpsert,
      onGroupsUpdate,
      onGroupParticipantsUpdate,
      onHistorySet,
      onLidMappingUpdate,
      onMessagesUpsert,
    };
    socket.ev.on("creds.update", onCredentialsUpdate);
    socket.ev.on("connection.update", onConnectionUpdate);
    socket.ev.on("contacts.upsert", onContactsUpsert);
    socket.ev.on("contacts.update", onContactsUpdate);
    socket.ev.on("groups.upsert", onGroupsUpsert);
    socket.ev.on("groups.update", onGroupsUpdate);
    socket.ev.on("group-participants.update", onGroupParticipantsUpdate);
    socket.ev.on("messaging-history.set", onHistorySet);
    socket.ev.on("lid-mapping.update", onLidMappingUpdate);
    socket.ev.on("messages.upsert", onMessagesUpsert);
  }

  private async handleConnectionUpdate(
    socket: WASocket,
    epoch: number,
    invalidationRevision: number,
    update: Partial<ConnectionState>,
  ): Promise<void> {
    if (!this.isCurrent(socket, epoch)) {
      return;
    }
    if (typeof update.qr === "string") {
      if (Buffer.byteLength(update.qr, "utf8") > MAX_TEXT_BYTES) {
        await this.failTerminal("invalid_qr");
        return;
      }
      const expiresAt = Date.now() + QR_TTL_MS;
      this.latestQr = { value: update.qr, expiresAt };
      this.state = "awaiting_link";
      const challengeRequest = this.challengeRequest;
      if (challengeRequest?.method === "qr") {
        await this.emitLinkChallenge(
          challengeRequest,
          "qr",
          update.qr,
          expiresAt,
        );
      }
      if (!this.isCurrent(socket, epoch)) {
        return;
      }
    }
    if (update.connection === "connecting") {
      this.state = this.latestQr === null ? "connecting" : "awaiting_link";
      await this.emitStatus();
      return;
    }
    if (update.connection === "open") {
      await this.auth.flush();
      if (!this.isCurrent(socket, epoch)) {
        return;
      }
      const identity = await resolveSelfIdentity(socket, this.auth.state);
      if (!this.isCurrent(socket, epoch)) {
        return;
      }
      this.reconnectAttempts = 0;
      this.latestQr = null;
      this.challengeRequest = null;
      this.state = "connected";
      await this.emitStatus({ identity });
      if (
        !this.isCurrent(socket, epoch) ||
        invalidationRevision !== this.connectionInvalidationRevision
      ) {
        return;
      }
      this.connectedStatusAcknowledged = true;
      await this.flushDeferredMessageUpserts(socket, epoch);
      return;
    }
    if (update.connection !== "close") {
      return;
    }

    this.detachSocket(socket, epoch);
    const disposition = classifyDisconnect(update.lastDisconnect?.error);
    const { classification } = disposition;
    const statusDetails = {
      classification,
      failure_code: disposition.failureCode,
    };
    if (classification === "needs_relink") {
      this.terminal = true;
      this.deferredMessageUpserts.length = 0;
      this.state = "needs_relink";
      await this.emitStatus(statusDetails);
      return;
    }
    if (this.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      this.terminal = true;
      this.deferredMessageUpserts.length = 0;
      this.state = "failed";
      await this.emitStatus({
        classification: "reconnect_exhausted",
        failure_code: "reconnect_exhausted",
      });
      return;
    }

    this.state = "reconnecting";
    const attempt = this.reconnectAttempts;
    this.reconnectAttempts += 1;
    await this.emitStatus(statusDetails);
    await this.auth.flush();
    const delayMs =
      classification === "restart_required"
        ? 0
        : (RECONNECT_DELAYS_MS[attempt] ?? RECONNECT_DELAYS_MS.at(-1) ?? 30_000);
    await this.delay(delayMs, this.abortController.signal);
    if (!this.stopping && !this.terminal && this.socket === null) {
      await this.replaceSocket();
    }
  }

  private async handleMessagesUpsert(
    socket: WASocket,
    epoch: number,
    event: MessagesUpsertEvent,
  ): Promise<void> {
    if (!this.isCurrent(socket, epoch) || event.type !== "notify") {
      return;
    }
    if (!this.connectedStatusAcknowledged) {
      this.deferMessageUpsert(event);
      return;
    }
    await this.auth.flush();
    if (!this.isCurrent(socket, epoch)) {
      return;
    }
    for (const message of event.messages) {
      const inbound = await extractInboundText(
        socket,
        message,
        this.identityDirectory,
      );
      if (inbound === null) {
        continue;
      }
      if (!this.isCurrent(socket, epoch)) {
        return;
      }
      await this.emitDurableEvent("inbound_text", {
        ...inbound,
      });
    }
  }

  private enqueueMessageUpsert(
    socket: WASocket,
    epoch: number,
    event: MessagesUpsertEvent,
  ): void {
    if (event.type !== "notify") {
      return;
    }
    this.enqueueSocketEvent(() =>
      this.handleMessagesUpsert(socket, epoch, event),
    );
  }

  private deferMessageUpsert(event: MessagesUpsertEvent): void {
    if (
      event.type !== "notify" ||
      this.stopping ||
      this.terminal
    ) {
      return;
    }
    const depth =
      this.deferredMessageUpserts.length +
      this.socketEventQueue.length +
      (this.socketEventWorker === null ? 0 : 1);
    if (depth >= MAX_SOCKET_EVENT_QUEUE_ITEMS) {
      this.runBackground(() =>
        this.failTerminal("socket_event_capacity_exceeded"),
      );
      return;
    }
    this.deferredMessageUpserts.push(event);
  }

  private async flushDeferredMessageUpserts(
    socket: WASocket,
    epoch: number,
  ): Promise<void> {
    while (
      this.connectedStatusAcknowledged &&
      this.isCurrent(socket, epoch)
    ) {
      const event = this.deferredMessageUpserts.shift();
      if (event === undefined) {
        return;
      }
      await this.handleMessagesUpsert(socket, epoch, event);
    }
  }

  private emitStatus(
    details: Record<string, unknown> = {},
  ): Promise<void> {
    return this.emitDurableEvent("session_status", {
      state: this.state,
      registered: this.auth.state.creds.registered,
      linked: isLinked(this.auth.state.creds),
      reconnect_attempts: this.reconnectAttempts,
      ...details,
    });
  }

  private emitLinkChallenge(
    context: ChallengeRequestContext,
    method: ChallengeMethod,
    value: string,
    expiresAt: number,
  ): Promise<void> {
    return this.emitDurableEvent("link_challenge", {
      method,
      requested_method: context.method,
      request_revision: context.requestRevision,
      value,
      expires_at: expiresAt,
    });
  }

  private emitDurableEvent(
    event: string,
    payload: Record<string, unknown>,
  ): Promise<void> {
    return this.durableEvents.enqueue(event, payload);
  }

  private enqueueSocketEvent(operation: () => Promise<void>): void {
    if (this.stopping || this.terminal) {
      return;
    }
    const depth =
      this.deferredMessageUpserts.length +
      this.socketEventQueue.length +
      (this.socketEventWorker === null ? 0 : 1);
    if (depth >= MAX_SOCKET_EVENT_QUEUE_ITEMS) {
      this.runBackground(() =>
        this.failTerminal("socket_event_capacity_exceeded"),
      );
      return;
    }
    this.socketEventQueue.push(operation);
    this.ensureSocketEventWorker();
  }

  private ensureSocketEventWorker(): void {
    if (this.socketEventWorker !== null) {
      return;
    }
    const worker = this.drainSocketEvents();
    this.socketEventWorker = worker;
    void worker.then(
      () => {
        this.completeSocketEventWorker(worker);
      },
      () => {
        this.completeSocketEventWorker(worker);
      },
    );
  }

  private completeSocketEventWorker(worker: Promise<void>): void {
    if (this.socketEventWorker !== worker) {
      return;
    }
    this.socketEventWorker = null;
    if (
      !this.stopping &&
      !this.terminal &&
      this.socketEventQueue.length > 0
    ) {
      this.ensureSocketEventWorker();
    }
  }

  private async drainSocketEvents(): Promise<void> {
    while (!this.stopping && !this.terminal) {
      const operation = this.socketEventQueue.shift();
      if (operation === undefined) {
        return;
      }
      try {
        await operation();
      } catch {
        this.socketEventQueue.length = 0;
        await this.failTerminal("session_runtime_failure").catch(
          () => undefined,
        );
        return;
      }
    }
  }

  private runBackground(operation: () => Promise<void>): void {
    const task = operation().catch(async () => {
      await this.failTerminal("session_runtime_failure").catch(() => undefined);
    });
    this.backgroundTasks.add(task);
    void task.finally(() => {
      this.backgroundTasks.delete(task);
    });
  }

  private async failTerminal(code: string): Promise<void> {
    if (this.stopping || this.terminal) {
      return;
    }
    this.terminal = true;
    this.state = "failed";
    this.socketEventQueue.length = 0;
    this.deferredMessageUpserts.length = 0;
    await this.closeCurrentSocket();
    try {
      await this.emitStatus({ failure_code: code });
    } finally {
      this.abortController.abort();
      this.durableEvents.stop();
    }
  }

  private assertOperational(): void {
    if (this.stopping || this.terminal) {
      throw new BridgeError("session_not_operational");
    }
  }

  private requireSocket(): WASocket {
    if (this.socket === null) {
      throw new BridgeError("socket_not_available", { retryable: true });
    }
    return this.socket;
  }

  private isCurrent(socket: WASocket, epoch: number): boolean {
    return (
      !this.stopping &&
      !this.terminal &&
      this.socket === socket &&
      this.socketEpoch === epoch
    );
  }

  private detachSocket(socket: WASocket, epoch: number): void {
    if (this.socket !== socket || this.socketEpoch !== epoch) {
      return;
    }
    const listeners = this.socketListeners;
    if (
      listeners !== null &&
      listeners.socket === socket &&
      listeners.epoch === epoch
    ) {
      socket.ev.off("creds.update", listeners.onCredentialsUpdate);
      socket.ev.off("connection.update", listeners.onConnectionUpdate);
      socket.ev.off("contacts.upsert", listeners.onContactsUpsert);
      socket.ev.off("contacts.update", listeners.onContactsUpdate);
      socket.ev.off("groups.upsert", listeners.onGroupsUpsert);
      socket.ev.off("groups.update", listeners.onGroupsUpdate);
      socket.ev.off(
        "group-participants.update",
        listeners.onGroupParticipantsUpdate,
      );
      socket.ev.off("messaging-history.set", listeners.onHistorySet);
      socket.ev.off("lid-mapping.update", listeners.onLidMappingUpdate);
      socket.ev.off("messages.upsert", listeners.onMessagesUpsert);
      this.socketListeners = null;
    }
    this.socket = null;
    this.connectedStatusAcknowledged = false;
    this.socketEpoch += 1;
  }

  private detachCurrentSocket(): WASocket | null {
    const socket = this.socket;
    if (socket === null) {
      return null;
    }
    this.detachSocket(socket, this.socketEpoch);
    return socket;
  }

  private async closeCurrentSocket(): Promise<void> {
    const socket = this.detachCurrentSocket();
    if (socket !== null) {
      await closeSocket(socket);
    }
  }
}

function lifecycleKey(sessionHandle: string, generation: number): string {
  return `${sessionHandle}\u0000${String(generation)}`;
}

function normalizeLifecycleError(error: unknown): BridgeError {
  return error instanceof BridgeError
    ? error
    : new BridgeError("session_close_failed", { retryable: true });
}

function readChallengeMethod(value: unknown): ChallengeMethod {
  const method = readString(value, "invalid_challenge_method", {
    maxLength: 32,
  });
  if (method !== "pairing_code" && method !== "qr") {
    throw new BridgeError("invalid_challenge_method");
  }
  return method;
}

function isLinked(credentials: AuthenticationCreds): boolean {
  return (
    credentials.registered ||
    (credentials.account !== undefined &&
      credentials.me !== undefined)
  );
}

export function classifyDisconnect(error: unknown): DisconnectDisposition {
  const statusCode = readStatusCode(error);
  switch (statusCode) {
    case DisconnectReason.loggedOut:
      return { classification: "needs_relink", failureCode: "logged_out" };
    case DisconnectReason.badSession:
      return { classification: "needs_relink", failureCode: "bad_session" };
    case DisconnectReason.multideviceMismatch:
      return {
        classification: "needs_relink",
        failureCode: "multidevice_mismatch",
      };
    case DisconnectReason.forbidden:
      return { classification: "needs_relink", failureCode: "forbidden" };
    case DisconnectReason.restartRequired:
      return {
        classification: "restart_required",
        failureCode: "restart_required",
      };
    case DisconnectReason.connectionClosed:
      return {
        classification: "transient_reconnect",
        failureCode: "connection_closed",
      };
    case DisconnectReason.connectionLost:
      return {
        classification: "transient_reconnect",
        failureCode: "connection_lost",
      };
    case DisconnectReason.timedOut:
      return {
        classification: "transient_reconnect",
        failureCode: "timed_out",
      };
    case DisconnectReason.unavailableService:
      return {
        classification: "transient_reconnect",
        failureCode: "unavailable_service",
      };
    case DisconnectReason.connectionReplaced:
      return {
        classification: "transient_reconnect",
        failureCode: "connection_replaced",
      };
    default:
      return {
        classification: "transient_reconnect",
        failureCode: "unknown_disconnect",
      };
  }
}

export function isOneToOneJid(
  jid: string | undefined | null,
): jid is string {
  if (typeof jid !== "string" || !ONE_TO_ONE_JID_PATTERN.test(jid)) {
    return false;
  }
  return (
    isPnUser(jid) === true ||
    isLidUser(jid) === true ||
    isHostedPnUser(jid) === true ||
    isHostedLidUser(jid) === true
  );
}

export function isGroupJid(
  jid: string | undefined | null,
): jid is string {
  return (
    typeof jid === "string" &&
    GROUP_JID_PATTERN.test(jid) &&
    isJidGroup(jid) === true
  );
}

export function isWhatsAppChatJid(
  jid: string | undefined | null,
): jid is string {
  return isOneToOneJid(jid) || isGroupJid(jid);
}

export async function extractInboundText(
  socket: WASocket,
  message: WAMessage,
  identityDirectory = new WhatsAppIdentityDirectory(),
): Promise<InboundText | null> {
  if (message.key.fromMe === true) {
    return null;
  }
  const remoteJid = message.key.remoteJid;
  if (!isWhatsAppChatJid(remoteJid)) {
    return null;
  }
  const chatKind = isGroupJid(remoteJid) ? "group" : "direct";
  const primarySender =
    chatKind === "group" ? message.key.participant : remoteJid;
  const alternateSender =
    chatKind === "group"
      ? message.key.participantAlt
      : message.key.remoteJidAlt;
  const senderJid = normalizedOneToOneJid(primarySender);
  const senderJidAlt = normalizedOneToOneJid(alternateSender);
  const canonicalSenderJid = senderJid ?? senderJidAlt;
  if (canonicalSenderJid === null) {
    return null;
  }
  const providerMessageId = message.key.id;
  if (typeof providerMessageId !== "string" || providerMessageId.length === 0) {
    return null;
  }
  const content = normalizeMessageContent(message.message);
  const contentType = getContentType(content);
  const body =
    contentType === "conversation"
      ? content?.conversation
      : contentType === "extendedTextMessage"
        ? content?.extendedTextMessage?.text
        : null;
  if (
    typeof body !== "string" ||
    body.trim().length === 0 ||
    Buffer.byteLength(body, "utf8") > MAX_TEXT_BYTES
  ) {
    return null;
  }

  const identity = await resolveJidIdentity(
    socket,
    senderJid,
    senderJidAlt,
    identityDirectory,
    chatKind === "group" ? remoteJid : null,
  );
  return {
    provider_message_id: providerMessageId,
    chat_jid: jidNormalizedUser(remoteJid),
    chat_kind: chatKind,
    sender_jid: canonicalSenderJid,
    sender_jid_alt:
      senderJid !== null && senderJidAlt !== senderJid ? senderJidAlt : null,
    sender_username: normalizeOptionalIdentityText(
      chatKind === "group"
        ? message.key.participantUsername
        : message.key.remoteJidUsername,
    ),
    sender_pn_jid: identity.pn_jid,
    sender_lid_jid: identity.lid_jid,
    addressing_mode: normalizeAddressingMode(message.key.addressingMode),
    identity_proof: identity.proof,
    push_name: normalizeOptionalIdentityText(message.pushName),
    body,
    timestamp: normalizeTimestamp(message.messageTimestamp),
  };
}

function normalizedOneToOneJid(
  value: string | null | undefined,
): string | null {
  if (!isOneToOneJid(value)) {
    return null;
  }
  return jidNormalizedUser(value);
}

function normalizeOptionalIdentityText(
  value: string | null | undefined,
): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const normalized = value.trim();
  if (
    normalized.length === 0 ||
    Buffer.byteLength(normalized, "utf8") > MAX_IDENTITY_TEXT_BYTES
  ) {
    return null;
  }
  return normalized;
}

function normalizeAddressingMode(
  value: string | null | undefined,
): "lid" | "pn" | null {
  return value === "lid" || value === "pn" ? value : null;
}

export async function resolveSelfIdentity(
  socket: WASocket,
  auth: AuthenticationState,
): Promise<ReportedIdentity> {
  const user = socket.user;
  const me = auth.creds.me;
  return resolveJidIdentity(
    socket,
    user?.id ?? me?.id,
    user?.lid ?? user?.phoneNumber ?? me?.lid ?? me?.phoneNumber,
  );
}

async function resolveJidIdentity(
  socket: WASocket,
  primary: string | null | undefined,
  alternate: string | null | undefined,
  identityDirectory = new WhatsAppIdentityDirectory(),
  groupJid: string | null = null,
): Promise<ReportedIdentity> {
  const normalized = [primary, alternate]
    .filter((value): value is string => typeof value === "string")
    .map((value) => jidNormalizedUser(value))
    .filter((value) => value.length > 0);
  let pnJid =
    normalized.find(
      (jid) => isPnUser(jid) === true || isHostedPnUser(jid) === true,
    ) ??
    null;
  const lidJid =
    normalized.find(
      (jid) => isLidUser(jid) === true || isHostedLidUser(jid) === true,
    ) ??
    null;
  if (pnJid === null && lidJid !== null) {
    pnJid = identityDirectory.contactPnForLid(lidJid);
    if (pnJid === null && groupJid !== null) {
      const metadata = await identityDirectory.groupMetadata(socket, groupJid);
      if (metadata !== null) {
        pnJid = identityDirectory.groupParticipantPn(metadata, lidJid);
      }
    }
    if (pnJid === null) {
      const mapped = await socket.signalRepository.lidMapping.getPNForLID(lidJid);
      pnJid = normalizedPnJid(mapped);
    }
    pnJid ??= identityDirectory.mappedPnForLid(lidJid);
  }
  return {
    pn_jid: pnJid,
    lid_jid: lidJid,
    proof:
      pnJid !== null && lidJid !== null
        ? "pn_and_lid"
        : pnJid !== null
          ? "pn"
          : lidJid !== null
            ? "lid_only"
            : "unresolved",
  };
}

function normalizedPnJid(value: string | null | undefined): string | null {
  if (
    typeof value !== "string" ||
    (isPnUser(value) !== true && isHostedPnUser(value) !== true)
  ) {
    return null;
  }
  return jidNormalizedUser(value);
}

function normalizedLidJid(value: string | null | undefined): string | null {
  if (
    typeof value !== "string" ||
    (isLidUser(value) !== true && isHostedLidUser(value) !== true)
  ) {
    return null;
  }
  return jidNormalizedUser(value);
}

function normalizedGroupJid(value: string | null | undefined): string | null {
  return isGroupJid(value) ? jidNormalizedUser(value) : null;
}

function contactJids(contact: Partial<Contact>): string[] {
  const normalized = [contact.id, contact.lid, contact.phoneNumber]
    .map((jid) => normalizedOneToOneJid(jid))
    .filter((jid): jid is string => jid !== null);
  return [...new Set(normalized)];
}

function pnJidFromContact(contact: Partial<Contact>): string | null {
  return normalizedPnJid(contact.phoneNumber) ?? normalizedPnJid(contact.id);
}

function rememberBounded<K, V>(
  values: Map<K, V>,
  key: K,
  value: V,
  maximumSize: number,
): void {
  values.delete(key);
  values.set(key, value);
  while (values.size > maximumSize) {
    const oldest = values.keys().next();
    if (oldest.done === true) {
      return;
    }
    values.delete(oldest.value);
  }
}

async function createBaileysSocket(
  auth: AuthenticationState,
): Promise<WASocket> {
  const version = await currentWaWebVersion();
  return makeWASocket({
    auth,
    browser: Browsers.macOS("Voxos"),
    emitOwnEvents: false,
    getMessage: () => Promise.resolve(undefined),
    logger: pino({ level: "silent" }),
    markOnlineOnConnect: false,
    shouldIgnoreJid: (jid: string) =>
      isJidBroadcast(jid) === true ||
      isJidNewsletter(jid) === true ||
      isJidStatusBroadcast(jid),
    syncFullHistory: false,
    version,
  });
}

async function currentWaWebVersion(): Promise<WAVersion> {
  const now = Date.now();
  if (cachedWaWebVersion !== null && cachedWaWebVersion.expiresAt > now) {
    return cachedWaWebVersion.version;
  }
  waWebVersionRefresh ??= fetchLatestWaWebVersion()
    .then((result) => {
      cachedWaWebVersion = {
        expiresAt: Date.now() + WA_WEB_VERSION_CACHE_TTL_MS,
        version: result.version,
      };
      return result.version;
    })
    .finally(() => {
      waWebVersionRefresh = null;
    });
  return waWebVersionRefresh;
}

function assertBridgeCommand(command: CommandFrame): void {
  if (
    command.session_handle !== BRIDGE_SESSION_HANDLE ||
    command.generation !== 0
  ) {
    throw new BridgeError("invalid_command_scope");
  }
}

function readStatusCode(error: unknown): number | null {
  try {
    const root = expectRecord(error, "unknown_disconnect");
    if (typeof root.statusCode === "number") {
      return root.statusCode;
    }
    if (root.output !== undefined) {
      const output = expectRecord(root.output, "unknown_disconnect");
      if (typeof output.statusCode === "number") {
        return output.statusCode;
      }
    }
    if (root.data !== undefined) {
      const data = expectRecord(root.data, "unknown_disconnect");
      if (typeof data.statusCode === "number") {
        return data.statusCode;
      }
    }
  } catch {
    return null;
  }
  return null;
}

function normalizeTimestamp(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.trunc(value).toString(10);
  }
  if (typeof value === "bigint") {
    return value.toString(10);
  }
  if (typeof value === "string" && /^[0-9]{1,20}$/.test(value)) {
    return value;
  }
  if (typeof value === "object" && value !== null) {
    const record = value as {
      readonly high?: unknown;
      readonly low?: unknown;
      readonly unsigned?: unknown;
    };
    if (
      typeof record.high === "number" &&
      Number.isInteger(record.high) &&
      typeof record.low === "number" &&
      Number.isInteger(record.low)
    ) {
      const raw =
        (BigInt(record.high >>> 0) << 32n) | BigInt(record.low >>> 0);
      const timestamp =
        record.unsigned === true ? raw : BigInt.asIntN(64, raw);
      return timestamp >= 0n ? timestamp.toString(10) : null;
    }
  }
  return null;
}

async function closeSocket(socket: WASocket): Promise<void> {
  try {
    await withTimeout(socket.end(undefined), CLOSE_TIMEOUT_MS, "socket_close_timeout");
  } catch {
    try {
      await socket.ws.close();
    } catch {
      return;
    }
  }
}

function withTimeout<T>(
  operation: Promise<T>,
  timeoutMs: number,
  code: string,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new BridgeError(code, { retryable: true }));
    }, timeoutMs);
    timer.unref();
    operation.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(
          error instanceof Error
            ? error
            : new BridgeError("upstream_operation_failed", { retryable: true }),
        );
      },
    );
  });
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted || milliseconds === 0) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, milliseconds);
    timer.unref();
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}
