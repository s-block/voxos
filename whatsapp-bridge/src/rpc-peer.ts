import { randomUUID } from "node:crypto";

import {
  BridgeError,
  type CommandFrame,
  PROTOCOL_VERSION,
  expectRecord,
  readBoolean,
  readString,
} from "./protocol.js";
import type { JsonLineWriter } from "./protocol.js";

const MAX_PENDING_REQUESTS = 1024;
const MAX_PENDING_DURABLE_REQUESTS = 512;
const MAX_COMPLETED_REQUESTS = 2048;
const DEFAULT_RPC_TIMEOUT_MS = 20_000;
const MIN_RPC_TIMEOUT_MS = 1_000;
const MAX_RPC_TIMEOUT_MS = 60_000;
const DEFAULT_DURABLE_RETRY_DELAYS_MS = [
  250,
  1_000,
  2_000,
  5_000,
  10_000,
  30_000,
] as const;
const DEFAULT_DURABLE_EVENT_QUEUE_CAPACITY = 64;
const RETRYABLE_REMOTE_ERROR_CODES = new Set(["persistence_failed"]);

type RequestLane = "auth" | "durable";

interface PendingRequest {
  readonly sessionHandle: string;
  readonly generation: number;
  readonly lane: RequestLane;
  readonly resolve: (value: unknown) => void;
  readonly reject: (error: BridgeError) => void;
  readonly timeout: NodeJS.Timeout;
  readonly signal: AbortSignal | null;
  readonly onAbort: (() => void) | null;
}

interface CompletedRequest {
  readonly sessionHandle: string;
  readonly generation: number;
}

interface DurableEventQueueItem {
  readonly event: string;
  readonly payload: Record<string, unknown>;
  readonly resolve: () => void;
  readonly reject: (error: BridgeError) => void;
}

export class BridgeRpcPeer {
  private readonly pending = new Map<string, PendingRequest>();
  private readonly completed = new Map<string, CompletedRequest>();
  private pendingDurableRequests = 0;

  constructor(
    private readonly writer: JsonLineWriter,
    private readonly timeoutMs: number = DEFAULT_RPC_TIMEOUT_MS,
    private readonly durableRetryDelaysMs: readonly number[] =
      DEFAULT_DURABLE_RETRY_DELAYS_MS,
  ) {}

  get requestTimeoutMs(): number {
    return this.timeoutMs;
  }

  async request(
    sessionHandle: string,
    generation: number,
    event: string,
    payload: Record<string, unknown>,
    options: {
      readonly lane?: RequestLane;
      readonly requestId?: string;
      readonly signal?: AbortSignal;
    } = {},
  ): Promise<unknown> {
    const lane = options.lane ?? "auth";
    if (options.signal?.aborted === true) {
      throw new BridgeError("session_stopped");
    }
    if (this.pending.size >= MAX_PENDING_REQUESTS) {
      throw new BridgeError("rpc_capacity_exceeded", { retryable: true });
    }
    if (
      lane === "durable" &&
      this.pendingDurableRequests >= MAX_PENDING_DURABLE_REQUESTS
    ) {
      throw new BridgeError("rpc_capacity_exceeded", { retryable: true });
    }

    const requestId = options.requestId ?? randomUUID();
    let resolvePromise: (value: unknown) => void = () => undefined;
    let rejectPromise: (error: BridgeError) => void = () => undefined;
    const result = new Promise<unknown>((resolve, reject) => {
      resolvePromise = resolve;
      rejectPromise = reject;
    });
    const timeout = setTimeout(() => {
      const pending = this.pending.get(requestId);
      if (pending === undefined) {
        return;
      }
      this.rejectPending(
        requestId,
        new BridgeError("rpc_timeout", { retryable: true }),
      );
    }, this.timeoutMs);
    timeout.unref();
    const signal = options.signal ?? null;
    const onAbort =
      signal === null
        ? null
        : (): void => {
            this.rejectPending(requestId, new BridgeError("session_stopped"));
          };
    this.pending.set(requestId, {
      sessionHandle,
      generation,
      lane,
      resolve: resolvePromise,
      reject: rejectPromise,
      timeout,
      signal,
      onAbort,
    });
    if (lane === "durable") {
      this.pendingDurableRequests += 1;
    }
    if (signal !== null && onAbort !== null) {
      signal.addEventListener("abort", onAbort, { once: true });
      if (signal.aborted) {
        this.rejectPending(requestId, new BridgeError("session_stopped"));
      }
    }

    try {
      if (this.pending.has(requestId)) {
        await this.writer.write({
          protocol_version: PROTOCOL_VERSION,
          frame_type: "event",
          request_id: requestId,
          session_handle: sessionHandle,
          generation,
          event,
          payload,
        });
      }
    } catch {
      this.rejectPending(requestId, new BridgeError("ipc_write_failed", { retryable: true }));
    }
    return result;
  }

