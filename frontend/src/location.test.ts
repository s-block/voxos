import { describe, expect, it, vi } from "vitest";
import {
  BrowserLocationSharing,
  LocationCaptureError,
  locationSharingStorageKey,
} from "./location";

describe("BrowserLocationSharing", () => {
  it("requires an explicit enable and reuses only a fresh in-memory snapshot", async () => {
    const getCurrentPosition = vi.fn(
      (success: PositionCallback) =>
        success(positionAt(Date.parse("2026-08-30T12:00:00.000Z"))),
    );
    const storage = memoryStorage();
    const sharing = new BrowserLocationSharing("location-key", {
      geolocation: { getCurrentPosition },
      now: () => Date.parse("2026-08-30T12:01:00.000Z"),
      storage,
    });

    expect(await sharing.snapshotForAction()).toBeNull();
    const enabledLocation = await sharing.enable();
    const actionLocation = await sharing.snapshotForAction();

    expect(sharing.isEnabled).toBe(true);
    expect(storage.getItem("location-key")).toBe("true");
    expect(actionLocation).toEqual(enabledLocation);
    expect(getCurrentPosition).toHaveBeenCalledTimes(1);
  });

  it("turns sharing off when foreground permission is revoked", async () => {
    const storage = memoryStorage({ "location-key": "true" });
    const sharing = new BrowserLocationSharing("location-key", {
      geolocation: {
        getCurrentPosition: (_success, error) =>
          error?.({ code: 1, message: "denied" } as GeolocationPositionError),
      },
      storage,
    });

    expect(await sharing.snapshotForAction()).toBeNull();
    expect(sharing.isEnabled).toBe(false);
    expect(sharing.lastError).toBeInstanceOf(LocationCaptureError);
    expect(sharing.lastError?.code).toBe("denied");
    expect(storage.getItem("location-key")).toBeNull();
  });

  it("scopes the opt-in setting to the server and signed-in user", () => {
    expect(
      locationSharingStorageKey("https://api.example.test", "user-123"),
    ).toContain("user-123");
    expect(
      locationSharingStorageKey("https://api.example.test", "user-123"),
    ).not.toBe(
      locationSharingStorageKey("https://api.example.test", "user-456"),
    );
  });
});

function positionAt(timestamp: number): GeolocationPosition {
  return {
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
    timestamp,
    toJSON: () => ({}),
  };
}

function memoryStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => values.get(key) ?? null,
    removeItem: (key: string) => {
      values.delete(key);
    },
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };
}
