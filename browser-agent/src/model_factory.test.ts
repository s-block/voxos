import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer } from "node:http";
import { test } from "node:test";

import { OpenAIClient } from "@browserbasehq/stagehand/lib/v3/llm/OpenAIClient.js";
import { z } from "zod";

import { browserTaskRequestSchema } from "./contracts.js";
import { stagehandModel } from "./model_factory.js";

for (const adapter of ["stagehand_native", "openai_compatible"] as const) {
  test(`${adapter} keeps structured output and per-request credentials with patched SDKs`, async () => {
    const requests: { authorization: string | undefined; path: string; body: Record<string, unknown> }[] = [];
    const server = createServer(async (request, response) => {
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(Buffer.from(chunk));
      requests.push({
        authorization: request.headers.authorization,
        path: request.url ?? "",
        body: JSON.parse(Buffer.concat(chunks).toString()),
      });
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify({
        id: "completion-fixture", object: "chat.completion", created: 1,
        model: "gpt-4o-mini",
        choices: [{ index: 0, message: { role: "assistant", content: '{"answer":"verified"}' }, finish_reason: "stop" }],
        usage: { prompt_tokens: 2, completion_tokens: 3, total_tokens: 5 },
      }));
    });
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    try {
      const address = server.address();
      assert.ok(address && typeof address !== "string");
      const selection = stagehandModel(browserTaskRequestSchema.parse({
        instruction: "Read the page", startUrl: "https://docs.example.com/",
        allowedOrigins: ["https://docs.example.com"], maxSteps: 1,
        model: { adapter, modelName: "gpt-4o-mini", apiKey: "sdk-test-key", baseUrl: `http://127.0.0.1:${address.port}/v1` }, // pragma: allowlist secret
      }));
      const client = selection.llmClient ?? new OpenAIClient({
        modelName: "gpt-4o-mini",
        clientOptions: { apiKey: "sdk-test-key", baseURL: `http://127.0.0.1:${address.port}/v1` }, // pragma: allowlist secret
        logger: () => {},
      });
      const result = await client.createChatCompletion<{ data: { answer: string } }>({
        options: {
          messages: [{ role: "user", content: "Read the page" }],
          response_model: { name: "answer", schema: z.object({ answer: z.string() }) },
        },
        logger: () => {},
      });
      assert.equal(result.data.answer, "verified");
      assert.equal(requests.length, 1);
      assert.equal(requests[0]?.authorization, "Bearer sdk-test-key");
      assert.equal(requests[0]?.path, "/v1/chat/completions");
      assert.equal(requests[0]?.body.model, "gpt-4o-mini");
      assert.equal((requests[0]?.body.response_format as { type: string }).type, "json_schema");
    } finally {
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    }
  });
}