  async requestDurable(
    sessionHandle: string,
    generation: number,
    event: string,
    payload: Record<string, unknown>,
    signal: AbortSignal,
  ): Promise<void> {
    const requestId = randomUUID();
    let retryAttempt = 0;
    for (;;) {
      try {
        await this.request(sessionHandle, generation, event, payload, {
          lane: "durable",
          requestId,
          signal,
        });
        return;
      } catch (error) {
        const bridgeError =
          error instanceof BridgeError
            ? error
            : new BridgeError("remote_persistence_failed", {
                retryable: true,
              });
        if (!bridgeError.retryable || signal.aborted) {
          throw bridgeError;
        }
        const retryDelay = durableRetryDelay(
          this.durableRetryDelaysMs,
          retryAttempt,
        );
        retryAttempt += 1;
        await waitForRetry(retryDelay, signal);
      }
    }
  }

  resolveResult(command: CommandFrame): void {
    const rpcRequestId = readString(
      command.payload.rpc_request_id,
      "invalid_rpc_request_id",
      { maxLength: 128, pattern: /^[A-Za-z0-9._:-]+$/ },
    );
    const pending = this.pending.get(rpcRequestId);
    if (pending === undefined) {
      const completed = this.completed.get(rpcRequestId);
      if (
        completed?.sessionHandle === command.session_handle &&
        completed.generation === command.generation
      ) {
        // The Python side may repeat an acknowledgement after an at-least-once
        // event replay. The original result was already applied.
        return;
      }
      throw new BridgeError("unknown_rpc_request");
    }
    if (
      pending.sessionHandle !== command.session_handle ||
      pending.generation !== command.generation
    ) {
      throw new BridgeError("rpc_correlation_mismatch");
    }

    const success = readBoolean(command.payload.success, "invalid_rpc_result");
    const errorCode = success
      ? null
      : readString(command.payload.error_code, "invalid_rpc_error_code", {
          maxLength: 64,
          pattern: /^[a-z0-9_]+$/,
        });
    this.rememberCompleted(rpcRequestId, pending);
    this.removePending(rpcRequestId, pending);
    if (success) {
      pending.resolve(command.payload.result);
      return;
    }
    pending.reject(
      new BridgeError(errorCode ?? "remote_persistence_failed", {
        retryable:
          errorCode !== null && RETRYABLE_REMOTE_ERROR_CODES.has(errorCode),
      }),
    );
  }

  cancelSession(sessionHandle: string, generation: number): void {
    for (const [requestId, pending] of this.pending) {
      if (
        pending.sessionHandle === sessionHandle &&
        pending.generation === generation
      ) {
        this.rejectPending(requestId, new BridgeError("session_stopped"));
      }
    }
  }

  cancelAll(): void {
    for (const requestId of this.pending.keys()) {
      this.rejectPending(requestId, new BridgeError("bridge_stopped"));
    }
  }

  private rejectPending(requestId: string, error: BridgeError): void {
    const pending = this.pending.get(requestId);
    if (pending === undefined) {
      return;
    }
    this.removePending(requestId, pending);
    pending.reject(error);
  }

  private removePending(requestId: string, pending: PendingRequest): void {
    this.pending.delete(requestId);
    clearTimeout(pending.timeout);
    if (pending.signal !== null && pending.onAbort !== null) {
      pending.signal.removeEventListener("abort", pending.onAbort);
    }
    if (pending.lane === "durable") {
      this.pendingDurableRequests -= 1;
    }
  }

  private rememberCompleted(
    requestId: string,
    pending: PendingRequest,
  ): void {
    this.completed.delete(requestId);
    this.completed.set(requestId, {
      sessionHandle: pending.sessionHandle,
      generation: pending.generation,
    });
    if (this.completed.size > MAX_COMPLETED_REQUESTS) {
      const oldest = this.completed.keys().next().value;
      if (oldest !== undefined) {
        this.completed.delete(oldest);
      }
    }
  }
}

