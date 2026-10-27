import { timingSafeEqual } from "node:crypto";

export function configuredServiceToken(value: string | undefined, settingName = "BROWSER_AGENT_SERVICE_TOKEN"): string {
  if (value === undefined || !/^[\x21-\x7e]{32,512}$/.test(value)) {
    throw new Error(`${settingName} must be a secret containing 32 to 512 printable non-space characters`);
  }
  return value;
}

export function isAuthorizedServiceCall(header: string | undefined, token: string): boolean {
  const match = /^Bearer ([\x21-\x7e]+)$/i.exec(header ?? "");
  if (match === null || match[1] === undefined) return false;
  const supplied = Buffer.from(match[1], "utf8");
  const expected = Buffer.from(token, "utf8");
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}
