import { lookup } from "node:dns/promises";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { isIP } from "node:net";
import { Stagehand, type AgentResult } from "@browserbasehq/stagehand";
import { z } from "zod";

import {
  browserTaskOutputSchema,
  browserTaskRequestSchema,
  type BrowserTaskRequest,
  type BrowserTaskResponse,
} from "./contracts.js";
import { stagehandModel } from "./model_factory.js";
import { TopLevelNavigationGuard } from "./navigation_guard.js";
import { browserSystemPrompt } from "./task_policy.js";
import { configuredServiceToken, isAuthorizedServiceCall } from "./service_auth.js";
import { QueueAbortedError, QueueFullError, QueueTimeoutError, SerialTaskQueue } from "./task_queue.js";

const port = parsePositiveInteger(process.env.PORT, 3100);
const steelBaseUrl = validatedServiceUrl(process.env.STEEL_BASE_URL ?? "http://steel-browser:3000");
const taskTimeoutMs = parsePositiveInteger(process.env.TASK_TIMEOUT_MS, 570_000);
const maximumBodyBytes = 32_768;
const serviceToken = configuredServiceToken(process.env.BROWSER_AGENT_SERVICE_TOKEN);
const steelServiceToken = configuredServiceToken(process.env.STEEL_SERVICE_TOKEN, "STEEL_SERVICE_TOKEN");
const maximumQueuedTasks = parsePositiveInteger(process.env.MAX_QUEUED_TASKS, 4);
const queueWaitTimeoutMs = parsePositiveInteger(process.env.QUEUE_WAIT_TIMEOUT_MS, 30_000);

const steelSessionSchema = z
  .object({
    id: z.string().min(1),
    websocketUrl: z.string().min(1),
  })
  .passthrough();

const taskQueue = new SerialTaskQueue(maximumQueuedTasks, queueWaitTimeoutMs);

const server = createServer(async (request, response) => {
  try {
    if (request.method === "GET" && request.url === "/health") {
      sendJson(response, 200, { status: "ok" });
      return;
    }
    if (request.method !== "POST" || request.url !== "/v1/tasks/run") {
      sendJson(response, 404, { error: "not_found" });
      return;
    }
    if (!isAuthorizedServiceCall(request.headers.authorization, serviceToken)) {
      response.setHeader("www-authenticate", "Bearer");
      sendJson(response, 401, { error: "not_authenticated" });
      return;
    }

    const body = await readJsonBody(request);
    const parsed = browserTaskRequestSchema.safeParse(body);
    if (!parsed.success) {
      sendJson(response, 422, { error: "invalid_request" });
      return;
    }
    const taskDeadline = performance.now() + taskTimeoutMs;

    const requestAbortController = new AbortController();
    const abortRequest = (): void => requestAbortController.abort();
    const abortDisconnectedResponse = (): void => {
      if (!response.writableEnded) requestAbortController.abort();
    };
    request.once("aborted", abortRequest);
    response.once("close", abortDisconnectedResponse);
    if (request.aborted || response.destroyed) requestAbortController.abort();
    try {
      const result = await taskQueue.run(
        () => runTask(parsed.data, requestAbortController.signal, Math.max(1, taskDeadline - performance.now())),
        requestAbortController.signal,
      );
      sendJson(response, 200, result);
    } finally {
      request.off("aborted", abortRequest);
      response.off("close", abortDisconnectedResponse);
    }
  } catch (error) {
    logTaskFailure(error);
    if (response.destroyed || error instanceof QueueAbortedError) return;
    let status: number;
    let message: string;
    if (error instanceof QueueFullError || error instanceof QueueTimeoutError) {
      status = 429;
      message = "browser_queue_unavailable";
      response.setHeader("retry-after", "30");
    } else if (error instanceof RequestTooLargeError) {
      status = 413;
      message = "request_too_large";
    } else if (error instanceof TaskTimeoutError) {
      status = 504;
      message = "task_timeout";
    } else {
      status = 502;
      message = "browser_operation_failed";
    }
    sendJson(response, status, { error: message });
  }
});

