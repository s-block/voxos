# Browser Task Service

This directory contains a stateless, provider-neutral browser automation service built with Stagehand, Playwright-compatible Chromium control, and a private Steel browser process. It is a general browser-use runtime: the caller supplies the task instruction, start URL, origin boundary, action policy, optional named variables, and model transport for each request.

No website, login flow, account type, or extraction objective is encoded in the service. Applications can add their own authorization and secret brokers in front of this API without changing the browser runtime.

## API

`POST /v1/tasks/run` accepts:

```http
Authorization: Bearer <browser-agent-service-token>
```

```json
{
  "instruction": "Open the documentation and summarize the authentication options.",
  "startUrl": "https://docs.example.com/",
  "allowedOrigins": ["https://docs.example.com"],
  "actionMode": "read_only",
  "maxSteps": 40,
  "variables": {},
  "model": {
    "adapter": "stagehand_native",
    "modelName": "openai/gpt-5-mini",
    "baseUrl": "https://api.openai.com/v1",
    "apiKey": "..."
  }
}
```

Named variables use `{ "value": "...", "description": "..." }` entries. Stagehand exposes them to the browser agent as `%variable_name%`. The system policy tells the agent not to reveal variable values; callers must still treat the service and its browser process as secret-bearing infrastructure.

The response is either:

```json
{ "status": "completed", "output": "..." }
```

or a `blocked` result with evidence describing why the objective could not be verified.

`GET /health` returns service health.

## Security boundary

- Keep this API on a private application network. Every task requires the shared service bearer credential. Validate end-user authorization in the caller before sending a task.
- Validate caller authorization before attaching variables or expanding allowed origins.
- Use exact HTTPS origins and the narrowest origin set required by the task.
- Default to `read_only`; enable `state_changes_allowed` only for an explicitly authorized action.
- Enforce network egress controls outside the container. Application validation rejects local and literal private targets, but an egress policy is the definitive control against DNS rebinding and access to infrastructure metadata.
- Treat webpage content as untrusted. The browser policy prevents it from overriding the caller instruction or disclosing task variables.
- Sessions are ephemeral and force-closed after every request. This service does not persist browser profiles.

## Local development

```bash
npm ci
npm test
BROWSER_AGENT_SERVICE_TOKEN="$(openssl rand -hex 32)" STEEL_SERVICE_TOKEN="$(openssl rand -hex 32)" npm start
```

Set `STEEL_BASE_URL` to the private Steel endpoint. Configure the same
`STEEL_SERVICE_TOKEN` in its authenticated HTTP/WebSocket proxy, and the same
`BROWSER_AGENT_SERVICE_TOKEN` as `BROWSER_ACCOUNTS_STAGEHAND_SERVICE_TOKEN` in each
Voxos caller. Tokens must contain 32 to 512 printable non-space characters; the
service refuses to start without both credentials. Supply deployed tokens from
Secrets and protect local environment files.

`PORT` defaults to `3100`, and `TASK_TIMEOUT_MS` defaults to `570000`, including
time spent waiting in the queue. One task runs at a time. `MAX_QUEUED_TASKS`
defaults to `4` and `QUEUE_WAIT_TIMEOUT_MS`
defaults to `30000`. Excess or expired admissions return 429 with `Retry-After`;
canceled queued requests are removed without starting a browser session.
