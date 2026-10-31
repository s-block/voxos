import type { Readable, Writable } from "node:stream";

import { decodeTagged, encodeTagged, TaggedJsonError } from "./tagged-json.js";

export const PROTOCOL_VERSION = 2;
export const BRIDGE_VERSION = "0.1.0";
export const BAILEYS_VERSION = "7.0.0-rc14";
export const BRIDGE_SESSION_HANDLE = "bridge";
export const MAX_FRAME_BYTES = 1024 * 1024;
export const MAX_TEXT_BYTES = 16 * 1024;

const IDENTIFIER_PATTERN = /^[A-Za-z0-9._:-]+$/;
const MAX_IDENTIFIER_LENGTH = 128;

export type CommandName =
  | "handshake"
  | "request_challenge"
  | "rpc_result"
  | "send_text"
  | "session_status"
  | "shutdown"
  | "start_session"
  | "stop_session"
  | "logout_if_linked";

export interface CommandFrame {
  readonly protocol_version: typeof PROTOCOL_VERSION;
  readonly frame_type: "command";
  readonly request_id: string;
  readonly session_handle: string;
  readonly generation: number;
  readonly command: CommandName;
  readonly payload: Record<string, unknown>;
}

export interface ResponseFrame {
  readonly protocol_version: typeof PROTOCOL_VERSION;
  readonly frame_type: "response";
  readonly request_id: string;
  readonly session_handle: string;
  readonly generation: number;
  readonly success: boolean;
  readonly payload?: Record<string, unknown>;
  readonly error?: {
    readonly code: string;
    readonly retryable: boolean;
    readonly ambiguous: boolean;
  };
}

export interface EventFrame {
  readonly protocol_version: typeof PROTOCOL_VERSION;
  readonly frame_type: "event";
  readonly request_id: string;
  readonly session_handle: string;
  readonly generation: number;
  readonly event: string;
  readonly payload: Record<string, unknown>;
}

export type OutgoingFrame = EventFrame | ResponseFrame;

export class BridgeError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly ambiguous: boolean;

  constructor(
    code: string,
    options: {
      readonly retryable?: boolean;
      readonly ambiguous?: boolean;
    } = {},
  ) {
    super(code);
    this.name = "BridgeError";
    this.code = code;
    this.retryable = options.retryable ?? false;
    this.ambiguous = options.ambiguous ?? false;
  }
}

export class JsonLineWriter {
  private writeTail: Promise<void> = Promise.resolve();

  constructor(private readonly output: Writable) {}

  write(frame: OutgoingFrame): Promise<void> {
    const line = serializeFrame(frame);
    const operation = this.writeTail.then(() => writeChunk(this.output, `${line}\n`));
    this.writeTail = operation.catch(() => undefined);
    return operation;
  }

  flush(): Promise<void> {
    return this.writeTail;
  }
}

export function parseCommandLine(line: Buffer): CommandFrame {
  if (line.byteLength === 0) {
    throw new BridgeError("empty_frame");
  }
  if (line.byteLength > MAX_FRAME_BYTES) {
    throw new BridgeError("frame_too_large");
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(line.toString("utf8")) as unknown;
    parsed = decodeTagged(parsed);
  } catch (error) {
    if (error instanceof TaggedJsonError) {
      throw new BridgeError("invalid_tagged_json");
    }
    throw new BridgeError("invalid_json");
  }
  const frame = expectRecord(parsed, "invalid_frame");
  if (frame.protocol_version !== PROTOCOL_VERSION) {
    throw new BridgeError("unsupported_protocol_version");
  }
  if (frame.frame_type !== "command") {
    throw new BridgeError("invalid_frame_type");
  }
  const command = readCommand(frame.command);
  return {
    protocol_version: PROTOCOL_VERSION,
    frame_type: "command",
    request_id: readIdentifier(frame.request_id, "invalid_request_id"),
    session_handle: readIdentifier(frame.session_handle, "invalid_session_handle"),
    generation: readGeneration(frame.generation),
    command,
    payload: expectRecord(frame.payload, "invalid_payload"),
  };
}

export function serializeFrame(frame: OutgoingFrame): string {
  let serialized: string;
  try {
    serialized = JSON.stringify(encodeTagged(frame));
  } catch {
    throw new BridgeError("frame_encoding_failed");
  }
  if (Buffer.byteLength(serialized, "utf8") > MAX_FRAME_BYTES) {
    throw new BridgeError("frame_too_large");
  }
  return serialized;
}

