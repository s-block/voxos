import { readFileSync, writeFileSync } from "node:fs";
const cdpPath = "/source/api/src/services/cdp/cdp.service.ts";
let cdp = readFileSync(cdpPath, "utf8");
if (!cdp.includes('          "--remote-allow-origins=*",')) throw new Error("Upstream Chromium launch defaults changed");
cdp = cdp.replace('          "--remote-allow-origins=*",\n', "").replace("IsolateOrigins,site-per-process,", "");
writeFileSync(cdpPath, cdp);
const indexPath = "/source/api/src/index.ts";
let index = readFileSync(indexPath, "utf8");
const marker = "const setupServer = async () => {";
if (!index.includes(marker)) throw new Error("Upstream Steel server startup changed");
index = 'import { timingSafeEqual } from "node:crypto";\n' + index.replace(marker, `
const serviceToken = process.env.STEEL_SERVICE_TOKEN;
if (!serviceToken || !/^[a-f0-9]{64}$/.test(serviceToken)) throw new Error("Steel service authentication is required");
const expectedAuthorization = Buffer.from("Bearer " + serviceToken);
function authorized(value: string | undefined): boolean {
  if (!value) return false;
  const supplied = Buffer.from(value);
  return supplied.length === expectedAuthorization.length && timingSafeEqual(supplied, expectedAuthorization);
}
server.addHook("onRequest", async (request, reply) => {
  if (request.raw.url?.split("?")[0] === "/v1/health") return;
  if (!authorized(request.headers.authorization)) {
    return reply.header("www-authenticate", "Bearer").code(401).send({ error: "not_authenticated" });
  }
});
server.server.prependListener("upgrade", (request, socket) => {
  if (!authorized(request.headers.authorization)) socket.destroy();
});
${marker}`);
writeFileSync(indexPath, index);

const eventsPath = "/source/api/src/services/cdp/instrumentation/page-events.ts";
let events = readFileSync(eventsPath, "utf8");
const oldError = "error: { message: err?.message, stack: err?.stack }";
if (!events.includes(oldError)) throw new Error("Upstream page-error instrumentation changed");
events = events.replaceAll(oldError, "error: { message: err instanceof Error ? err.message : 'Unknown browser error', stack: err instanceof Error ? err.stack : undefined }");
writeFileSync(eventsPath, events);

const configPath = "/source/api/src/config.ts";
let config = readFileSync(configPath, "utf8");
if (!config.includes("  production: {},")) throw new Error("Upstream production logging configuration changed");
config = config.replace("  production: {},", '  production: { level: process.env.LOG_LEVEL || "warn" },');
writeFileSync(configPath, config);

const loggerPath = "/source/api/src/services/cdp/instrumentation/browser-logger.ts";
let logger = readFileSync(loggerPath, "utf8");
const consoleDefault = "const enableConsoleLogging = options.enableConsoleLogging ?? true;";
if (!logger.includes(consoleDefault)) throw new Error("Upstream browser-event logging changed");
logger = logger.replace(consoleDefault, "const enableConsoleLogging = false;");
writeFileSync(loggerPath, logger);

const sessionsPath = "/source/ui/src/contexts/sessions-context/sessions-context.tsx";
let sessions = readFileSync(sessionsPath, "utf8");
const routerType = 'import { ErrorResponse } from "@remix-run/router";';
if (!sessions.includes(routerType)) throw new Error("Upstream UI session error typing changed");
sessions = sessions.replace(routerType, 'import { GetSessionDetailsError } from "@/steel-client";').replace("useQuery<SessionDetails, ErrorResponse>", "useQuery<SessionDetails, GetSessionDetailsError>");
writeFileSync(sessionsPath, sessions);
