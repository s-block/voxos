import {
  initAuthCreds,
  proto,
  type AuthenticationCreds,
  type AuthenticationState,
  type SignalDataSet,
  type SignalDataTypeMap,
  type SignalKeyStore,
} from "baileys";

import { BridgeError, expectRecord, readInteger, readString } from "./protocol.js";
import { readRpcValues } from "./rpc-peer.js";
import type { BridgeRpcPeer } from "./rpc-peer.js";

const CREDENTIALS_CATEGORY = "credentials";
const CREDENTIALS_LOGICAL_ID = "credentials";
const MAX_KEY_IDS_PER_READ_RPC = 512;
const MAX_KEY_IDS_PER_OPERATION = 2048;
const MAX_KEY_ID_LENGTH = 512;
const KEY_CATEGORIES = new Set<keyof SignalDataTypeMap>([
  "app-state-sync-key",
  "app-state-sync-version",
  "device-list",
  "identity-key",
  "lid-mapping",
  "pre-key",
  "sender-key",
  "sender-key-memory",
  "session",
  "tctoken",
]);

export class RemoteAuthStore {
  readonly state: AuthenticationState;

  private persistenceTail: Promise<void> = Promise.resolve();
  private persistenceFailure: BridgeError | null = null;

  private constructor(
    private readonly rpc: BridgeRpcPeer,
    private readonly sessionHandle: string,
    private readonly generation: number,
    creds: AuthenticationCreds,
  ) {
    this.state = {
      creds,
      keys: this.makeKeyStore(),
    };
  }

  static async create(
    rpc: BridgeRpcPeer,
    sessionHandle: string,
    generation: number,
    serializedCredentials: unknown,
  ): Promise<RemoteAuthStore> {
    const isFresh =
      serializedCredentials === null || serializedCredentials === undefined;
    const credentials = isFresh
      ? initAuthCreds()
      : validateAuthenticationCredentials(serializedCredentials);
    const store = new RemoteAuthStore(
      rpc,
      sessionHandle,
      generation,
      credentials,
    );
    if (isFresh) {
      await store.persistCredentials();
    }
    return store;
  }

  persistCredentials(): Promise<void> {
    return this.enqueuePersistence(async () => {
      await this.rpc.request(
        this.sessionHandle,
        this.generation,
        "auth_set",
        {
          writes: [
            {
              category: CREDENTIALS_CATEGORY,
              logical_id: CREDENTIALS_LOGICAL_ID,
              value: this.state.creds,
            },
          ],
        },
      );
    });
  }

  async flush(): Promise<void> {
    await this.persistenceTail;
    this.assertHealthy();
  }

  private makeKeyStore(): SignalKeyStore {
    return {
      get: async <T extends keyof SignalDataTypeMap>(
        type: T,
        ids: string[],
      ): Promise<Record<string, SignalDataTypeMap[T]>> => {
        this.assertHealthy();
        await this.persistenceTail;
        this.assertHealthy();
        assertKeyCategory(type);
        const logicalIds = validateKeyIds(ids);
        const output: Record<string, SignalDataTypeMap[T]> = {};
        for (const logicalIdBatch of batches(
          logicalIds,
          MAX_KEY_IDS_PER_READ_RPC,
        )) {
          const rawResult = await this.rpc.request(
            this.sessionHandle,
            this.generation,
            "auth_get",
            {
              category: type,
              logical_ids: logicalIdBatch,
            },
          );
          const values = readRpcValues(rawResult);
          for (const key of Object.keys(values)) {
            if (!logicalIdBatch.includes(key)) {
              throw new BridgeError("unexpected_auth_key");
            }
          }

          for (const id of logicalIdBatch) {
            let value = values[id];
            if (value === null || value === undefined) {
              continue;
            }
            if (type === "app-state-sync-key") {
              value = proto.Message.AppStateSyncKeyData.fromObject(
                expectRecord(value, "invalid_auth_key"),
              );
            }
            output[id] = value as SignalDataTypeMap[T];
          }
        }
        return output;
      },
      set: (data: SignalDataSet): Promise<void> =>
        this.enqueuePersistence(async () => {
          const writes = flattenKeyWrites(data);
          if (writes.length === 0) {
            return;
          }
          await this.rpc.request(
            this.sessionHandle,
            this.generation,
            "auth_set",
            { writes },
          );
        }),
    };
  }

