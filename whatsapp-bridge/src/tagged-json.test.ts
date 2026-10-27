import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { decodeTagged, encodeTagged, TaggedJsonError } from "./tagged-json.js";

describe("tagged JSON", () => {
  it("round-trips binary and non-JSON scalar values losslessly", () => {
    const original = {
      buffer: Buffer.from([0, 1, 2, 254, 255]),
      bytes: new Uint8Array([4, 5, 6]),
      count: 9_007_199_254_740_993n,
      date: new Date("2026-07-20T10:00:00.000Z"),
      missing: undefined,
      nested: [{ value: Buffer.from("signal-key") }],
    };

    const decoded = decodeTagged(
      JSON.parse(JSON.stringify(encodeTagged(original))) as unknown,
    );

    assert.deepStrictEqual(decoded, original);
  });

  it("rejects cycles, oversized binary, and malformed tags", () => {
    const cyclic: { self?: unknown } = {};
    cyclic.self = cyclic;

    assert.throws(() => encodeTagged(cyclic), TaggedJsonError);
    assert.throws(
      () => encodeTagged(Buffer.alloc(512 * 1024 + 1)),
      TaggedJsonError,
    );
    assert.throws(
      () =>
        decodeTagged({
          $voxos_type: "buffer",
          base64: "not/base64!",
        }),
      TaggedJsonError,
    );
  });
});