server.listen(port, "0.0.0.0");

async function runTask(
  request: BrowserTaskRequest,
  requestSignal: AbortSignal,
  remainingTimeoutMs: number,
): Promise<BrowserTaskResponse> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), remainingTimeoutMs);
  const abortRequest = (): void => controller.abort();
  if (requestSignal.aborted) controller.abort();
  else requestSignal.addEventListener("abort", abortRequest, { once: true });
  const taskStartedAt = Date.now();
  let stepNumber = 0;
  let stagehand: Stagehand | undefined;
  let navigationGuard: TopLevelNavigationGuard | undefined;
  let steelSessionId: string | undefined;
  let taskStage: BrowserTaskStage = "create_session";
  try {
    const steelSession = await createSteelSession(controller.signal);
    steelSessionId = steelSession.id;
    const { model, llmClient } = stagehandModel(request);
    taskStage = "initialize_stagehand";
    const cdpUrl = await routableWebSocketUrl(steelSession.websocketUrl);
    stagehand = new Stagehand({
      env: "LOCAL",
      experimental: true,
      disableAPI: true,
      disablePino: true,
      verbose: 0,
      ...(model === undefined ? {} : { model }),
      ...(llmClient === undefined ? {} : { llmClient }),
      localBrowserLaunchOptions: {
        cdpUrl,
        cdpHeaders: { Authorization: `Bearer ${steelServiceToken}` },
        connectTimeoutMs: 30_000,
        viewport: { width: 1440, height: 1000 },
      },
    });
    await stagehand.init();
    taskStage = "install_navigation_guard";
    navigationGuard = await TopLevelNavigationGuard.connect(
      cdpUrl,
      request.allowedOrigins,
      { Authorization: `Bearer ${steelServiceToken}` },
    );
    taskStage = "open_start_origin";
    const page = await stagehand.context.awaitActivePage(30_000);
    await page.goto(request.startUrl);

    taskStage = "create_agent";
    const agent = stagehand.agent({
      mode: "dom",
      ...(model === undefined ? {} : { model, executionModel: model }),
      systemPrompt: browserSystemPrompt(request),
    });
    taskStage = "execute_agent";
    const execution = agent.execute({
      instruction: request.instruction,
      maxSteps: request.maxSteps,
      excludeTools: ["search"],
      output: browserTaskOutputSchema,
      variables: request.variables,
      signal: controller.signal,
      toolTimeout: Math.min(90_000, remainingTimeoutMs),
      callbacks: {
        onStepFinish: (event) => {
          stepNumber += 1;
          console.info(
            JSON.stringify({
              event: "browser_agent_step",
              step: stepNumber,
              finishReason: event.finishReason,
              tools: event.toolCalls.map((call) => call.toolName),
              elapsedMs: Date.now() - taskStartedAt,
            }),
          );
        },
      },
    });
    const result = await Promise.race([execution, navigationGuard.failure]);
    return normalizedResult(result);
  } catch (error) {
    if (controller.signal.aborted) {
      throw new TaskTimeoutError();
    }
    throw new BrowserTaskExecutionError(taskStage, error);
  } finally {
    clearTimeout(timeout);
    requestSignal.removeEventListener("abort", abortRequest);
    if (navigationGuard !== undefined) {
      await navigationGuard.close().catch(() => undefined);
    }
    if (stagehand !== undefined) {
      await stagehand.close({ force: true }).catch(() => undefined);
    }
    if (steelSessionId !== undefined) {
      await releaseSteelSession(steelSessionId).catch(() => undefined);
    }
  }
}

function normalizedResult(result: AgentResult): BrowserTaskResponse {
  const structured = browserTaskOutputSchema.safeParse(result.output);
  if (structured.success) {
    return {
      status: structured.data.completed && result.success && result.completed ? "completed" : "blocked",
      output: structured.data.summary,
    };
  }
  const message = result.message.trim();
  return {
    status: "blocked",
    output: message || "The browser agent did not return a usable result.",
  };
}