export async function* readBoundedLines(
  input: Readable,
): AsyncGenerator<Buffer> {
  let pending: Buffer = Buffer.alloc(0);
  for await (const chunk of input) {
    const rawChunk: unknown = chunk;
    if (typeof rawChunk !== "string" && !(rawChunk instanceof Uint8Array)) {
      throw new BridgeError("invalid_stdin_chunk");
    }
    const bytes: Buffer =
      typeof rawChunk === "string"
        ? Buffer.from(rawChunk, "utf8")
        : Buffer.from(rawChunk);
    pending = pending.byteLength === 0 ? bytes : Buffer.concat([pending, bytes]);
    let newlineIndex = pending.indexOf(0x0a);
    while (newlineIndex >= 0) {
      if (newlineIndex > MAX_FRAME_BYTES) {
        throw new BridgeError("frame_too_large");
      }
      const lineEnd =
        newlineIndex > 0 && pending[newlineIndex - 1] === 0x0d
          ? newlineIndex - 1
          : newlineIndex;
      yield pending.subarray(0, lineEnd);
      pending = pending.subarray(newlineIndex + 1);
      newlineIndex = pending.indexOf(0x0a);
    }
    if (pending.byteLength > MAX_FRAME_BYTES) {
      throw new BridgeError("frame_too_large");
    }
  }
  if (pending.byteLength > 0) {
    yield pending;
  }
}

export function successResponse(
  command: CommandFrame,
  payload: Record<string, unknown> = {},
): ResponseFrame {
  return {
    protocol_version: PROTOCOL_VERSION,
    frame_type: "response",
    request_id: command.request_id,
    session_handle: command.session_handle,
    generation: command.generation,
    success: true,
    payload,
  };
}

export function errorResponse(command: CommandFrame, error: unknown): ResponseFrame {
  const safeError =
    error instanceof BridgeError ? error : new BridgeError("internal_bridge_error");
  return {
    protocol_version: PROTOCOL_VERSION,
    frame_type: "response",
    request_id: command.request_id,
    session_handle: command.session_handle,
    generation: command.generation,
    success: false,
    error: {
      code: safeError.code,
      retryable: safeError.retryable,
      ambiguous: safeError.ambiguous,
    },
  };
}

export function protocolErrorEvent(code: string): EventFrame {
  return {
    protocol_version: PROTOCOL_VERSION,
    frame_type: "event",
    request_id: "protocol-error",
    session_handle: BRIDGE_SESSION_HANDLE,
    generation: 0,
    event: "protocol_error",
    payload: { code },
  };
}

export function expectRecord(value: unknown, code: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new BridgeError(code);
  }
  return value as Record<string, unknown>;
}

export function readString(
  value: unknown,
  code: string,
  options: {
    readonly maxLength?: number;
    readonly minLength?: number;
    readonly pattern?: RegExp;
  } = {},
): string {
  if (typeof value !== "string") {
    throw new BridgeError(code);
  }
  const minLength = options.minLength ?? 1;
  const maxLength = options.maxLength ?? 4096;
  if (
    value.length < minLength ||
    value.length > maxLength ||
    (options.pattern !== undefined && !options.pattern.test(value))
  ) {
    throw new BridgeError(code);
  }
  return value;
}

export function readBoolean(value: unknown, code: string): boolean {
  if (typeof value !== "boolean") {
    throw new BridgeError(code);
  }
  return value;
}

export function readInteger(
  value: unknown,
  code: string,
  options: {
    readonly min?: number;
    readonly max?: number;
  } = {},
): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < (options.min ?? Number.MIN_SAFE_INTEGER) ||
    value > (options.max ?? Number.MAX_SAFE_INTEGER)
  ) {
    throw new BridgeError(code);
  }
  return value;
}

function readIdentifier(value: unknown, code: string): string {
  return readString(value, code, {
    maxLength: MAX_IDENTIFIER_LENGTH,
    pattern: IDENTIFIER_PATTERN,
  });
}

function readGeneration(value: unknown): number {
  return readInteger(value, "invalid_generation", { min: 0 });
}

function readCommand(value: unknown): CommandName {
  switch (value) {
    case "handshake":
    case "request_challenge":
    case "rpc_result":
    case "send_text":
    case "session_status":
    case "shutdown":
    case "start_session":
    case "stop_session":
    case "logout_if_linked":
      return value;
    default:
      throw new BridgeError("unknown_command");
  }
}

function writeChunk(output: Writable, chunk: string): Promise<void> {
  return new Promise((resolve, reject) => {
    output.write(chunk, (error: Error | null | undefined) => {
      if (error !== null && error !== undefined) {
        reject(error);
        return;
      }
      resolve();
    });
  });
}
