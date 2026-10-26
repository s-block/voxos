import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import type { LanguageModelV2 } from "@ai-sdk/provider";
import {
  AISdkClient,
  type ModelConfiguration,
} from "@browserbasehq/stagehand";

import type { BrowserTaskRequest } from "./contracts.js";

export interface StagehandModelSelection {
  model?: ModelConfiguration;
  llmClient?: AISdkClient;
}

class AgentCompatibleAISdkClient extends AISdkClient {
  private readonly languageModel: LanguageModelV2;

  constructor(model: LanguageModelV2) {
    super({ model });
    this.languageModel = model;
  }

  getLanguageModel(): LanguageModelV2 {
    return this.languageModel;
  }
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
    llmClient: new AgentCompatibleAISdkClient(provider(request.model.modelName)),
  };
}
