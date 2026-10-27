import assert from "node:assert/strict";
import { Writable } from "node:stream";
import { describe, it } from "node:test";

import {
  DisconnectReason,
  type AuthenticationState,
  type GroupMetadata,
  type WAMessage,
  type WASocket,
} from "baileys";

import {
  type CommandFrame,
  JsonLineWriter,
  PROTOCOL_VERSION,
  expectRecord,
} from "./protocol.js";
import { BridgeRpcPeer } from "./rpc-peer.js";
import {
  classifyDisconnect,
  type Delay,
  SessionManager,
  type SocketFactory,
} from "./session-manager.js";
import { decodeTagged } from "./tagged-json.js";

type CapturedFrame = Record<string, unknown>;
type EventListener = (value: unknown) => void;

interface FrameWaiter {
  readonly predicate: (frame: CapturedFrame) => boolean;
  readonly resolve: (frame: CapturedFrame) => void;
  timer: NodeJS.Timeout | undefined;
}

class CaptureWritable extends Writable {
  readonly frames: CapturedFrame[] = [];
  private readonly waiters = new Set<FrameWaiter>();

  override _write(
    chunk: Buffer | string,
    _encoding: BufferEncoding,
    callback: (error?: Error | null) => void,
  ): void {
    try {
      const lines = chunk.toString().trim().split("\n");
      for (const line of lines) {
        if (line.length === 0) {
          continue;
        }
        const frame = expectRecord(
          decodeTagged(JSON.parse(line) as unknown),
          "invalid_test_frame",
        );
        this.frames.push(frame);
        for (const waiter of this.waiters) {
          if (waiter.predicate(frame)) {
            if (waiter.timer !== undefined) {
              clearTimeout(waiter.timer);
            }
            this.waiters.delete(waiter);
            waiter.resolve(frame);
          }
        }
      }
      callback();
    } catch (error) {
      callback(error instanceof Error ? error : new Error("capture failed"));
    }
  }

  waitFor(
    predicate: (frame: CapturedFrame) => boolean,
    afterIndex = 0,
  ): Promise<CapturedFrame> {
    const existing = this.frames.slice(afterIndex).find(predicate);
    if (existing !== undefined) {
      return Promise.resolve(existing);
    }
    return new Promise((resolve, reject) => {
      const waiter: FrameWaiter = {
        predicate,
        resolve,
        timer: undefined,
      };
      this.waiters.add(waiter);
      waiter.timer = setTimeout(() => {
        this.waiters.delete(waiter);
        reject(new Error("timed out waiting for captured frame"));
      }, 2_000);
    });
  }
}

class FakeEvents {
  private readonly listeners = new Map<string, Set<EventListener>>();

  on(event: string, listener: EventListener): void {
    const listeners = this.listeners.get(event) ?? new Set<EventListener>();
    listeners.add(listener);
    this.listeners.set(event, listeners);
  }

  off(event: string, listener: EventListener): void {
    this.listeners.get(event)?.delete(listener);
  }

  removeAllListeners(event: string): void {
    this.listeners.delete(event);
  }

  emit(event: string, value: unknown): void {
    for (const listener of this.listeners.get(event) ?? []) {
      listener(value);
    }
  }
}

class FakeSocket {
  readonly ev = new FakeEvents();
  readonly auth: AuthenticationState;
  readonly signalRepository = {
    lidMapping: {
      getPNForLID: (jid: string): Promise<string | undefined> =>
        Promise.resolve(this.lidMappings.get(jid)),
    },
  };
  readonly lidMappings = new Map<string, string>();
  readonly groups = new Map<string, GroupMetadata>();
  readonly ws = {
    close: (): void => {
      this.wsCloseCalls += 1;
    },
  };
  user = {
    id: "447700900001@s.whatsapp.net",
    lid: "100001@lid",
  };
  endCalls = 0;
  internalCloseUpdates = 0;
  logoutCalls = 0;
  pairingCalls = 0;
  pairingPhoneNumber: string | null = null;
  sendCalls = 0;
  groupMetadataCalls = 0;
  wsCloseCalls = 0;
  sendBehavior: () => Promise<WAMessage | undefined> = () =>
    Promise.resolve({
      key: {
        id: `sent-${String(this.sendCalls)}`,
        remoteJid: "447700900002@s.whatsapp.net",
        fromMe: true,
      },
    } as WAMessage);
  logoutBehavior: () => Promise<void> = () => Promise.resolve();

  constructor(auth: AuthenticationState) {
    this.auth = auth;
    this.ev.on("connection.update", (value: unknown) => {
      const update = expectRecord(value, "invalid_connection_update");
      if (update.connection === "close") {
        this.internalCloseUpdates += 1;
      }
    });
  }

  requestPairingCode(phoneNumber: string): Promise<string> {
    this.pairingCalls += 1;
    this.pairingPhoneNumber = phoneNumber;
    return Promise.resolve("1234-5678");
  }

  sendMessage(): Promise<WAMessage | undefined> {
    this.sendCalls += 1;
    return this.sendBehavior();
  }

  groupMetadata(jid: string): Promise<GroupMetadata> {
    this.groupMetadataCalls += 1;
    const metadata = this.groups.get(jid);
    if (metadata === undefined) {
      return Promise.reject(new Error("group metadata unavailable"));
    }
    return Promise.resolve(metadata);
  }

  end(): Promise<void> {
    this.endCalls += 1;
    return Promise.resolve();
  }

  logout(): Promise<void> {
    this.logoutCalls += 1;
    return this.logoutBehavior();
  }

  asSocket(): WASocket {
    return this as unknown as WASocket;
  }
}

interface Harness {
  readonly capture: CaptureWritable;
  readonly manager: SessionManager;
  readonly sockets: FakeSocket[];
}

