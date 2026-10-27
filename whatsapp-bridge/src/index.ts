import {
  BridgeError,
  JsonLineWriter,
  parseCommandLine,
  protocolErrorEvent,
  readBoundedLines,
} from "./protocol.js";
import {
  BridgeRpcPeer,
  rpcTimeoutFromEnvironment,
} from "./rpc-peer.js";
import { SessionManager } from "./session-manager.js";

const SHUTDOWN_TIMEOUT_MS = 15_000;

installSanitizedConsole();

async function runBridge(): Promise<void> {
  const writer = new JsonLineWriter(process.stdout);
  const rpc = new BridgeRpcPeer(
    writer,
    rpcTimeoutFromEnvironment(
      process.env.VOXOS_WHATSAPP_RPC_TIMEOUT_MS,
    ),
  );
  const manager = new SessionManager(writer, rpc);
  let shuttingDown = false;
  let fatalExitStarted = false;

  const shutdown = async (): Promise<void> => {
    if (shuttingDown) {
      return;
    }
    shuttingDown = true;
    await Promise.race([
      manager.abort(),
      new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, SHUTDOWN_TIMEOUT_MS);
        timer.unref();
      }),
    ]);
    await writer.flush().catch(() => undefined);
  };

  const fatalExit = (diagnostic: string): void => {
    if (fatalExitStarted) {
      return;
    }
    fatalExitStarted = true;
    fixedDiagnostic(diagnostic);
    const forceExitTimer = setTimeout(() => {
      process.exit(1);
    }, SHUTDOWN_TIMEOUT_MS + 1_000);
    void shutdown()
      .catch(() => undefined)
      .finally(() => {
        clearTimeout(forceExitTimer);
        process.exit(1);
      });
  };

  const onSignal = (): void => {
    void shutdown().finally(() => {
      process.stdin.destroy();
      process.exitCode = 0;
    });
  };
  const onUncaughtException = (): void => {
    fatalExit("uncaught exception");
  };
  const onUnhandledRejection = (): void => {
    fatalExit("unhandled rejection");
  };
  process.once("SIGINT", onSignal);
  process.once("SIGTERM", onSignal);
  process.once("uncaughtException", onUncaughtException);
  process.once("unhandledRejection", onUnhandledRejection);

  try {
    for await (const line of readBoundedLines(process.stdin)) {
      let command;
      try {
        command = parseCommandLine(line);
      } catch (error) {
        const code =
          error instanceof BridgeError ? error.code : "invalid_protocol_frame";
        await writer.write(protocolErrorEvent(code));
        continue;
      }
      void manager.handleCommand(command).catch(() => {
        fatalExit("command response failed");
      });
    }
  } catch (error) {
    const code = error instanceof BridgeError ? error.code : "stdin_failure";
    await writer
      .write(protocolErrorEvent(code))
      .catch(() => fixedDiagnostic("protocol error response failed"));
  } finally {
    await shutdown();
    process.off("SIGINT", onSignal);
    process.off("SIGTERM", onSignal);
    process.off("uncaughtException", onUncaughtException);
    process.off("unhandledRejection", onUnhandledRejection);
  }
}

function fixedDiagnostic(message: string): void {
  process.stderr.write(`whatsapp-bridge: ${message}\n`);
}

function installSanitizedConsole(): void {
  const suppress = (): void => {
    fixedDiagnostic("suppressed library diagnostic");
  };
  console.debug = suppress;
  console.error = suppress;
  console.info = suppress;
  console.log = suppress;
  console.warn = suppress;
}

void runBridge().catch(() => {
  fixedDiagnostic("fatal bridge failure");
  process.exit(1);
});
