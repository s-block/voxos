import WebSocket, { type RawData } from "ws";

interface CdpError {
  code: number;
  message: string;
}

interface CdpMessage {
  id?: number;
  method?: string;
  params?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: CdpError;
  sessionId?: string;
}

interface PendingCommand {
  resolve: (result: Record<string, unknown>) => void;
  reject: (error: Error) => void;
}

interface TargetInfo {
  targetId: string;
  type: string;
}

const connectionTimeoutMs = 10_000;

export class TopLevelNavigationGuard {
  readonly failure: Promise<never>;

  private readonly allowedOrigins: ReadonlySet<string>;
  private readonly socket: WebSocket;
  private readonly pendingCommands = new Map<number, PendingCommand>();
  private readonly pageSessions = new Set<string>();
  private readonly attachedTargets = new Set<string>();
  private readonly rootFrameIds = new Map<string, string>();
  private nextCommandId = 1;
  private closing = false;
  private rejectFailure: (error: Error) => void = () => undefined;

  private constructor(cdpUrl: string, allowedOrigins: readonly string[], headers: Readonly<Record<string, string>>) {
    this.allowedOrigins = new Set(allowedOrigins);
    this.socket = new WebSocket(cdpUrl, { headers });
    this.failure = new Promise<never>((_resolve, reject) => {
      this.rejectFailure = reject;
    });
    void this.failure.catch(() => undefined);
    this.socket.on("message", (data) => this.handleMessage(data));
    this.socket.on("error", () => this.fail(new Error("Navigation guard connection failed")));
    this.socket.on("close", () => {
      if (!this.closing) this.fail(new Error("Navigation guard connection closed"));
    });
  }

  static async connect(
    cdpUrl: string,
    allowedOrigins: readonly string[],
    headers: Readonly<Record<string, string>> = {},
  ): Promise<TopLevelNavigationGuard> {
    const guard = new TopLevelNavigationGuard(cdpUrl, allowedOrigins, headers);
    try {
      await guard.awaitOpen();
      await guard.command("Target.setAutoAttach", {
        autoAttach: true,
        waitForDebuggerOnStart: true,
        flatten: true,
      });
      const targets = await guard.command("Target.getTargets", {});
      const targetInfos = Array.isArray(targets.targetInfos) ? targets.targetInfos : [];
      await Promise.all(
        targetInfos
          .filter(isTargetInfo)
          .filter((target) => target.type === "page")
          .map((target) => guard.attachPageTarget(target.targetId)),
      );
      return guard;
    } catch (error) {
      await guard.close().catch(() => undefined);
      throw error;
    }
  }

  async close(): Promise<void> {
    if (this.closing) return;
    this.closing = true;
    await Promise.allSettled(
      [...this.pageSessions].map((sessionId) => this.command("Fetch.disable", {}, sessionId)),
    );
    this.socket.close();
    for (const pending of this.pendingCommands.values()) {
      pending.reject(new Error("Navigation guard closed"));
    }
    this.pendingCommands.clear();
  }