describe("session manager", () => {
  it("negotiates linked state, durable events, and the exact RPC timeout", async () => {
    const harness = makeHarness({ rpcTimeoutMs: 12_345 });

    await harness.manager.handleCommand(
      command("handshake", "handshake-1", "bridge", 0),
    );
    const response = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "handshake-1" &&
        frame.success === true,
    );
    const payload = expectRecord(response.payload, "invalid_payload");
    assert.equal(payload.rpc_timeout_ms, 12_345);
    assert.ok(Array.isArray(payload.capabilities));
    assert.ok(payload.capabilities.includes("challenge_revisions"));
    assert.ok(payload.capabilities.includes("linked_state"));
    assert.ok(payload.capabilities.includes("logout_if_linked"));
    assert.ok(payload.capabilities.includes("durable_events"));
    assert.ok(payload.capabilities.includes("ordered_durable_events"));
  });

  it("multiplexes remote auth without session crossover and waits for acknowledgements", async () => {
    const harness = makeHarness();
    const first = await startFreshSession(harness, "session-a", 1);
    const second = await startFreshSession(harness, "session-b", 4);

    let firstSettled = false;
    const firstSet = Promise.resolve(
      first.auth.keys.set({
        session: {
          peer: new Uint8Array([1, 2, 3]),
        },
      }),
    ).then(() => {
      firstSettled = true;
    });
    const firstRpc = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "auth_set" &&
        frame.session_handle === "session-a" &&
        authWriteCategory(frame) === "session",
    );
    await nextTurn();
    assert.equal(firstSettled, false);

    const secondGet = second.auth.keys.get("session", ["peer"]);
    const secondRpc = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "auth_get" &&
        frame.session_handle === "session-b",
    );
    await acknowledgeRpc(harness, firstRpc, {});
    await acknowledgeRpc(harness, secondRpc, {
      values: { peer: new Uint8Array([9, 8, 7]) },
    });
    await firstSet;
    const secondValue = await secondGet;

    assert.equal(firstSettled, true);
    assert.deepStrictEqual(secondValue.peer, new Uint8Array([9, 8, 7]));
    assert.equal(harness.sockets.length, 2);

    const prekeys = Object.fromEntries(
      Array.from({ length: 812 }, (_, index) => [
        `prekey-${String(index)}`,
        new Uint8Array([index % 256]),
      ]),
    );
    const cursor = harness.capture.frames.length;
    const prekeySet = Promise.resolve(
      first.auth.keys.set({ session: prekeys }),
    );
    const prekeyWrite = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "auth_set" &&
        frame.session_handle === "session-a",
      cursor,
    );
    assert.equal(authWriteCount(prekeyWrite), 812);
    await acknowledgeRpc(harness, prekeyWrite, {});
    await prekeySet;

    await harness.manager.handleCommand(
      command("shutdown", "shutdown-1", "bridge", 0),
    );
    assert.equal(first.endCalls, 1);
    assert.equal(second.endCalls, 1);
    first.ev.emit("connection.update", { connection: "close" });
    second.ev.emit("connection.update", { connection: "close" });
    assert.equal(first.internalCloseUpdates, 1);
    assert.equal(second.internalCloseUpdates, 1);
  });

  it("reports PN and LID identity and accepts inbound direct text", async () => {
    const harness = makeHarness();
    const socket = await startFreshSession(harness, "session-a", 1);
    socket.ev.emit("connection.update", { connection: "open" });
    const connected = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "session_status" &&
        expectRecord(frame.payload, "invalid_payload").state === "connected",
    );
    const identity = expectRecord(
      expectRecord(connected.payload, "invalid_payload").identity,
      "invalid_identity",
    );
    assert.equal(identity.pn_jid, "447700900001@s.whatsapp.net");
    assert.equal(identity.lid_jid, "100001@lid");
    assert.equal(identity.proof, "pn_and_lid");
    await acknowledgeRpc(harness, connected, {});

    await harness.manager.handleCommand(
      command("send_text", "send-success", "session-a", 1, {
        to: "447700900002@s.whatsapp.net",
        text: "A durable reply",
      }),
    );
    const sendResponse = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "send-success" &&
        frame.success === true,
    );
    assert.equal(
      expectRecord(sendResponse.payload, "invalid_payload").provider_message_id,
      "sent-1",
    );

    const beforeMessages = harness.capture.frames.length;
    socket.ev.emit("messages.upsert", {
      type: "notify",
      messages: [
        message({
          id: "from-self",
          remoteJid: "447700900002@s.whatsapp.net",
          fromMe: true,
          content: { conversation: "ignore self" },
        }),
        message({
          id: "group",
          remoteJid: "12345@g.us",
          content: { conversation: "ignore group" },
        }),
        message({
          id: "media",
          remoteJid: "447700900002@s.whatsapp.net",
          content: { imageMessage: { caption: "ignore media" } },
        }),
        message({
          id: "accepted",
          remoteJid: "447700900002@s.whatsapp.net",
          remoteJidAlt: "100002@lid",
          remoteJidUsername: "customer.name",
          addressingMode: "pn",
          pushName: "Customer Name",
          content: { extendedTextMessage: { text: "Hello Voxos" } },
        }),
      ],
    });
    const inbound = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "inbound_text" &&
        expectRecord(frame.payload, "invalid_payload").provider_message_id ===
          "accepted",
      beforeMessages,
    );
    await nextTurn();
    const inboundEvents = harness.capture.frames
      .slice(beforeMessages)
      .filter((frame) => frame.event === "inbound_text");
    assert.equal(inboundEvents.length, 1);
    assert.equal(
      expectRecord(inbound.payload, "invalid_payload").body,
      "Hello Voxos",
    );
    assert.deepEqual(inbound.payload, {
      provider_message_id: "accepted",
      chat_jid: "447700900002@s.whatsapp.net",
      chat_kind: "direct",
      sender_jid: "447700900002@s.whatsapp.net",
      sender_jid_alt: "100002@lid",
      sender_username: "customer.name",
      sender_pn_jid: "447700900002@s.whatsapp.net",
      sender_lid_jid: "100002@lid",
      addressing_mode: "pn",
      identity_proof: "pn_and_lid",
      push_name: "Customer Name",
      body: "Hello Voxos",
      timestamp: "1752999999",
    });
    await acknowledgeRpc(harness, inbound, {});

    await harness.manager.abort();
  });

  it("selects group participant fields and resolves PN from group metadata", async () => {
    const harness = makeHarness();
    const socket = await startFreshSession(harness, "session-a", 1);
    socket.groups.set("120363000000001@g.us", {
      id: "120363000000001@g.us",
      owner: undefined,
      subject: "Customer group",
      participants: [
        {
          id: "100002@lid",
          lid: "100002@lid",
          phoneNumber: "447700900002@s.whatsapp.net",
        },
      ],
    });
    socket.ev.emit("connection.update", { connection: "open" });
    const connected = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "session_status" &&
        expectRecord(frame.payload, "invalid_payload").state === "connected",
    );
    await acknowledgeRpc(harness, connected, {});

    const beforeMessage = harness.capture.frames.length;
    socket.ev.emit("messages.upsert", {
      type: "notify",
      messages: [
        message({
          id: "group-message",
          remoteJid: "120363000000001@g.us",
          participant: "100002@lid",
          participantUsername: "customer.name",
          addressingMode: "lid",
          pushName: "Customer Name",
          content: { conversation: "Hello group" },
        }),
      ],
    });
    const inbound = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "inbound_text" &&
        expectRecord(frame.payload, "invalid_payload").provider_message_id ===
          "group-message",
      beforeMessage,
    );

    assert.deepEqual(inbound.payload, {
      provider_message_id: "group-message",
      chat_jid: "120363000000001@g.us",
      chat_kind: "group",
      sender_jid: "100002@lid",
      sender_jid_alt: null,
      sender_username: "customer.name",
      sender_pn_jid: "447700900002@s.whatsapp.net",
      sender_lid_jid: "100002@lid",
      addressing_mode: "lid",
      identity_proof: "pn_and_lid",
      push_name: "Customer Name",
      body: "Hello group",
      timestamp: "1752999999",
    });
    assert.equal(socket.groupMetadataCalls, 1);
    await acknowledgeRpc(harness, inbound, {});

    const beforeAlternate = harness.capture.frames.length;
    socket.ev.emit("messages.upsert", {
      type: "notify",
      messages: [
        message({
          id: "group-message-with-alt",
          remoteJid: "120363000000001@g.us",
          participant: "100003@lid",
          participantAlt: "447700900003@s.whatsapp.net",
          content: { conversation: "Hello from alternate" },
        }),
      ],
    });
    const alternate = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "inbound_text" &&
        expectRecord(frame.payload, "invalid_payload").provider_message_id ===
          "group-message-with-alt",
      beforeAlternate,
    );
    const alternatePayload = expectRecord(alternate.payload, "invalid_payload");
    assert.equal(alternatePayload.sender_jid, "100003@lid");
    assert.equal(
      alternatePayload.sender_jid_alt,
      "447700900003@s.whatsapp.net",
    );
    assert.equal(
      alternatePayload.sender_pn_jid,
      "447700900003@s.whatsapp.net",
    );
    assert.equal(alternatePayload.sender_lid_jid, "100003@lid");
    await acknowledgeRpc(harness, alternate, {});

    await harness.manager.handleCommand(
      command("send_text", "send-group", "session-a", 1, {
        to: "120363000000001@g.us",
        text: "A group reply",
      }),
    );
    const sent = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "send-group",
    );
    assert.equal(sent.success, true);
    await harness.manager.abort();
  });

  it("resolves LIDs from contacts, repository mappings, and mapping events without guessing", async () => {
    const harness = makeHarness();
    const socket = await startFreshSession(harness, "session-a", 1);
    socket.ev.emit("contacts.upsert", [
      {
        id: "100010@lid",
        lid: "100010@lid",
        phoneNumber: "447700900010@s.whatsapp.net",
      },
    ]);
    socket.lidMappings.set(
      "100011@lid",
      "447700900011@s.whatsapp.net",
    );
    socket.ev.emit("lid-mapping.update", {
      lid: "100012@lid",
      pn: "447700900012@s.whatsapp.net",
    });
    socket.ev.emit("messaging-history.set", {
      chats: [],
      contacts: [],
      messages: [],
      lidPnMappings: [
        {
          lid: "100013@lid",
          pn: "447700900013@s.whatsapp.net",
        },
      ],
    });
    socket.ev.emit("connection.update", { connection: "open" });
    const connected = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "session_status" &&
        expectRecord(frame.payload, "invalid_payload").state === "connected",
    );
    await acknowledgeRpc(harness, connected, {});

    const beforeMessages = harness.capture.frames.length;
    socket.ev.emit("messages.upsert", {
      type: "notify",
      messages: [
        message({
          id: "contact-map",
          remoteJid: "100010@lid",
          content: { conversation: "contact" },
        }),
        message({
          id: "repository-map",
          remoteJid: "100011@lid",
          content: { conversation: "repository" },
        }),
        message({
          id: "event-map",
          remoteJid: "100012@lid",
          content: { conversation: "event" },
        }),
        message({
          id: "history-map",
          remoteJid: "100013@lid",
          content: { conversation: "history" },
        }),
        message({
          id: "lid-only",
          remoteJid: "447700900099@lid",
          content: { conversation: "private identity" },
        }),
      ],
    });

    const expectedPns = new Map([
      ["contact-map", "447700900010@s.whatsapp.net"],
      ["repository-map", "447700900011@s.whatsapp.net"],
      ["event-map", "447700900012@s.whatsapp.net"],
      ["history-map", "447700900013@s.whatsapp.net"],
    ]);
    for (const [messageId, pnJid] of expectedPns) {
      const inbound = await harness.capture.waitFor(
        (frame) =>
          frame.frame_type === "event" &&
          frame.event === "inbound_text" &&
          expectRecord(frame.payload, "invalid_payload").provider_message_id ===
            messageId,
        beforeMessages,
      );
      const payload = expectRecord(inbound.payload, "invalid_payload");
      assert.equal(payload.sender_pn_jid, pnJid);
      assert.equal(payload.identity_proof, "pn_and_lid");
      await acknowledgeRpc(harness, inbound, {});
    }
    const lidOnly = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "inbound_text" &&
        expectRecord(frame.payload, "invalid_payload").provider_message_id ===
          "lid-only",
      beforeMessages,
    );
    const lidOnlyPayload = expectRecord(lidOnly.payload, "invalid_payload");
    assert.equal(lidOnlyPayload.sender_pn_jid, null);
    assert.equal(lidOnlyPayload.sender_lid_jid, "447700900099@lid");
    assert.equal(lidOnlyPayload.identity_proof, "lid_only");
    await acknowledgeRpc(harness, lidOnly, {});
    await harness.manager.abort();
  });

  it("recognizes QR-linked credentials even when Baileys leaves registered false", async () => {
    const harness = makeHarness();
    const socket = await startFreshSession(harness, "session-a", 1);
    Object.assign(socket.auth.creds, {
      account: {},
      me: { id: "447700900001@s.whatsapp.net" },
      registered: false,
    });

    const beforeOpen = harness.capture.frames.length;
    socket.ev.emit("connection.update", { connection: "open" });
    const connected = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "session_status" &&
        expectRecord(frame.payload, "invalid_payload").state === "connected",
      beforeOpen,
    );
    const connectedPayload = expectRecord(
      connected.payload,
      "invalid_payload",
    );
    assert.equal(connectedPayload.registered, false);
    assert.equal(connectedPayload.linked, true);
    await acknowledgeRpc(harness, connected, {});

    await harness.manager.handleCommand(
      command("session_status", "status-linked", "session-a", 1),
    );
    const status = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "status-linked",
    );
    const statusPayload = expectRecord(status.payload, "invalid_payload");
    assert.equal(statusPayload.registered, false);
    assert.equal(statusPayload.linked, true);

    await harness.manager.handleCommand(
      command("request_challenge", "challenge-linked", "session-a", 1, {
        method: "qr",
        request_revision: 1,
      }),
    );
    const challenge = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "challenge-linked",
    );
    assert.equal(challenge.success, false);
    assert.deepStrictEqual(challenge.error, {
      code: "already_linked",
      retryable: false,
      ambiguous: false,
    });

    await harness.manager.abort();
  });

  it("persists the connected status before delivering queued inbound text", async () => {
    const harness = makeHarness();
    const socket = await startFreshSession(harness, "session-a", 1);
    const beforeOpen = harness.capture.frames.length;

    socket.ev.emit("messages.upsert", {
      type: "notify",
      messages: [
        message({
          id: "ordered-after-open",
          remoteJid: "447700900002@s.whatsapp.net",
          content: { conversation: "Deliver after connected is durable" },
        }),
      ],
    });
    socket.ev.emit("connection.update", { connection: "open" });

    const connected = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "session_status" &&
        expectRecord(frame.payload, "invalid_payload").state === "connected",
      beforeOpen,
    );
    await nextTurn();
    assert.equal(
      harness.capture.frames
        .slice(beforeOpen)
        .some((frame) => frame.event === "inbound_text"),
      false,
    );

    await acknowledgeRpc(harness, connected, {});
    const inbound = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "inbound_text" &&
        expectRecord(frame.payload, "invalid_payload").provider_message_id ===
          "ordered-after-open",
      beforeOpen,
    );
    await acknowledgeRpc(harness, inbound, {});
    await harness.manager.abort();
  });

  it("bounds pre-connect inbound handlers and closes only the overflowing session", async () => {
    const harness = makeHarness();
    const socket = await startFreshSession(harness, "session-a", 1);
    const beforeMessages = harness.capture.frames.length;

    for (let index = 0; index < 65; index += 1) {
      socket.ev.emit("messages.upsert", {
        type: "notify",
        messages: [
          message({
            id: `queued-before-open-${String(index)}`,
            remoteJid: "447700900002@s.whatsapp.net",
            content: { conversation: "Bound this queue" },
          }),
        ],
      });
    }

    const failed = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "session_status" &&
        expectRecord(frame.payload, "invalid_payload").failure_code ===
          "socket_event_capacity_exceeded",
      beforeMessages,
    );
    await acknowledgeRpc(harness, failed, {});
    assert.equal(socket.endCalls, 1);
    await harness.manager.abort();
  });

  it("retries durable challenges, statuses, and inbound text until acknowledged", async () => {
    const harness = makeHarness({
      durableRetryDelaysMs: [0],
      rpcTimeoutMs: 25,
    });
    const socket = await startFreshSession(harness, "session-a", 1);
    await harness.manager.handleCommand(
      command("request_challenge", "qr-request-retry", "session-a", 1, {
        method: "qr",
        request_revision: 1,
      }),
    );

    socket.ev.emit("connection.update", {
      qr: "retry-qr-value",
      connection: "connecting",
    });
    const firstChallenge = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "link_challenge",
    );
    socket.ev.emit("connection.update", { connection: "open" });
    let retriedChallenge = firstChallenge;
    for (let retry = 0; retry < 3; retry += 1) {
      const afterPreviousAttempt = harness.capture.frames.length;
      retriedChallenge = await harness.capture.waitFor(
        (frame) =>
          frame.frame_type === "event" &&
          frame.event === "link_challenge" &&
          frame.request_id === firstChallenge.request_id,
        afterPreviousAttempt,
      );
    }
    await acknowledgeRpc(harness, retriedChallenge, {});

    const firstStatus = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "session_status" &&
        expectRecord(frame.payload, "invalid_payload").state ===
          "awaiting_link",
    );
    const afterFirstStatus = harness.capture.frames.length;
    const retriedStatus = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "session_status" &&
        frame.request_id === firstStatus.request_id &&
        expectRecord(frame.payload, "invalid_payload").state ===
          "awaiting_link",
      afterFirstStatus,
    );
    await acknowledgeRpc(harness, retriedStatus, {});

    const connected = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "session_status" &&
        expectRecord(frame.payload, "invalid_payload").state === "connected",
    );
    await acknowledgeRpc(harness, connected, {});

    const beforeInbound = harness.capture.frames.length;
    socket.ev.emit("messages.upsert", {
      type: "notify",
      messages: [
        message({
          id: "durable-inbound",
          remoteJid: "447700900002@s.whatsapp.net",
          content: { conversation: "Persist me" },
        }),
      ],
    });
    const firstInbound = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "inbound_text" &&
        expectRecord(frame.payload, "invalid_payload").provider_message_id ===
          "durable-inbound",
      beforeInbound,
    );
    const afterFirstInbound = harness.capture.frames.length;
    const retriedInbound = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "inbound_text" &&
        frame.request_id === firstInbound.request_id &&
        expectRecord(frame.payload, "invalid_payload").provider_message_id ===
          "durable-inbound",
      afterFirstInbound,
    );
    await acknowledgeRpc(harness, retriedInbound, {});

    assert.equal(firstChallenge.request_id, retriedChallenge.request_id);
    assert.equal(firstStatus.request_id, retriedStatus.request_id);
    assert.equal(firstInbound.request_id, retriedInbound.request_id);
    assert.ok(
      harness.capture.frames.indexOf(connected) >
        harness.capture.frames.indexOf(retriedStatus),
    );
    await harness.manager.abort();
  });

  it("does not retry a durable event after a permanent negative acknowledgement", async () => {
    const harness = makeHarness({
      durableRetryDelaysMs: [0],
      rpcTimeoutMs: 25,
    });
    const socket = await startFreshSession(harness, "session-a", 1);
    await harness.manager.handleCommand(
      command("request_challenge", "qr-request-permanent", "session-a", 1, {
        method: "qr",
        request_revision: 1,
      }),
    );
    const beforeQr = harness.capture.frames.length;
    socket.ev.emit("connection.update", {
      qr: "permanently-rejected-qr",
    });
    const qr = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "link_challenge",
      beforeQr,
    );

    await rejectRpc(harness, qr, "session_unavailable");
    await nextTurn();
    await nextTurn();

    assert.equal(
      harness.capture.frames
        .slice(beforeQr)
        .filter((frame) => frame.event === "link_challenge").length,
      1,
    );
    assert.equal(socket.endCalls, 1);
    await harness.manager.abort();
  });

  it("reports ephemeral QR and pairing-code challenges without filesystem state", async () => {
    const harness = makeHarness();
    const socket = await startFreshSession(harness, "session-a", 1);
    await harness.manager.handleCommand(
      command("request_challenge", "qr-request-1", "session-a", 1, {
        method: "qr",
        request_revision: 1,
      }),
    );
    const beforeQr = harness.capture.frames.length;
    socket.ev.emit("connection.update", {
      qr: "ephemeral-qr-value",
      connection: "connecting",
    });
    const qr = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "link_challenge" &&
        expectRecord(frame.payload, "invalid_payload").method === "qr",
      beforeQr,
    );
    const qrPayload = expectRecord(qr.payload, "invalid_payload");
    assert.equal(qrPayload.value, "ephemeral-qr-value");
    assert.equal(qrPayload.request_revision, 1);
    assert.equal(qrPayload.requested_method, "qr");
    await acknowledgeRpc(harness, qr, {});
    const awaitingLink = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "session_status" &&
        expectRecord(frame.payload, "invalid_payload").state ===
          "awaiting_link",
      beforeQr,
    );
    await acknowledgeRpc(harness, awaitingLink, {});

    const pairing = harness.manager.handleCommand(
      command("request_challenge", "pairing-1", "session-a", 1, {
        method: "pairing_code",
        phone_number: "447700900001",
        request_revision: 2,
      }),
    );
    const pairingChallenge = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "link_challenge" &&
        expectRecord(frame.payload, "invalid_payload").method ===
          "pairing_code",
      beforeQr,
    );
    const pairingPayload = expectRecord(
      pairingChallenge.payload,
      "invalid_payload",
    );
    assert.equal(pairingPayload.request_revision, 2);
    assert.equal(pairingPayload.requested_method, "pairing_code");
    await acknowledgeRpc(harness, pairingChallenge, {});
    await pairing;
    const response = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "pairing-1" &&
        frame.success === true,
    );
    assert.equal(
      expectRecord(response.payload, "invalid_payload").value,
      "1234-5678",
    );
    assert.equal(socket.pairingCalls, 1);
    assert.equal(socket.pairingPhoneNumber, "447700900001");
    const challengeCount = harness.capture.frames.filter(
      (frame) => frame.event === "link_challenge",
    ).length;
    socket.ev.emit("connection.update", {
      qr: "qr-that-must-not-replace-pairing-code",
    });
    await nextTurn();
    await nextTurn();
    assert.equal(
      harness.capture.frames.filter(
        (frame) => frame.event === "link_challenge",
      ).length,
      challengeCount,
    );

    await harness.manager.handleCommand(
      command("logout_if_linked", "logout-1", "session-a", 1),
    );
    assert.equal(socket.logoutCalls, 0);
    assert.equal(socket.endCalls, 1);
  });

  it("logs out QR-linked auth even when Python has not received linked status", async () => {
    const harness = makeHarness();
    const socket = await startFreshSession(harness, "session-a", 1);
    await persistLinkedAuthWithoutStatus(harness, socket);

    await harness.manager.handleCommand(
      command(
        "logout_if_linked",
        "logout-before-linked-status",
        "session-a",
        1,
      ),
    );
    const response = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "logout-before-linked-status",
    );

    assert.equal(response.success, true);
    assert.equal(socket.logoutCalls, 1);
    assert.equal(socket.endCalls, 0);
  });

  it("reports logout uncertain when linked auth is between reconnect sockets", async () => {
    let markDelayStarted: () => void = () => undefined;
    const delayStarted = new Promise<void>((resolve) => {
      markDelayStarted = resolve;
    });
    const harness = makeHarness({
      delay: (_milliseconds, signal) => {
        markDelayStarted();
        if (signal.aborted) {
          return Promise.resolve();
        }
        return new Promise((resolve) => {
          signal.addEventListener("abort", () => resolve(), { once: true });
        });
      },
    });
    const socket = await startFreshSession(harness, "session-a", 1);
    markLinked(socket);
    const beforeClose = harness.capture.frames.length;
    socket.ev.emit("connection.update", {
      connection: "close",
      lastDisconnect: {
        error: {
          output: {
            statusCode: DisconnectReason.connectionLost,
          },
        },
      },
    });
    const reconnecting = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "session_status" &&
        expectRecord(frame.payload, "invalid_payload").state === "reconnecting",
      beforeClose,
    );
    await acknowledgeRpc(harness, reconnecting, {});
    await delayStarted;

    await harness.manager.handleCommand(
      command(
        "logout_if_linked",
        "logout-without-live-socket",
        "session-a",
        1,
      ),
    );
    const response = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "logout-without-live-socket",
    );
    assert.equal(response.success, false);
    assert.deepStrictEqual(response.error, {
      code: "logout_uncertain",
      retryable: true,
      ambiguous: false,
    });
    assert.equal(socket.logoutCalls, 0);

    await harness.manager.handleCommand(
      command(
        "logout_if_linked",
        "logout-without-live-socket-retry",
        "session-a",
        1,
      ),
    );
    const retry = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "logout-without-live-socket-retry",
    );
    assert.deepStrictEqual(retry.error, response.error);
  });

  it("replays lifecycle retries with new request IDs without upgrading a stop to logout", async () => {
    const harness = makeHarness();
    const socket = await startFreshSession(harness, "session-a", 1);

    await harness.manager.handleCommand(
      command("stop_session", "stop-first", "session-a", 1),
    );
    await harness.manager.handleCommand(
      command("stop_session", "stop-retry", "session-a", 1),
    );
    const stopRetry = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "stop-retry",
    );
    assert.equal(stopRetry.success, true);
    assert.equal(socket.endCalls, 1);

    await harness.manager.handleCommand(
      command("logout_if_linked", "logout-after-stop", "session-a", 1),
    );
    const logoutAfterStop = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "logout-after-stop",
    );
    assert.equal(logoutAfterStop.success, false);
    assert.deepStrictEqual(logoutAfterStop.error, {
      code: "logout_uncertain",
      retryable: true,
      ambiguous: false,
    });
    assert.equal(socket.logoutCalls, 0);

    await harness.manager.handleCommand(
      command("stop_session", "wrong-generation", "session-a", 2),
    );
    const wrongGeneration = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "wrong-generation",
    );
    assert.equal(wrongGeneration.success, false);
    assert.deepStrictEqual(wrongGeneration.error, {
      code: "session_not_found",
      retryable: false,
      ambiguous: false,
    });
  });

  it("replays a confirmed logout without invoking WhatsApp twice", async () => {
    const harness = makeHarness();
    const socket = await startFreshSession(harness, "session-a", 1);
    markLinked(socket);

    await harness.manager.handleCommand(
      command("logout_if_linked", "logout-first", "session-a", 1),
    );
    await harness.manager.handleCommand(
      command("logout_if_linked", "logout-retry", "session-a", 1),
    );
    const retry = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "logout-retry",
    );
    assert.equal(retry.success, true);
    assert.equal(socket.logoutCalls, 1);

    await harness.manager.handleCommand(
      command("stop_session", "stop-after-logout", "session-a", 1),
    );
    const stop = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "stop-after-logout",
    );
    assert.equal(stop.success, true);
    assert.equal(socket.endCalls, 0);
  });

  it("removes a locally closed session when remote logout is uncertain", async () => {
    const harness = makeHarness();
    const socket = await startFreshSession(harness, "session-a", 1);
    markLinked(socket);
    socket.logoutBehavior = () => Promise.reject(new Error("provider failure"));

    await harness.manager.handleCommand(
      command("logout_if_linked", "logout-uncertain", "session-a", 1),
    );
    const response = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "logout-uncertain",
    );
    assert.equal(response.success, false);
    assert.deepStrictEqual(response.error, {
      code: "logout_uncertain",
      retryable: true,
      ambiguous: false,
    });
    assert.equal(socket.logoutCalls, 1);
    assert.equal(socket.endCalls, 1);

    await harness.manager.handleCommand(
      command("logout_if_linked", "logout-uncertain-retry", "session-a", 1),
    );
    const retry = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "logout-uncertain-retry",
    );
    assert.equal(retry.success, false);
    assert.deepStrictEqual(retry.error, response.error);
    assert.equal(socket.logoutCalls, 1);

    await harness.manager.handleCommand(
      command("session_status", "status-after-logout", "session-a", 1),
    );
    const status = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "status-after-logout",
    );
    assert.equal(status.success, false);
    assert.deepStrictEqual(status.error, {
      code: "session_not_found",
      retryable: false,
      ambiguous: false,
    });
  });

  it("never reports logout success when shutdown starts the local stop first", async () => {
    const harness = makeHarness();
    const socket = await startFreshSession(harness, "session-a", 1);
    markLinked(socket);

    const logout = harness.manager.handleCommand(
      command("logout_if_linked", "logout-during-shutdown", "session-a", 1),
    );
    const shutdown = harness.manager.abort();
    await Promise.all([logout, shutdown]);

    const response = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" &&
        frame.request_id === "logout-during-shutdown",
    );
    assert.equal(response.success, false);
    assert.deepStrictEqual(response.error, {
      code: "logout_uncertain",
      retryable: true,
      ambiguous: false,
    });
    assert.equal(socket.logoutCalls, 0);
    assert.equal(socket.endCalls, 1);
  });

  it("marks an unacknowledged send ambiguous and never retries it", async () => {
    const harness = makeHarness();
    const socket = await startFreshSession(harness, "session-a", 1);
    socket.ev.emit("connection.update", { connection: "open" });
    const connected = await harness.capture.waitFor(
      (frame) =>
        frame.event === "session_status" &&
        expectRecord(frame.payload, "invalid_payload").state === "connected",
    );
    await acknowledgeRpc(harness, connected, {});
    socket.sendBehavior = () => new Promise(() => undefined);
    const send = command("send_text", "send-1", "session-a", 1, {
      to: "447700900002@s.whatsapp.net",
      text: "Reply once",
      timeout_ms: 1_000,
    });

    const responsePromise = harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "response" && frame.request_id === "send-1",
    );
    await harness.manager.handleCommand(send);
    const response = await responsePromise;
    assert.equal(response.success, false);
    assert.deepStrictEqual(response.error, {
      code: "send_ambiguous",
      retryable: false,
      ambiguous: true,
    });

    await harness.manager.handleCommand(send);
    assert.equal(socket.sendCalls, 1);
    await harness.manager.abort();
  });
});

