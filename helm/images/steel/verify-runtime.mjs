import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
import { setTimeout as delay } from "node:timers/promises";

const require = createRequire("/app/api/package.json");
const WebSocket = require("ws");
const base = "http://127.0.0.1:3000";
const authorization = { Authorization: `Bearer ${process.env.STEEL_SERVICE_TOKEN}` };
for (let attempt = 0; ; attempt++) {
  try {
    assert.equal((await fetch(`${base}/health`)).status, 200);
    break;
  } catch (error) {
    if (attempt === 60) throw error;
    await delay(1000);
  }
}
assert.equal((await fetch(`${base}/v1/sessions`)).status, 401);
assert.equal((await fetch(`${base}/source/`)).status, 200);
assert.equal((await fetch(`${base}/source/steel-source.tar.gz`, { method: "HEAD" })).status, 200);
assert.equal((await fetch(`${base}/source/mupdf-1.28.1-source.tar.gz`, { method: "HEAD" })).status, 200);
assert.equal((await fetch("http://127.0.0.1:3001/v1/sessions")).status, 401);
assert.equal((await fetch(`${base}/v1/sessions`, { headers: authorization })).status, 200);
const created = await fetch(`${base}/v1/sessions`, {
  method: "POST", headers: { ...authorization, "Content-Type": "application/json" }, body: "{}",
});
assert.equal(created.status, 200);
const session = await created.json();
assert.ok(session.id);
const endpoint = new URL(session.websocketUrl);
endpoint.hostname = "127.0.0.1";
endpoint.port = "3000";
await new Promise((resolve, reject) => {
  const socket = new WebSocket(endpoint);
  const timeout = setTimeout(() => { socket.terminate(); reject(new Error("Unauthenticated CDP did not fail")); }, 10000);
  socket.on("open", () => { clearTimeout(timeout); socket.close(); reject(new Error("Unauthenticated CDP accepted")); });
  socket.on("unexpected-response", (_request, response) => {
    clearTimeout(timeout); response.resume(); socket.terminate();
    try { assert.equal(response.statusCode, 401); resolve(); } catch (error) { reject(error); }
  });
  socket.on("error", () => {});
});
await new Promise((resolve, reject) => {
  const socket = new WebSocket(endpoint, { headers: authorization });
  const timeout = setTimeout(() => { socket.terminate(); reject(new Error("Authenticated CDP timed out")); }, 15000);
  socket.on("open", () => socket.send(JSON.stringify({ id: 1, method: "Browser.getVersion" })));
  socket.on("message", (data) => {
    const message = JSON.parse(data.toString());
    try {
      assert.equal(message.error, undefined);
      if (message.id === 1) {
        assert.ok(message.result.product.startsWith("Chrome/"));
        socket.send(JSON.stringify({ id: 2, method: "Target.createTarget", params: { url: "data:text/html,<title>Sandbox verification</title>" } }));
      } else if (message.id === 2) {
        socket.send(JSON.stringify({ id: 3, method: "Target.attachToTarget", params: { targetId: message.result.targetId, flatten: true } }));
      } else if (message.id === 3) {
        socket.send(JSON.stringify({ id: 4, method: "Runtime.evaluate", sessionId: message.result.sessionId, params: { expression: "console.log('voxos_browser_log_fixture')" } }));
      } else if (message.id === 4) {
        clearTimeout(timeout); socket.close(); resolve();
      }
    } catch (error) { clearTimeout(timeout); socket.close(); reject(error); }
  });
  socket.on("error", reject);
});
await delay(500);
const chromium = readdirSync("/proc").filter((name) => /^\d+$/.test(name)).flatMap((pid) => {
  try {
    const args = readFileSync(`/proc/${pid}/cmdline`, "utf8").split("\0");
    if (!args[0].startsWith("/usr/lib/chromium/chromium")) return [];
    return [{ args, status: readFileSync(`/proc/${pid}/status`, "utf8") }];
  } catch { return []; } // Processes can exit during /proc traversal.
});
assert.ok(chromium.length > 0);
assert.ok(chromium.every(({ args }) => !args.join(" ").includes("--no-sandbox")));
const browser = chromium.find(({ args }) => !args.join(" ").includes("--type="));
assert.ok(browser && /^CapEff:\s+0+$/m.test(browser.status));
const renderers = chromium.filter(({ args }) => args.join(" ").includes("--type=renderer"));
assert.ok(renderers.length > 0);
assert.ok(renderers.every(({ status }) => /^Uid:\s+1000\s/m.test(status) && /^CapEff:\s+0+$/m.test(status) && /^Seccomp:\s+2$/m.test(status) && /^NSpid:\s+\d+\s+\d+\s+\d+/m.test(status)));
const released = await fetch(`${base}/v1/sessions/${session.id}/release`, {
  method: "POST", headers: { ...authorization, "Content-Type": "application/json" }, body: "{}",
});
assert.equal(released.status, 200);
console.log("Steel health, REST/CDP authentication, session lifecycle and renderer sandbox verified");
