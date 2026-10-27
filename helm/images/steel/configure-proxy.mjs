import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
const token = process.env.STEEL_SERVICE_TOKEN;
if (!token || !/^[a-f0-9]{64}$/.test(token)) throw new Error("STEEL_SERVICE_TOKEN must contain 32 random bytes as hexadecimal");
mkdirSync("/tmp/steel-nginx", { recursive: true });
writeFileSync("/tmp/steel-nginx.conf", readFileSync("/app/nginx.conf.template", "utf8").replace("__TOKEN__", token), { mode: 0o600 });