describe("disconnect classification", () => {
  it("separates relink failures while keeping socket closures recoverable", () => {
    assert.deepStrictEqual(
      classifyDisconnect({ output: { statusCode: DisconnectReason.connectionLost } }),
      {
        classification: "transient_reconnect",
        failureCode: "connection_lost",
      },
    );
    assert.deepStrictEqual(
      classifyDisconnect({ output: { statusCode: DisconnectReason.restartRequired } }),
      {
        classification: "restart_required",
        failureCode: "restart_required",
      },
    );
    assert.deepStrictEqual(
      classifyDisconnect({ output: { statusCode: DisconnectReason.loggedOut } }),
      {
        classification: "needs_relink",
        failureCode: "logged_out",
      },
    );
    assert.deepStrictEqual(
      classifyDisconnect({ output: { statusCode: DisconnectReason.connectionReplaced } }),
      {
        classification: "transient_reconnect",
        failureCode: "connection_replaced",
      },
    );
    assert.deepStrictEqual(classifyDisconnect(new Error("unknown")), {
      classification: "transient_reconnect",
      failureCode: "unknown_disconnect",
    });
  });

  it("reports a safe disconnect code in reconnecting status events", async () => {
    const harness = makeHarness({ delay: async () => undefined });
    const socket = await startFreshSession(harness, "session-a", 1);
    const beforeClose = harness.capture.frames.length;

    socket.ev.emit("connection.update", {
      connection: "close",
      lastDisconnect: {
        error: {
          output: {
            statusCode: DisconnectReason.connectionReplaced,
          },
        },
      },
    });

    const reconnecting = await harness.capture.waitFor(
      (frame) =>
        frame.frame_type === "event" &&
        frame.event === "session_status" &&
        expectRecord(frame.payload, "invalid_payload").state === "reconnecting",
      beforeClose,
    );
    const payload = expectRecord(reconnecting.payload, "invalid_payload");
    assert.equal(payload.classification, "transient_reconnect");
    assert.equal(payload.failure_code, "connection_replaced");
    await acknowledgeRpc(harness, reconnecting, {});
    await harness.manager.abort();
  });
});

