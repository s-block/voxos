import { FormEvent, useState } from "react";
import type { BrandLogo } from "../api";
import type { AuthFormMode } from "../appTypes";
import { TextField } from "../components/FormFields";
import { ServiceLogo } from "../components/ServiceLogo";
import { VoxosLogoTile } from "../components/VoxosMark";

type AuthGateProps = {
  authError: string | null;
  isAuthReady: boolean;
  isAuthWorking: boolean;
  onApple: () => void;
  onChangeServer: () => void;
  onEmailSubmit: (
    mode: AuthFormMode,
    email: string,
    password: string,
  ) => Promise<void>;
  onGoogle: () => void;
  serverUrl: string;
};

const AUTH_PROVIDER_LOGOS: Record<"apple" | "google", BrandLogo> = {
  apple: {
    domain: "apple.com",
    label: "Apple",
    source: "domain",
  },
  google: {
    domain: "google.com",
    label: "Google",
    source: "domain",
  },
};

export function AuthGate({
  authError,
  isAuthReady,
  isAuthWorking,
  onApple,
  onChangeServer,
  onEmailSubmit,
  onGoogle,
  serverUrl,
}: AuthGateProps) {
  const [mode, setMode] = useState<AuthFormMode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const canSubmit =
    isAuthReady &&
    !isAuthWorking &&
    email.trim() !== "" &&
    password !== "";

  return (
    <main className="auth-shell">
      <section className="auth-panel">
        <div className="auth-brand">
          <VoxosLogoTile
            ariaLabel="VoxOS"
            className="auth-logo-lockup"
            surface="light"
          />
          <div className="section-title">
            <h1>Sign in</h1>
          </div>
        </div>

        <div className="segmented two-state" role="group">
          <button
            aria-pressed={mode === "signin"}
            className={mode === "signin" ? "selected" : ""}
            disabled={isAuthWorking}
            onClick={() => setMode("signin")}
            type="button"
          >
            Sign in
          </button>
          <button
            aria-pressed={mode === "create"}
            className={mode === "create" ? "selected" : ""}
            disabled={isAuthWorking}
            onClick={() => setMode("create")}
            type="button"
          >
            Create
          </button>
        </div>

        <form
          className="settings-form"
          onSubmit={(event: FormEvent<HTMLFormElement>) => {
            event.preventDefault();
            if (canSubmit) {
              void onEmailSubmit(mode, email.trim(), password);
            }
          }}
        >
          <TextField
            disabled={isAuthWorking}
            label="Email"
            onChange={setEmail}
            placeholder="you@example.com"
            type="email"
            value={email}
          />
          <TextField
            disabled={isAuthWorking}
            label="Password"
            onChange={setPassword}
            placeholder="Password"
            type="password"
            value={password}
          />

          <button className="primary" disabled={!canSubmit} type="submit">
            {mode === "create" ? "Create account" : "Sign in"}
          </button>
        </form>

        <div className="provider-actions">
          <button
            disabled={!isAuthReady || isAuthWorking}
            onClick={onGoogle}
            type="button"
          >
            <ServiceLogo
              decorative
              label="Google"
              logo={AUTH_PROVIDER_LOGOS.google}
              size="sm"
            />
            Google
          </button>
          <button
            disabled={!isAuthReady || isAuthWorking}
            onClick={onApple}
            type="button"
          >
            <ServiceLogo
              decorative
              label="Apple"
              logo={AUTH_PROVIDER_LOGOS.apple}
              size="sm"
            />
            Apple
          </button>
        </div>

        {authError !== null ? <p className="error-text">{authError}</p> : null}
        <div className="active-server-row">
          <span>
            <small>Server</small>
            <strong>{serverUrl}</strong>
          </span>
          <button disabled={isAuthWorking} onClick={onChangeServer} type="button">
            Change
          </button>
        </div>
      </section>
    </main>
  );
}
