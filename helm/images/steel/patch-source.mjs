import { readFileSync, writeFileSync } from "node:fs";

function writeModified(path, content) {
  writeFileSync(path, "// Modified by Voxos; see voxos-build/SOURCE.md in the source archive.\n" + content);
}

const cdpPath = "/source/api/src/services/cdp/cdp.service.ts";
let cdp = readFileSync(cdpPath, "utf8");
if (!cdp.includes('          "--remote-allow-origins=*",')) throw new Error("Upstream Chromium launch defaults changed");
cdp = cdp.replace('          "--remote-allow-origins=*",\n', "").replace("IsolateOrigins,site-per-process,", "");
writeModified(cdpPath, cdp);
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
writeModified(indexPath, index);

const eventsPath = "/source/api/src/services/cdp/instrumentation/page-events.ts";
let events = readFileSync(eventsPath, "utf8");
const oldError = "error: { message: err?.message, stack: err?.stack }";
if (!events.includes(oldError)) throw new Error("Upstream page-error instrumentation changed");
events = events.replaceAll(oldError, "error: { message: err instanceof Error ? err.message : 'Unknown browser error', stack: err instanceof Error ? err.stack : undefined }");
writeModified(eventsPath, events);

const configPath = "/source/api/src/config.ts";
let config = readFileSync(configPath, "utf8");
if (!config.includes("  production: {},")) throw new Error("Upstream production logging configuration changed");
config = config.replace("  production: {},", '  production: { level: process.env.LOG_LEVEL || "warn" },');
writeModified(configPath, config);

const loggerPath = "/source/api/src/services/cdp/instrumentation/browser-logger.ts";
let logger = readFileSync(loggerPath, "utf8");
const consoleDefault = "const enableConsoleLogging = options.enableConsoleLogging ?? true;";
if (!logger.includes(consoleDefault)) throw new Error("Upstream browser-event logging changed");
logger = logger.replace(consoleDefault, "const enableConsoleLogging = false;");
writeModified(loggerPath, logger);

const sessionsPath = "/source/ui/src/contexts/sessions-context/sessions-context.tsx";
let sessions = readFileSync(sessionsPath, "utf8");
const routerType = 'import { ErrorResponse } from "@remix-run/router";';
if (!sessions.includes(routerType)) throw new Error("Upstream UI session error typing changed");
sessions = sessions.replace(routerType, 'import { GetSessionDetailsError } from "@/steel-client";').replace("useQuery<SessionDetails, ErrorResponse>", "useQuery<SessionDetails, GetSessionDetailsError>");
writeModified(sessionsPath, sessions);

// Zod 4 validates records with explicit keys and supplies defaults before transforms.
const envPath = "/source/api/src/env.ts";
let env = readFileSync(envPath, "utf8");
env = env.replaceAll('z.record(z.string())', 'z.record(z.string(), z.string())')
  .replaceAll('.default("false")', '.prefault("false")')
  .replaceAll('.default("true")', '.prefault("true")')
  .replaceAll('.default("")', '.prefault("")');
writeModified(envPath, env);
for (const [path, before, after] of [
  ["modules/logs/logs.schema.ts", "z.record(z.any())", "z.record(z.string(), z.any())"],
  ["modules/selenium/selenium.schema.ts", "z.record(z.string())", "z.record(z.string(), z.string())"],
]) {
  const schemaPath = `/source/api/src/${path}`;
  const schema = readFileSync(schemaPath, "utf8");
  if (!schema.includes(before)) throw new Error(`Upstream record schema changed: ${path}`);
  writeModified(schemaPath, schema.replaceAll(before, after));
}
const schemaPath = "/source/api/src/utils/schema.ts";
let schema = readFileSync(schemaPath, "utf8");
const oldConversion = `zodToJsonSchema(zodSchema, {
    target: "openApi3",
    $refStrategy: "none",
    errorMessages: opts.errorMessages,
  })`;
if (!schema.includes(oldConversion)) throw new Error("Upstream JSON Schema conversion changed");
schema = schema.replace('import { zodToJsonSchema } from "zod-to-json-schema";\n', "")
  .replace(oldConversion, `z.toJSONSchema(zodSchema, {
    target: "draft-7",
    reused: "inline",
    unrepresentable: ({ zodSchema }) => zodSchema._zod.def.type === "date"
      ? { type: "string", format: "date-time" }
      : "throw",
  })`);
writeModified(schemaPath, schema);