function makeHarness(
  options: {
    readonly delay?: Delay;
    readonly durableRetryDelaysMs?: readonly number[];
    readonly rpcTimeoutMs?: number;
  } = {},
): Harness {
  const capture = new CaptureWritable();
  const writer = new JsonLineWriter(capture);
  const rpc = new BridgeRpcPeer(
    writer,
    options.rpcTimeoutMs ?? 2_000,
    options.durableRetryDelaysMs ?? [0, 0],
  );
  const sockets: FakeSocket[] = [];
  const factory: SocketFactory = (auth) => {
    const socket = new FakeSocket(auth);
    sockets.push(socket);
    return socket.asSocket();
  };
  return {
    capture,
    manager: new SessionManager(
      writer,
      rpc,
      factory,
      options.delay ?? (() => Promise.resolve()),
    ),
    sockets,
  };
}

async function startFreshSession(
  harness: Harness,
  sessionHandle: string,
  generation: number,
): Promise<FakeSocket> {
  const requestId = `start-${sessionHandle}`;
  const before = harness.capture.frames.length;
  const start = harness.manager.handleCommand(
    command("start_session", requestId, sessionHandle, generation),
  );
  const persistence = await harness.capture.waitFor(
    (frame) =>
      frame.frame_type === "event" &&
      frame.event === "auth_set" &&
      frame.session_handle === sessionHandle &&
      authWriteCategory(frame) === "credentials",
    before,
  );
  await acknowledgeRpc(harness, persistence, {});
  await start;
  const response = await harness.capture.waitFor(
    (frame) =>
      frame.frame_type === "response" &&
      frame.request_id === requestId &&
      frame.success === true,
    before,
  );
  const payload = expectRecord(response.payload, "invalid_payload");
  assert.equal(payload.registered, false);
  assert.equal(payload.linked, false);
  const socket = harness.sockets.at(-1);
  assert.ok(socket);
  return socket;
}

