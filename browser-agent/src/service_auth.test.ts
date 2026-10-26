import assert from "node:assert/strict";
import { test } from "node:test";

import { configuredServiceToken, isAuthorizedServiceCall } from "./service_auth.js";

const serviceToken = "test-browser-service-token-with-enough-length";

test("only the matching service bearer credential is accepted", () => {
  assert.equal(isAuthorizedServiceCall(`Bearer ${serviceToken}`, serviceToken), true);
  assert.equal(isAuthorizedServiceCall(`bearer ${serviceToken}`, serviceToken), true);
  for (const supplied of [undefined, "", serviceToken, `Basic ${serviceToken}`, "Bearer incorrect", `Bearer ${serviceToken} extra`]) {
    assert.equal(isAuthorizedServiceCall(supplied, serviceToken), false);
  }
});

test("missing or unsafe token configuration fails closed", () => {
  assert.equal(configuredServiceToken(serviceToken), serviceToken);
  for (const invalid of [undefined, "", "short", `${serviceToken}\n`, `${serviceToken} `]) {
    assert.throws(() => configuredServiceToken(invalid), /BROWSER_AGENT_SERVICE_TOKEN/);
  }
});
