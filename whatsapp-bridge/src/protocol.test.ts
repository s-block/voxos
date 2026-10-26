import assert from "node:assert/strict";
import { Readable, Writable } from "node:stream";
import { describe, it } from "node:test";

import {
  BridgeError,
  JsonLineWriter,
  MAX_FRAME_BYTES,
  PROTOCOL_VERSION,
  parseCommandLine,
  readBoundedLines,
} from "./protocol.js";
import type { CommandFrame } from "./protocol.js";
import { BridgeRpcPeer, rpcTimeoutFromEnvironment } from "./rpc-peer.js";

describe("bridge protocol", () => {
  it("validates the allowlisted RPC timeout environment value", () => {
    assert.equal(rpcTimeoutFromEnvironment(undefined), 20_000);
    assert.equal(rpcTimeoutFromEnvironment("12345"), 12_345);
    assert.throws(
      () => rpcTimeoutFromEnvironment("0"),
      /invalid_rpc_timeout/,
    );
    assert.throws(
      () => rpcTimeoutFromEnvironment("60001"),
      /invalid_rpc_timeout/,
    );
  });

  it("accepts a duplicate result for a completed durable event", async () => {
    const writer = new JsonLineWriter(
      new Writable({
        write(_chunk, _encoding, callback): void {
          callback();
        },
      }),
    );
    const rpc = new BridgeRpcPeer(writer);
    const request = rpc.request(
      "session-1",
      7,
      "session_status",
      { state: "connecting" },
      { lane: "durable", requestId: "durable-event-1" },
    );
    const result: CommandFrame = {
      protocol_version: PROTOCOL_VERSION,
      frame_type: "command" as const,
      request_id: "result-command-1",
      session_handle: "session-1",
      generation: 7,
      command: "rpc_result" as const,
      payload: {
        rpc_request_id: "durable-event-1",
        success: true,
        result: {},
      },
    };

    rpc.resolveResult(result);
    rpc.resolveResult({ ...result, request_id: "result-command-2" });

    await request;
  });

  it("validates a versioned command frame", () => {
    const frame = parseCommandLine(
      Buffer.from(
        JSON.stringify({
          protocol_version: PROTOCOL_VERSION,
          frame_type: "command",
          request_id: "request-1",
          session_handle: "session-1",
          generation: 7,
          command: "session_status",
          payload: {},
        }),
      ),
    );

    assert.equal(frame.command, "session_status");
    assert.equal(frame.generation, 7);

    const logout = parseCommandLine(
      Buffer.from(
        JSON.stringify({
          ...frame,
          request_id: "request-logout",
          command: "logout_if_linked",
        }),
      ),
    );
    assert.equal(logout.command, "logout_if_linked");
  });

  it("rejects unsupported versions and unknown commands", () => {
    const base = {
      protocol_version: PROTOCOL_VERSION,
      frame_type: "command",
      request_id: "request-1",
      session_handle: "session-1",
      generation: 1,
      command: "session_status",
      payload: {},
    };

    assert.throws(
      () =>
        parseCommandLine(
          Buffer.from(JSON.stringify({ ...base, protocol_version: 1 })),
        ),
      (error: unknown) =>
        error instanceof BridgeError &&
        error.code === "unsupported_protocol_version",
    );
    assert.throws(
      () =>
        parseCommandLine(
          Buffer.from(JSON.stringify({ ...base, command: "delete_everything" })),
        ),
      (error: unknown) =>
        error instanceof BridgeError && error.code === "unknown_command",
    );
  });

  it("bounds stdin frames before yielding them", async () => {
    const lines: Buffer[] = [];
    for await (const line of readBoundedLines(
      Readable.from([Buffer.from("{}\n{}\r\n")]),
    )) {
      lines.push(line);
    }
    assert.deepStrictEqual(
      lines.map((line) => line.toString("utf8")),
      ["{}", "{}"],
    );

    await assert.rejects(
      async () => {
        for await (const line of readBoundedLines(
          Readable.from([Buffer.alloc(MAX_FRAME_BYTES + 1, 0x61)]),
        )) {
          assert.fail(
            `oversized frame was yielded: ${String(line.byteLength)}`,
          );
        }
      },
      (error: unknown) =>
        error instanceof BridgeError && error.code === "frame_too_large",
    );
  });
});