async function acknowledgeRpc(
  harness: Harness,
  event: CapturedFrame,
  result: Record<string, unknown>,
): Promise<void> {
  const sessionHandle = event.session_handle;
  const generation = event.generation;
  const requestId = event.request_id;
  if (
    typeof sessionHandle !== "string" ||
    typeof generation !== "number" ||
    typeof requestId !== "string"
  ) {
    throw new Error("invalid RPC event");
  }
  await harness.manager.handleCommand(
    command(
      "rpc_result",
      `ack-${requestId}`,
      sessionHandle,
      generation,
      {
        rpc_request_id: requestId,
        success: true,
        result,
      },
    ),
  );
}

async function rejectRpc(
  harness: Harness,
  event: CapturedFrame,
  errorCode: string,
): Promise<void> {
  const sessionHandle = event.session_handle;
  const generation = event.generation;
  const requestId = event.request_id;
  if (
    typeof sessionHandle !== "string" ||
    typeof generation !== "number" ||
    typeof requestId !== "string"
  ) {
    throw new Error("invalid RPC event");
  }
  await harness.manager.handleCommand(
    command(
      "rpc_result",
      `reject-${requestId}`,
      sessionHandle,
      generation,
      {
        rpc_request_id: requestId,
        success: false,
        error_code: errorCode,
      },
    ),
  );
}