export class DurableEventQueue {
  private readonly items: DurableEventQueueItem[] = [];
  private worker: Promise<void> | null = null;
  private activeItem = false;
  private stopped = false;

  constructor(
    private readonly rpc: BridgeRpcPeer,
    private readonly sessionHandle: string,
    private readonly generation: number,
    private readonly signal: AbortSignal,
    private readonly capacity: number = DEFAULT_DURABLE_EVENT_QUEUE_CAPACITY,
  ) {}

  enqueue(
    event: string,
    payload: Record<string, unknown>,
  ): Promise<void> {
    if (this.stopped || this.signal.aborted) {
      return Promise.reject(new BridgeError("session_stopped"));
    }
    const depth = this.items.length + (this.activeItem ? 1 : 0);
    if (depth >= this.capacity) {
      return Promise.reject(
        new BridgeError("durable_event_capacity_exceeded"),
      );
    }
    const result = new Promise<void>((resolve, reject) => {
      this.items.push({
        event,
        payload,
        resolve,
        reject,
      });
    });
    this.ensureWorker();
    return result;
  }

  stop(): void {
    if (this.stopped) {
      return;
    }
    this.stopped = true;
    const error = new BridgeError("session_stopped");
    for (const item of this.items.splice(0)) {
      item.reject(error);
    }
  }

  async flush(): Promise<void> {
    if (this.worker !== null) {
      await this.worker;
    }
  }

  private ensureWorker(): void {
    if (this.worker !== null) {
      return;
    }
    const worker = this.drain();
    this.worker = worker;
    void worker.then(
      () => {
        this.completeWorker(worker);
      },
      () => {
        this.completeWorker(worker);
      },
    );
  }

  private completeWorker(worker: Promise<void>): void {
    if (this.worker !== worker) {
      return;
    }
    this.worker = null;
    if (!this.stopped && this.items.length > 0) {
      this.ensureWorker();
    }
  }

  private async drain(): Promise<void> {
    while (!this.stopped) {
      const item = this.items.shift();
      if (item === undefined) {
        return;
      }
      this.activeItem = true;
      try {
        await this.rpc.requestDurable(
          this.sessionHandle,
          this.generation,
          item.event,
          item.payload,
          this.signal,
        );
        item.resolve();
      } catch (error) {
        const bridgeError =
          error instanceof BridgeError
            ? error
            : new BridgeError("durable_event_delivery_failed");
        item.reject(bridgeError);
        if (!bridgeError.retryable || this.signal.aborted) {
          this.stop();
        }
      } finally {
        this.activeItem = false;
      }
    }
  }
}

export function readRpcValues(value: unknown): Record<string, unknown> {
  const result = expectRecord(value, "invalid_auth_get_result");
  return expectRecord(result.values, "invalid_auth_get_result");
}

export function rpcTimeoutFromEnvironment(value: string | undefined): number {
  if (value === undefined) {
    return DEFAULT_RPC_TIMEOUT_MS;
  }
  if (!/^[1-9][0-9]*$/.test(value)) {
    throw new BridgeError("invalid_rpc_timeout");
  }
  const timeoutMs = Number(value);
  if (
    !Number.isSafeInteger(timeoutMs) ||
    timeoutMs < MIN_RPC_TIMEOUT_MS ||
    timeoutMs > MAX_RPC_TIMEOUT_MS
  ) {
    throw new BridgeError("invalid_rpc_timeout");
  }
  return timeoutMs;
}

function durableRetryDelay(
  delays: readonly number[],
  attempt: number,
): number {
  if (delays.length === 0) {
    return DEFAULT_DURABLE_RETRY_DELAYS_MS.at(-1) ?? 30_000;
  }
  return delays[Math.min(attempt, delays.length - 1)] ?? 30_000;
}

function waitForRetry(
  milliseconds: number,
  signal: AbortSignal,
): Promise<void> {
  if (signal.aborted) {
    return Promise.reject(new BridgeError("session_stopped"));
  }
  if (milliseconds === 0) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const onAbort = (): void => {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      reject(new BridgeError("session_stopped"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    timer.unref();
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) {
      onAbort();
    }
  });
}
