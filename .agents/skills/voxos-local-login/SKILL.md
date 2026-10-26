---
name: voxos-local-login
description: Log in to the local Voxos frontend with the shared local Firebase email/password test account. Use when Codex needs authenticated access to the local Voxos app, needs a Firebase ID token for local API checks, or needs to verify signed-in browser UI flows on localhost.
---

# Voxos Local Login

Use the fake local-development account configured in the repository root `.env`.
Do not print the password, Firebase ID token, refresh token, or Authorization header.
Do not commit `.env` or copy the credential values into this skill, `AGENTS.md`,
tests, docs, screenshots, logs, or command output.

## Decision

- For signed-in UI work, use Chrome rather than the Codex in-app browser when a
  browser tool is available. The in-app browser is fine for unauthenticated
  previews, but signed-in pages need normal browser auth state.
- For API-only checks, sign in through Firebase REST with the env credentials and
  send the returned ID token as `Authorization: Bearer <token>`.
- If the account is missing, create it only in the local Firebase project used
  by `frontend/.env.local`, then save or update only the root `.env` values.

## Environment

Load local configuration from the repository root:

```bash
set -a
. ./.env
. ./frontend/.env.local
set +a
```

Required values:

- `VOXOS_CODEX_FRONTEND_URL`, usually `http://localhost:5173`
- `VOXOS_CODEX_API_BASE_URL`, usually `http://localhost:7100`
- `VOXOS_CODEX_TEST_EMAIL`
- `VOXOS_CODEX_TEST_PASSWORD`
- `VITE_FIREBASE_API_KEY` from `frontend/.env.local`

Before login, check the local API and frontend are running:

```bash
curl -fsS "${VOXOS_CODEX_API_BASE_URL%/}/health"
curl -fsSI "${VOXOS_CODEX_FRONTEND_URL%/}"
```

If either server is down and the user asked for end-to-end UI work, inspect the
Makefile recipe before starting `make run` or `make frontend`.

## Browser Login

1. Open `${VOXOS_CODEX_FRONTEND_URL}` in Chrome.
2. If the auth gate is visible, select `Sign in`.
3. Enter `VOXOS_CODEX_TEST_EMAIL` and `VOXOS_CODEX_TEST_PASSWORD`.
4. Submit the form.
5. Confirm the authenticated app shell appears. Good signals include the sidebar,
   account/settings pages, or a successful load of authenticated provider settings.

If sign-in returns a missing-user error, switch to `Create`, submit the same
email/password, then sign in again if needed.

## API Token Login

Use this when a bearer token is enough or when verifying the backend auth path.
Keep all token handling inside the process; print only success/failure.

```bash
set -a
. ./.env
. ./frontend/.env.local
set +a

node <<'NODE'
const apiKey = process.env.VITE_FIREBASE_API_KEY;
const email = process.env.VOXOS_CODEX_TEST_EMAIL;
const password = process.env.VOXOS_CODEX_TEST_PASSWORD;
const apiBaseUrl = process.env.VOXOS_CODEX_API_BASE_URL ?? "http://localhost:7100";

if (!apiKey || !email || !password) {
  throw new Error("Missing local Codex login environment variables.");
}

const response = await fetch(
  `https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${encodeURIComponent(apiKey)}`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, returnSecureToken: true }),
  },
);
const payload = await response.json();
if (!response.ok) {
  throw new Error(payload?.error?.message ?? `Firebase sign-in failed: ${response.status}`);
}

const check = await fetch(`${apiBaseUrl.replace(/\/$/, "")}/api/voice/provider-settings`, {
  headers: { Authorization: `Bearer ${payload.idToken}` },
});
if (!check.ok) {
  throw new Error(`Local API auth check failed: ${check.status}`);
}

console.log("Local Voxos Firebase login succeeded.");
NODE
```

The authenticated API check also ensures the backend maps the Firebase user to
the durable local Voxos user record.
