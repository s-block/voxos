import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import type { AddressInfo } from "node:net";
import { WebSocketServer, type WebSocket } from "ws";

import { TopLevelNavigationGuard } from "./navigation_guard.js";

interface CdpCommand {
  id: number;
  method: string;
  params: Record<string, unknown>;
  sessionId?: string;
}

const servers: WebSocketServer[] = [];

afterEach(async () => {
  await Promise.all(
    servers.splice(0).map(
      (server) => {
        for (const client of server.clients) client.terminate();
        return new Promise<void>((resolve) => {
          server.close(() => resolve());
        });
      },
    ),
  );
});

test("guards existing and newly opened top-level pages without blocking subframes", async () => {
  const commands: CdpCommand[] = [];
  const server = new WebSocketServer({ port: 0 });
  servers.push(server);
  let authorization: string | undefined;
  server.on("connection", (_socket, request) => { authorization = request.headers.authorization; });
  const socketPromise = new Promise<WebSocket>((resolve) => server.once("connection", resolve));
  await new Promise<void>((resolve) => server.once("listening", resolve));
  const address = server.address() as AddressInfo;

  const guardPromise = TopLevelNavigationGuard.connect(
    `ws://127.0.0.1:${address.port}`,
    ["https://account.example"],
    { Authorization: "Bearer test-steel-service-token-with-enough-length" },
  );
  const socket = await socketPromise;
  socket.on("message", (raw) => {
    const command = JSON.parse(raw.toString()) as CdpCommand;
    commands.push(command);
    if (command.method === "Target.getTargets") {
      respond(socket, command.id, {
        targetInfos: [{ targetId: "existing-page", type: "page" }],
      });
      return;
    }
    if (command.method === "Target.attachToTarget") {
      respond(socket, command.id, { sessionId: "existing-session" });
      return;
    }
    if (command.method === "Page.getFrameTree") {
      respond(socket, command.id, {
        frameTree: { frame: { id: command.sessionId === "popup-session" ? "popup-root" : "existing-root" } },
      });
      return;
    }
    respond(socket, command.id, {});
  });
  const guard = await guardPromise;
  assert.equal(authorization, "Bearer test-steel-service-token-with-enough-length");

  const allowedFrom = commands.length;
  emitPausedRequest(socket, "existing-session", "existing-root", "https://account.example/dashboard");
  const allowed = await waitForCommand(commands, allowedFrom, "Fetch.continueRequest", "existing-session");
  assert.equal(allowed.sessionId, "existing-session");

  const subframeFrom = commands.length;
  emitPausedRequest(socket, "existing-session", "child-frame", "https://external.example/widget");
  await waitForCommand(commands, subframeFrom, "Fetch.continueRequest", "existing-session");

  const warnings: string[] = [];
  const originalWarn = console.warn;
  console.warn = (value?: unknown): void => {
    warnings.push(String(value));
  };
  try {
    const blockedFrom = commands.length;
    emitPausedRequest(socket, "existing-session", "existing-root", "https://external.example/private?token=secret");
    await waitForCommand(commands, blockedFrom, "Fetch.failRequest", "existing-session");
  } finally {
    console.warn = originalWarn;
  }
  assert.deepEqual(warnings.map((value) => JSON.parse(value)), [
    { event: "browser_navigation_blocked", origin: "https://external.example" },
  ]);

  const popupFrom = commands.length;
  socket.send(
    JSON.stringify({
      method: "Target.attachedToTarget",
      params: {
        sessionId: "popup-session",
        targetInfo: { targetId: "popup-page", type: "page" },
      },
    }),
  );
  await waitForCommand(commands, popupFrom, "Fetch.enable", "popup-session");
  await waitForCommand(commands, popupFrom, "Runtime.runIfWaitingForDebugger", "popup-session");

  await guard.close();
});

function respond(socket: WebSocket, id: number, result: Record<string, unknown>): void {
  socket.send(JSON.stringify({ id, result }));
}

function emitPausedRequest(socket: WebSocket, sessionId: string, frameId: string, url: string): void {
  socket.send(
    JSON.stringify({
      method: "Fetch.requestPaused",
      sessionId,
      params: {
        requestId: `${sessionId}-${frameId}`,
        frameId,
        resourceType: "Document",
        request: { url },
      },
    }),
  );
}

async function waitForCommand(
  commands: readonly CdpCommand[],
  from: number,
  method: string,
  sessionId?: string,
): Promise<CdpCommand> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const command = commands
      .slice(from)
      .find((candidate) => candidate.method === method && candidate.sessionId === sessionId);
    if (command !== undefined) return command;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error(`Timed out waiting for ${method}`);
}