async function createSteelSession(signal: AbortSignal): Promise<z.infer<typeof steelSessionSchema>> {
  const response = await fetch(`${steelBaseUrl}/v1/sessions`, {
    method: "POST",
    headers: { "content-type": "application/json", Authorization: `Bearer ${steelServiceToken}` },
    body: JSON.stringify({
      headless: true,
      persist: false,
      dimensions: { width: 1440, height: 1000 },
    }),
    signal,
  });
  if (!response.ok) {
    throw new Error("Steel rejected session creation");
  }
  return steelSessionSchema.parse(await response.json());
}

async function releaseSteelSession(sessionId: string): Promise<void> {
  const response = await fetch(`${steelBaseUrl}/v1/sessions/${encodeURIComponent(sessionId)}/release`, {
    method: "POST",
    headers: { Authorization: `Bearer ${steelServiceToken}` },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok && response.status !== 404) {
    throw new Error("Steel rejected session cleanup");
  }
}

async function routableWebSocketUrl(value: string): Promise<string> {
  const websocketUrl = new URL(value, steelBaseUrl);
  const steelUrl = new URL(steelBaseUrl);
  if (
    websocketUrl.hostname === "localhost" ||
    websocketUrl.hostname === "127.0.0.1" ||
    websocketUrl.hostname === "0.0.0.0"
  ) {
    websocketUrl.hostname = await connectionHostname(steelUrl.hostname);
    websocketUrl.port = steelUrl.port;
  }
  if (websocketUrl.protocol === "http:") websocketUrl.protocol = "ws:";
  if (websocketUrl.protocol === "https:") websocketUrl.protocol = "wss:";
  if (websocketUrl.protocol !== "ws:" && websocketUrl.protocol !== "wss:") {
    throw new Error("Steel returned an invalid browser endpoint");
  }
  return websocketUrl.toString();
}

async function connectionHostname(hostname: string): Promise<string> {
  if (hostname === "localhost") return "127.0.0.1";
  if (isIP(hostname) !== 0) return hostname;
  return (await lookup(hostname)).address;
}

async function readJsonBody(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > maximumBodyBytes) throw new RequestTooLargeError();
    chunks.push(buffer);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function sendJson(response: ServerResponse, status: number, payload: object): void {
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });
  response.end(JSON.stringify(payload));
}

function validatedServiceUrl(value: string): string {
  const url = new URL(value);
  if (!(["http:", "https:"] as const).includes(url.protocol as "http:" | "https:") || url.username || url.password) {
    throw new Error("STEEL_BASE_URL is invalid");
  }
  return value.replace(/\/$/, "");
}

function parsePositiveInteger(value: string | undefined, fallback: number): number {
  if (value === undefined) return fallback;
  const parsed = /^\d+$/.test(value) ? Number(value) : Number.NaN;
  if (!Number.isSafeInteger(parsed) || parsed <= 0) throw new Error("Expected a positive integer setting");
  return parsed;
}

class RequestTooLargeError extends Error {}
class TaskTimeoutError extends Error {}

type BrowserTaskStage =
  | "create_session"
  | "initialize_stagehand"
  | "install_navigation_guard"
  | "open_start_origin"
  | "create_agent"
  | "execute_agent";

class BrowserTaskExecutionError extends Error {
  constructor(
    readonly stage: BrowserTaskStage,
    options: unknown,
  ) {
    super("Browser task execution failed", { cause: options });
  }
}

function logTaskFailure(error: unknown): void {
  if (error instanceof BrowserTaskExecutionError) {
    console.error(
      JSON.stringify({
        event: "browser_task_failed",
        stage: error.stage,
        errorType: safeErrorType(error.cause),
      }),
    );
  }
}

function safeErrorType(error: unknown): string {
  if (!(error instanceof Error)) return "UnknownError";
  const type = error.constructor.name;
  return /^[A-Za-z][A-Za-z0-9_]{0,99}$/.test(type) ? type : "Error";
}
