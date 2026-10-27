import assert from "node:assert/strict";
import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const agentToken = "test-agent-service-token-with-enough-length";
const steelToken = "test-steel-service-token-with-enough-length";

test("HTTP task admissions authenticate before parsing and bound the queue", { timeout: 20_000 }, async () => {
  let markSteelStarted!: () => void;
  const steelStarted = new Promise<void>((resolve) => { markSteelStarted = resolve; });
  let steelAuthorization: string | undefined;
  const steel = createServer((request) => {
    steelAuthorization = request.headers.authorization;
    markSteelStarted();
    // Hold creation open so the running slot stays occupied until its task deadline.
  });
  await listen(steel);
  const steelPort = (steel.address() as AddressInfo).port;
  const reservation = createServer();
  await listen(reservation);
  const port = (reservation.address() as AddressInfo).port;
  await close(reservation);
  const child = spawn(process.execPath, [fileURLToPath(new URL("server.js", import.meta.url))], {
    env: {
      PATH: process.env.PATH ?? "",
      PORT: String(port),
      BROWSER_AGENT_SERVICE_TOKEN: agentToken,
      STEEL_SERVICE_TOKEN: steelToken,
      STEEL_BASE_URL: `http://127.0.0.1:${steelPort}`,
      MAX_QUEUED_TASKS: "1",
      QUEUE_WAIT_TIMEOUT_MS: "200",
      TASK_TIMEOUT_MS: "1500",
    },
    stdio: "ignore",
  });
  const baseUrl = `http://127.0.0.1:${port}`;
  try {
    await awaitHealth(baseUrl, child);
    const unauthorized = await fetch(`${baseUrl}/v1/tasks/run`, { method: "POST", body: "not-json" });
    assert.equal(unauthorized.status, 401);
    assert.equal(unauthorized.headers.get("www-authenticate"), "Bearer");
    const invalid = await fetch(`${baseUrl}/v1/tasks/run`, {
      method: "POST", headers: { Authorization: `Bearer ${agentToken}` }, body: "{}",
    });
    assert.equal(invalid.status, 422);

    const operation = (): Promise<Response> => fetch(`${baseUrl}/v1/tasks/run`, {
      method: "POST",
      headers: { Authorization: `Bearer ${agentToken}`, "content-type": "application/json" },
      body: JSON.stringify({
        instruction: "Read the public documentation", startUrl: "https://docs.example.com/",
        allowedOrigins: ["https://docs.example.com"], maxSteps: 1,
        model: { adapter: "stagehand_native", modelName: "openai/test-model", baseUrl: "https://api.example.com/v1", apiKey: "test-model-credential" }, // pragma: allowlist secret -- synthetic fixture
      }),
    });
    const first = operation();
    await steelStarted;
    assert.equal(steelAuthorization, `Bearer ${steelToken}`);
    const second = operation();
    await delay(30);
    const excess = await operation();
    assert.equal(excess.status, 429);
    assert.equal(excess.headers.get("retry-after"), "30");
    assert.equal((await second).status, 429);
    assert.equal((await fetch(`${baseUrl}/health`)).status, 200);
    assert.equal((await first).status, 504);
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      const stopped = once(child, "exit");
      child.kill("SIGTERM");
      await stopped;
    }
    steel.closeAllConnections();
    await close(steel);
  }
});

async function listen(server: Server): Promise<void> {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
}

async function close(server: Server): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    server.close((error) => error === undefined ? resolve() : reject(error));
  });
}

async function awaitHealth(baseUrl: string, child: ChildProcess): Promise<void> {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error("Browser service exited before becoming healthy");
    try {
      if ((await fetch(`${baseUrl}/health`)).status === 200) return;
    } catch {
      // The child is still starting and has not opened its local listener.
    }
    await delay(25);
  }
  throw new Error("Browser service did not become healthy");
}