  private enqueuePersistence(operation: () => Promise<void>): Promise<void> {
    this.assertHealthy();
    const result = this.persistenceTail.then(async () => {
      this.assertHealthy();
      await operation();
    });
    this.persistenceTail = result.catch((error: unknown) => {
      this.persistenceFailure =
        error instanceof BridgeError
          ? error
          : new BridgeError("auth_persistence_failed", { retryable: true });
    });
    return result;
  }

  private assertHealthy(): void {
    if (this.persistenceFailure !== null) {
      throw this.persistenceFailure;
    }
  }
}

interface KeyWrite {
  readonly category: keyof SignalDataTypeMap;
  readonly logical_id: string;
  readonly value: unknown;
}

function flattenKeyWrites(data: SignalDataSet): KeyWrite[] {
  const writes: KeyWrite[] = [];
  const rawData = data as Record<string, Record<string, unknown> | undefined>;
  for (const [rawCategory, values] of Object.entries(rawData)) {
    const category = assertKeyCategory(rawCategory);
    if (values === undefined) {
      continue;
    }
    const entries = Object.entries(values);
    if (writes.length + entries.length > MAX_KEY_IDS_PER_OPERATION) {
      throw new BridgeError("too_many_auth_keys");
    }
    for (const [id, value] of entries) {
      writes.push({
        category,
        logical_id: validateKeyId(id),
        value,
      });
    }
  }
  return writes;
}

function assertKeyCategory(value: string): keyof SignalDataTypeMap {
  if (!KEY_CATEGORIES.has(value as keyof SignalDataTypeMap)) {
    throw new BridgeError("invalid_auth_category");
  }
  return value as keyof SignalDataTypeMap;
}

function validateKeyIds(ids: readonly string[]): string[] {
  if (ids.length > MAX_KEY_IDS_PER_OPERATION) {
    throw new BridgeError("too_many_auth_keys");
  }
  return ids.map(validateKeyId);
}

function validateKeyId(id: string): string {
  return readString(id, "invalid_auth_key_id", {
    maxLength: MAX_KEY_ID_LENGTH,
  });
}

function validateAuthenticationCredentials(value: unknown): AuthenticationCreds {
  const credentials = expectRecord(value, "invalid_credentials");
  validateKeyPair(credentials.noiseKey);
  validateKeyPair(credentials.pairingEphemeralKeyPair);
  validateKeyPair(credentials.signedIdentityKey);

  const signedPreKey = expectRecord(
    credentials.signedPreKey,
    "invalid_credentials",
  );
  validateKeyPair(signedPreKey.keyPair);
  assertBytes(signedPreKey.signature);
  readInteger(signedPreKey.keyId, "invalid_credentials", { min: 0 });
  readInteger(credentials.registrationId, "invalid_credentials", { min: 0 });
  readString(credentials.advSecretKey, "invalid_credentials", {
    maxLength: 4096,
  });
  readInteger(credentials.firstUnuploadedPreKeyId, "invalid_credentials", {
    min: 0,
  });
  readInteger(credentials.nextPreKeyId, "invalid_credentials", { min: 0 });
  readInteger(credentials.accountSyncCounter, "invalid_credentials", { min: 0 });
  if (!Array.isArray(credentials.processedHistoryMessages)) {
    throw new BridgeError("invalid_credentials");
  }
  const accountSettings = expectRecord(
    credentials.accountSettings,
    "invalid_credentials",
  );
  if (typeof accountSettings.unarchiveChats !== "boolean") {
    throw new BridgeError("invalid_credentials");
  }
  if (typeof credentials.registered !== "boolean") {
    throw new BridgeError("invalid_credentials");
  }
  if (credentials.me !== undefined) {
    const me = expectRecord(credentials.me, "invalid_credentials");
    readString(me.id, "invalid_credentials", { maxLength: 256 });
  }
  return credentials as AuthenticationCreds;
}

function validateKeyPair(value: unknown): void {
  const keyPair = expectRecord(value, "invalid_credentials");
  assertBytes(keyPair.public);
  assertBytes(keyPair.private);
}

function assertBytes(value: unknown): asserts value is Uint8Array {
  if (!(value instanceof Uint8Array)) {
    throw new BridgeError("invalid_credentials");
  }
}

function batches<T>(values: readonly T[], size: number): T[][] {
  const output: T[][] = [];
  for (let index = 0; index < values.length; index += size) {
    output.push(values.slice(index, index + size));
  }
  return output;
}