function markLinked(socket: FakeSocket): void {
  Object.assign(socket.auth.creds, {
    account: {},
    me: { id: "447700900001@s.whatsapp.net" },
    registered: false,
  });
}

async function persistLinkedAuthWithoutStatus(
  harness: Harness,
  socket: FakeSocket,
): Promise<void> {
  const beforeUpdate = harness.capture.frames.length;
  socket.ev.emit("creds.update", {
    account: {},
    me: { id: "447700900001@s.whatsapp.net" },
    registered: false,
  });
  const persistence = await harness.capture.waitFor(
    (frame) =>
      frame.frame_type === "event" &&
      frame.event === "auth_set" &&
      authWriteCategory(frame) === "credentials",
    beforeUpdate,
  );
  await acknowledgeRpc(harness, persistence, {});
  await nextTurn();
}

function command(
  name: CommandFrame["command"],
  requestId: string,
  sessionHandle: string,
  generation: number,
  payload: Record<string, unknown> = {},
): CommandFrame {
  return {
    protocol_version: PROTOCOL_VERSION,
    frame_type: "command",
    request_id: requestId,
    session_handle: sessionHandle,
    generation,
    command: name,
    payload,
  };
}

function authWriteCategory(frame: CapturedFrame): unknown {
  const payload = expectRecord(frame.payload, "invalid_payload");
  if (!Array.isArray(payload.writes) || payload.writes.length === 0) {
    return undefined;
  }
  return expectRecord(payload.writes[0], "invalid_write").category;
}

