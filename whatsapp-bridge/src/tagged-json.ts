const TAG_FIELD = "$voxos_type";
const MAX_NESTING_DEPTH = 64;
const MAX_BINARY_BYTES = 512 * 1024;
const DANGEROUS_KEYS = new Set(["__proto__", "constructor", "prototype"]);
const BASE64_PATTERN = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

type TaggedObject =
  | { readonly $voxos_type: "bigint"; readonly decimal: string }
  | { readonly $voxos_type: "buffer"; readonly base64: string }
  | { readonly $voxos_type: "date"; readonly iso: string }
  | { readonly $voxos_type: "uint8array"; readonly base64: string }
  | { readonly $voxos_type: "undefined" };

export class TaggedJsonError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TaggedJsonError";
  }
}

export function encodeTagged(value: unknown): unknown {
  return encodeValue(value, 0, new WeakSet<object>());
}

export function decodeTagged(value: unknown): unknown {
  return decodeValue(value, 0);
}

function encodeValue(value: unknown, depth: number, ancestors: WeakSet<object>): unknown {
  assertDepth(depth);
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new TaggedJsonError("Non-finite numbers are not supported");
    }
    return value;
  }
  if (typeof value === "undefined") {
    return { [TAG_FIELD]: "undefined" } satisfies TaggedObject;
  }
  if (typeof value === "bigint") {
    return {
      [TAG_FIELD]: "bigint",
      decimal: value.toString(10),
    } satisfies TaggedObject;
  }
  if (typeof value === "function" || typeof value === "symbol") {
    throw new TaggedJsonError("Unsupported value type");
  }
  if (Buffer.isBuffer(value)) {
    assertBinarySize(value.byteLength);
    return {
      [TAG_FIELD]: "buffer",
      base64: value.toString("base64"),
    } satisfies TaggedObject;
  }
  if (value instanceof Uint8Array) {
    assertBinarySize(value.byteLength);
    return {
      [TAG_FIELD]: "uint8array",
      base64: Buffer.from(value).toString("base64"),
    } satisfies TaggedObject;
  }
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) {
      throw new TaggedJsonError("Invalid dates are not supported");
    }
    return {
      [TAG_FIELD]: "date",
      iso: value.toISOString(),
    } satisfies TaggedObject;
  }

  if (ancestors.has(value)) {
    throw new TaggedJsonError("Cyclic values are not supported");
  }
  ancestors.add(value);
  try {
    if (Array.isArray(value)) {
      return value.map((item) => encodeValue(item, depth + 1, ancestors));
    }
    const output: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) {
      assertSafeKey(key);
      output[key] = encodeValue(item, depth + 1, ancestors);
    }
    return output;
  } finally {
    ancestors.delete(value);
  }
}

function decodeValue(value: unknown, depth: number): unknown {
  assertDepth(depth);
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    typeof value === "number"
  ) {
    if (typeof value === "number" && !Number.isFinite(value)) {
      throw new TaggedJsonError("Non-finite numbers are not supported");
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => decodeValue(item, depth + 1));
  }
  if (!isRecord(value)) {
    throw new TaggedJsonError("Invalid JSON value");
  }

  const tag = value[TAG_FIELD];
  if (tag !== undefined) {
    return decodeTaggedObject(value, tag);
  }

  const output: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    assertSafeKey(key);
    output[key] = decodeValue(item, depth + 1);
  }
  return output;
}

function decodeTaggedObject(value: Record<string, unknown>, tag: unknown): unknown {
  if (typeof tag !== "string") {
    throw new TaggedJsonError("Invalid tagged value");
  }
  switch (tag) {
    case "undefined":
      assertExactKeys(value, [TAG_FIELD]);
      return undefined;
    case "bigint": {
      assertExactKeys(value, [TAG_FIELD, "decimal"]);
      const decimal = value.decimal;
      if (typeof decimal !== "string" || !/^-?(?:0|[1-9][0-9]*)$/.test(decimal)) {
        throw new TaggedJsonError("Invalid bigint value");
      }
      return BigInt(decimal);
    }
    case "buffer":
      assertExactKeys(value, [TAG_FIELD, "base64"]);
      return Buffer.from(readCanonicalBase64(value.base64), "base64");
    case "uint8array":
      assertExactKeys(value, [TAG_FIELD, "base64"]);
      return new Uint8Array(Buffer.from(readCanonicalBase64(value.base64), "base64"));
    case "date": {
      assertExactKeys(value, [TAG_FIELD, "iso"]);
      if (typeof value.iso !== "string") {
        throw new TaggedJsonError("Invalid date value");
      }
      const date = new Date(value.iso);
      if (Number.isNaN(date.getTime()) || date.toISOString() !== value.iso) {
        throw new TaggedJsonError("Invalid date value");
      }
      return date;
    }
    default:
      throw new TaggedJsonError("Unknown tagged value");
  }
}

function readCanonicalBase64(value: unknown): string {
  if (
    typeof value !== "string" ||
    !BASE64_PATTERN.test(value) ||
    value.length > Math.ceil(MAX_BINARY_BYTES / 3) * 4
  ) {
    throw new TaggedJsonError("Invalid binary value");
  }
  const decoded = Buffer.from(value, "base64");
  assertBinarySize(decoded.byteLength);
  if (decoded.toString("base64") !== value) {
    throw new TaggedJsonError("Binary value is not canonical base64");
  }
  return value;
}

function assertDepth(depth: number): void {
  if (depth > MAX_NESTING_DEPTH) {
    throw new TaggedJsonError("Maximum nesting depth exceeded");
  }
}

function assertBinarySize(size: number): void {
  if (size > MAX_BINARY_BYTES) {
    throw new TaggedJsonError("Binary value exceeds the size limit");
  }
}

function assertSafeKey(key: string): void {
  if (DANGEROUS_KEYS.has(key)) {
    throw new TaggedJsonError("Unsafe object key");
  }
}

function assertExactKeys(value: Record<string, unknown>, expected: readonly string[]): void {
  const keys = Object.keys(value).sort();
  const expectedKeys = [...expected].sort();
  if (
    keys.length !== expectedKeys.length ||
    keys.some((key, index) => key !== expectedKeys[index])
  ) {
    throw new TaggedJsonError("Invalid tagged value shape");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
