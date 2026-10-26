export type AgentLocationSnapshot = {
  latitude: number;
  longitude: number;
  horizontal_accuracy_m: number;
  captured_at: string;
};

type LocationSharingDependencies = {
  geolocation?: Pick<Geolocation, "getCurrentPosition"> | null;
  now?: () => number;
  storage?: Pick<Storage, "getItem" | "removeItem" | "setItem"> | null;
};

const LOCATION_CACHE_MAX_AGE_MS = 5 * 60 * 1000;
const LOCATION_CAPTURE_TIMEOUT_MS = 6_000;

export class LocationCaptureError extends Error {
  constructor(
    message: string,
    readonly code: "denied" | "unavailable" | "unsupported",
  ) {
    super(message);
    this.name = "LocationCaptureError";
  }
}

export class BrowserLocationSharing {
  readonly storageKey: string;
  private readonly geolocation: Pick<Geolocation, "getCurrentPosition"> | null;
  private readonly now: () => number;
  private readonly storage: Pick<
    Storage,
    "getItem" | "removeItem" | "setItem"
  > | null;
  private cachedLocation: AgentLocationSnapshot | null = null;
  private enabled: boolean;
  private error: LocationCaptureError | null = null;

  constructor(storageKey: string, dependencies: LocationSharingDependencies = {}) {
    this.storageKey = storageKey;
    this.geolocation =
      dependencies.geolocation === undefined
        ? globalThis.navigator?.geolocation ?? null
        : dependencies.geolocation;
    this.now = dependencies.now ?? Date.now;
    this.storage =
      dependencies.storage === undefined ? browserStorage() : dependencies.storage;
    this.enabled = this.readEnabled();
  }

  get isEnabled(): boolean {
    return this.enabled;
  }

  get lastError(): LocationCaptureError | null {
    return this.error;
  }

  async enable(): Promise<AgentLocationSnapshot> {
    const location = await this.capture();
    this.enabled = true;
    this.error = null;
    this.cachedLocation = location;
    this.writeEnabled(true);
    return location;
  }

  disable(): void {
    this.enabled = false;
    this.error = null;
    this.cachedLocation = null;
    this.writeEnabled(false);
  }

  async snapshotForAction(): Promise<AgentLocationSnapshot | null> {
    if (!this.enabled) {
      return null;
    }
    if (this.cachedLocation !== null && this.isFresh(this.cachedLocation)) {
      return this.cachedLocation;
    }
    try {
      const location = await this.capture();
      this.cachedLocation = location;
      this.error = null;
      return location;
    } catch (error) {
      this.error = normalizeLocationError(error);
      if (this.error.code === "denied") {
        this.enabled = false;
        this.cachedLocation = null;
        this.writeEnabled(false);
      }
      return null;
    }
  }

  private capture(): Promise<AgentLocationSnapshot> {
    if (this.geolocation === null) {
      const error = new LocationCaptureError(
        "Location is not available in this browser.",
        "unsupported",
      );
      this.error = error;
      return Promise.reject(error);
    }
    return new Promise((resolve, reject) => {
      this.geolocation?.getCurrentPosition(
        (position) => {
          resolve({
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
            horizontal_accuracy_m: position.coords.accuracy,
            captured_at: new Date(position.timestamp).toISOString(),
          });
        },
        (positionError) => reject(locationErrorFromPosition(positionError)),
        {
          enableHighAccuracy: true,
          maximumAge: 60_000,
          timeout: LOCATION_CAPTURE_TIMEOUT_MS,
        },
      );
    });
  }

  private isFresh(location: AgentLocationSnapshot): boolean {
    const capturedAt = Date.parse(location.captured_at);
    return (
      Number.isFinite(capturedAt) &&
      this.now() - capturedAt >= 0 &&
      this.now() - capturedAt <= LOCATION_CACHE_MAX_AGE_MS
    );
  }

  private readEnabled(): boolean {
    try {
      return this.storage?.getItem(this.storageKey) === "true";
    } catch {
      return false;
    }
  }

  private writeEnabled(enabled: boolean): void {
    try {
      if (enabled) {
        this.storage?.setItem(this.storageKey, "true");
      } else {
        this.storage?.removeItem(this.storageKey);
      }
    } catch {
      // Browser privacy modes may make storage unavailable. The in-memory choice
      // still applies for the current page.
    }
  }
}

export function locationSharingStorageKey(
  serverUrl: string,
  userId: string,
): string {
  return `voxos.location-sharing:${encodeURIComponent(serverUrl)}:${encodeURIComponent(userId)}`;
}

function browserStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

function locationErrorFromPosition(error: GeolocationPositionError): LocationCaptureError {
  if (error.code === 1) {
    return new LocationCaptureError(
      "Location permission was denied. Allow location in browser settings to share it.",
      "denied",
    );
  }
  return new LocationCaptureError(
    error.message || "Your current location could not be determined.",
    "unavailable",
  );
}

function normalizeLocationError(error: unknown): LocationCaptureError {
  if (error instanceof LocationCaptureError) {
    return error;
  }
  return new LocationCaptureError(
    error instanceof Error
      ? error.message
      : "Your current location could not be determined.",
    "unavailable",
  );
}
