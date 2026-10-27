import { useState } from "react";
import type { User } from "firebase/auth";
import type { BrowserLocationSharing } from "../location";

type AccountSettingsPageProps = {
  changeServer: () => void;
  firebaseUser: User;
  isAuthWorking: boolean;
  locationSharing?: BrowserLocationSharing;
  serverUrl: string;
  signOutUser: () => Promise<void>;
};

export function AccountSettingsPage({
  changeServer,
  firebaseUser,
  isAuthWorking,
  locationSharing,
  serverUrl,
  signOutUser,
}: AccountSettingsPageProps) {
  const displayName =
    firebaseUser.displayName ?? firebaseUser.email ?? firebaseUser.uid;
  const [isLocationEnabled, setIsLocationEnabled] = useState(
    locationSharing?.isEnabled ?? false,
  );
  const [isLocationWorking, setIsLocationWorking] = useState(false);
  const [locationError, setLocationError] = useState<string | null>(null);

  const updateLocationSharing = async (enabled: boolean) => {
    if (locationSharing === undefined) {
      return;
    }
    setLocationError(null);
    if (!enabled) {
      locationSharing.disable();
      setIsLocationEnabled(false);
      return;
    }
    setIsLocationWorking(true);
    try {
      await locationSharing.enable();
      setIsLocationEnabled(true);
    } catch (error) {
      setIsLocationEnabled(false);
      setLocationError(
        error instanceof Error ? error.message : "Location could not be enabled.",
      );
    } finally {
      setIsLocationWorking(false);
    }
  };

  return (
    <div className="settings-page account-page">
      <section className="settings-panel account-panel">
        <div className="section-title">
          <p className="eyebrow">User</p>
          <h2>Account</h2>
        </div>
        <div className="account-summary">
          <span>Signed in as</span>
          <strong>{displayName}</strong>
        </div>
        <dl className="account-details">
          <div>
            <dt>Email</dt>
            <dd>{firebaseUser.email ?? "Not provided"}</dd>
          </div>
          <div>
            <dt>User ID</dt>
            <dd>{firebaseUser.uid}</dd>
          </div>
        </dl>
        <div className="active-server-row">
          <span>
            <small>Server</small>
            <strong>{serverUrl}</strong>
          </span>
          <button disabled={isAuthWorking} onClick={changeServer} type="button">
            Change server
          </button>
        </div>
        {locationSharing === undefined ? null : (
          <div className="location-sharing-row">
            <span>
              <small>Location for your agent</small>
              <strong>
                {isLocationEnabled ? "Shared when relevant" : "Not shared"}
              </strong>
              <p>
                When enabled, Voxos requests your foreground location before each
                message and at the start of a voice session. Location failures never
                block chat or calls.
              </p>
            </span>
            <label className="location-sharing-toggle">
              <input
                checked={isLocationEnabled}
                disabled={isLocationWorking}
                onChange={(event) =>
                  void updateLocationSharing(event.currentTarget.checked)
                }
                type="checkbox"
              />
              <span>{isLocationWorking ? "Requesting…" : "Allow"}</span>
            </label>
            {locationError === null ? null : (
              <p className="settings-error" role="alert">
                {locationError}
              </p>
            )}
          </div>
        )}
        <button
          className="danger"
          disabled={isAuthWorking}
          onClick={() => void signOutUser()}
          type="button"
        >
          Sign out
        </button>
      </section>
    </div>
  );
}