  private async awaitOpen(): Promise<void> {
    if (this.socket.readyState === WebSocket.OPEN) return;
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("Navigation guard connection timed out")), connectionTimeoutMs);
      this.socket.once("open", () => {
        clearTimeout(timeout);
        resolve();
      });
      this.socket.once("error", () => {
        clearTimeout(timeout);
        reject(new Error("Navigation guard connection failed"));
      });
    });
  }

  private handleMessage(data: RawData): void {
    let message: CdpMessage;
    try {
      message = JSON.parse(data.toString()) as CdpMessage;
    } catch {
      this.fail(new Error("Navigation guard received invalid CDP data"));
      return;
    }
    if (message.id !== undefined) {
      const pending = this.pendingCommands.get(message.id);
      if (pending === undefined) return;
      this.pendingCommands.delete(message.id);
      if (message.error !== undefined) pending.reject(new Error("Navigation guard CDP command failed"));
      else pending.resolve(message.result ?? {});
      return;
    }
    void this.handleEvent(message).catch(() => {
      this.fail(new Error("Navigation guard event handling failed"));
    });
  }

  private async handleEvent(message: CdpMessage): Promise<void> {
    if (message.method === "Target.attachedToTarget") {
      const sessionId = stringValue(message.params?.sessionId);
      const targetInfo = message.params?.targetInfo;
      if (sessionId !== undefined && isTargetInfo(targetInfo)) {
        if (targetInfo.type === "page") {
          await this.initializePageSession(sessionId);
        }
        await this.command("Runtime.runIfWaitingForDebugger", {}, sessionId);
      }
      return;
    }
    if (message.method === "Target.detachedFromTarget") {
      const sessionId = stringValue(message.params?.sessionId);
      if (sessionId !== undefined) {
        this.pageSessions.delete(sessionId);
        this.rootFrameIds.delete(sessionId);
      }
      return;
    }
    if (message.sessionId === undefined) return;
    if (message.method === "Page.frameNavigated") {
      this.updateRootFrame(message.sessionId, message.params?.frame);
      return;
    }
    if (message.method === "Fetch.requestPaused") {
      await this.handlePausedRequest(message.sessionId, message.params ?? {});
    }
  }

  private async attachPageTarget(targetId: string): Promise<void> {
    if (this.attachedTargets.has(targetId)) return;
    this.attachedTargets.add(targetId);
    try {
      const result = await this.command("Target.attachToTarget", { targetId, flatten: true });
      const sessionId = stringValue(result.sessionId);
      if (sessionId === undefined) throw new Error("Navigation guard did not receive a CDP session");
      await this.initializePageSession(sessionId);
    } catch (error) {
      this.attachedTargets.delete(targetId);
      throw error;
    }
  }

  private async initializePageSession(sessionId: string): Promise<void> {
    if (this.pageSessions.has(sessionId)) return;
    this.pageSessions.add(sessionId);
    await this.command("Page.enable", {}, sessionId);
    const frameTree = await this.command("Page.getFrameTree", {}, sessionId);
    const rootFrameId = nestedStringValue(frameTree, "frameTree", "frame", "id");
    if (rootFrameId === undefined) throw new Error("Navigation guard could not identify the main frame");
    this.rootFrameIds.set(sessionId, rootFrameId);
    await this.command(
      "Fetch.enable",
      {
        patterns: [{ urlPattern: "*", resourceType: "Document", requestStage: "Request" }],
      },
      sessionId,
    );
  }

  private updateRootFrame(sessionId: string, value: unknown): void {
    if (!isRecord(value) || value.parentId !== undefined) return;
    const frameId = stringValue(value.id);
    if (frameId !== undefined) this.rootFrameIds.set(sessionId, frameId);
  }

  private async handlePausedRequest(sessionId: string, params: Record<string, unknown>): Promise<void> {
    const requestId = stringValue(params.requestId);
    if (requestId === undefined) throw new Error("Navigation guard received an invalid paused request");
    const frameId = stringValue(params.frameId);
    const rootFrameId = this.rootFrameIds.get(sessionId);
    const request = params.request;
    const url = isRecord(request) ? stringValue(request.url) : undefined;
    const isTopLevelDocument = params.resourceType === "Document" && frameId !== undefined && frameId === rootFrameId;
    if (!isTopLevelDocument || url === undefined || this.isAllowedUrl(url)) {
      await this.command("Fetch.continueRequest", { requestId }, sessionId);
      return;
    }
    console.warn(
      JSON.stringify({
        event: "browser_navigation_blocked",
        origin: safeOrigin(url),
      }),
    );
    await this.command("Fetch.failRequest", { requestId, errorReason: "BlockedByClient" }, sessionId);
  }

  private isAllowedUrl(value: string): boolean {
    try {
      const url = new URL(value);
      return (url.protocol === "http:" || url.protocol === "https:") && this.allowedOrigins.has(url.origin);
    } catch {
      return false;
    }
  }

  private command(
    method: string,
    params: Record<string, unknown>,
    sessionId?: string,
  ): Promise<Record<string, unknown>> {
    if (this.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("Navigation guard is not connected"));
    }
    const id = this.nextCommandId;
    this.nextCommandId += 1;
    return new Promise<Record<string, unknown>>((resolve, reject) => {
      this.pendingCommands.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId === undefined ? {} : { sessionId }) }));
    });
  }

  private fail(error: Error): void {
    if (this.closing) return;
    this.closing = true;
    this.rejectFailure(error);
    for (const pending of this.pendingCommands.values()) pending.reject(error);
    this.pendingCommands.clear();
    this.socket.close();
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isTargetInfo(value: unknown): value is TargetInfo {
  return isRecord(value) && typeof value.targetId === "string" && typeof value.type === "string";
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function nestedStringValue(value: Record<string, unknown>, ...path: string[]): string | undefined {
  let current: unknown = value;
  for (const key of path) {
    if (!isRecord(current)) return undefined;
    current = current[key];
  }
  return stringValue(current);
}

function safeOrigin(value: string): string {
  try {
    return new URL(value).origin;
  } catch {
    return "invalid";
  }
}
