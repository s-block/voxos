import assert from "node:assert/strict";
import { test } from "node:test";

import { browserTaskRequestSchema } from "./contracts.js";
import { browserSystemPrompt } from "./task_policy.js";

const baseRequest = {
  instruction: "Compare the support plans and report their differences.",
  startUrl: "https://docs.example.com/plans",
  allowedOrigins: ["https://docs.example.com"],
  actionMode: "read_only" as const,
  maxSteps: 20,
  variables: {},
  model: {
    adapter: "openai_compatible" as const,
    modelName: "general-model",
    baseUrl: "https://models.example.com/v1",
    apiKey: "secret-key", // pragma: allowlist secret
  },
};

test("accepts a general browser task without credentials", () => {
  const request = browserTaskRequestSchema.parse(baseRequest);

  assert.equal(request.instruction, baseRequest.instruction);
  assert.deepEqual(request.variables, {});
});

test("accepts arbitrary named task variables", () => {
  const request = browserTaskRequestSchema.parse({
    ...baseRequest,
    variables: {
      customer_reference: {
        value: "reference-value",
        description: "The approved customer reference",
      },
    },
  });

  assert.equal(request.variables.customer_reference?.description, "The approved customer reference");
});

test("generic policy contains no account retrieval workflow", () => {
  const prompt = browserSystemPrompt(browserTaskRequestSchema.parse(baseRequest));

  assert.doesNotMatch(prompt, /account dashboard|sign-in flow|requested facts/i);
  assert.match(prompt, /general browser automation agent/i);
  assert.match(prompt, /read-only task/i);
});

test("requires the start URL origin to be authorized", () => {
  const result = browserTaskRequestSchema.safeParse({
    ...baseRequest,
    allowedOrigins: ["https://other.example.com"],
  });

  assert.equal(result.success, false);
});
