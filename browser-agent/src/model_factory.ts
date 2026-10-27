import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import {
  AISdkClient,
  type ModelConfiguration,
} from "@browserbasehq/stagehand";

import type { BrowserTaskRequest } from "./contracts.js";

export interface StagehandModelSelection {
  model?: ModelConfiguration;
  llmClient?: AISdkClient;
}

export function stagehandModel(request: BrowserTaskRequest): StagehandModelSelection {
  if (request.model.adapter === "stagehand_native") {
    return {
      model: {
        modelName: request.model.modelName,
        apiKey: request.model.apiKey,
        baseURL: request.model.baseUrl,
      },
    };
  }

  const provider = createOpenAICompatible({
    name: "browser-runtime-model",
    apiKey: request.model.apiKey,
    baseURL: request.model.baseUrl,
    supportsStructuredOutputs: true,
  });
  return {
    llmClient: new AISdkClient({ model: provider(request.model.modelName) }),
  };
}