function authWriteCount(frame: CapturedFrame): number {
  const payload = expectRecord(frame.payload, "invalid_payload");
  if (!Array.isArray(payload.writes)) {
    throw new Error("invalid auth writes");
  }
  return payload.writes.length;
}

function message(options: {
  readonly id: string;
  readonly remoteJid: string;
  readonly remoteJidAlt?: string;
  readonly remoteJidUsername?: string;
  readonly participant?: string;
  readonly participantAlt?: string;
  readonly participantUsername?: string;
  readonly addressingMode?: string;
  readonly pushName?: string;
  readonly fromMe?: boolean;
  readonly content: Record<string, unknown>;
}): WAMessage {
  return {
    key: {
      id: options.id,
      remoteJid: options.remoteJid,
      ...(options.remoteJidAlt === undefined
        ? {}
        : { remoteJidAlt: options.remoteJidAlt }),
      ...(options.remoteJidUsername === undefined
        ? {}
        : { remoteJidUsername: options.remoteJidUsername }),
      ...(options.participant === undefined
        ? {}
        : { participant: options.participant }),
      ...(options.participantAlt === undefined
        ? {}
        : { participantAlt: options.participantAlt }),
      ...(options.participantUsername === undefined
        ? {}
        : { participantUsername: options.participantUsername }),
      ...(options.addressingMode === undefined
        ? {}
        : { addressingMode: options.addressingMode }),
      fromMe: options.fromMe ?? false,
    },
    message: options.content,
    messageTimestamp: 1_752_999_999,
    ...(options.pushName === undefined ? {} : { pushName: options.pushName }),
  };
}

function nextTurn(): Promise<void> {
  return new Promise((resolve) => {
    setImmediate(resolve);
  });
}
