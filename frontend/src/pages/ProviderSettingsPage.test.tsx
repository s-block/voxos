import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { TextProviderSettings, VoiceProviderSettings } from "../api";
import { ProviderSettingsPage } from "./ProviderSettingsPage";

describe("ProviderSettingsPage", () => {
  it("shows NVIDIA model routes and saves it as a text-chat provider", async () => {
    const user = userEvent.setup();
    const saveTextProviderKey = vi.fn().mockResolvedValue(true);

    render(
      <ProviderSettingsPage
        canEdit
        isTextCredentialSaving={false}
        isTextSettingsLoading={false}
        isVoiceCredentialSaving={false}
        isVoiceSettingsLoading={false}
        saveOpenAICompatibleProvider={vi.fn().mockResolvedValue(true)}
        saveTextProviderKey={saveTextProviderKey}
        saveVoiceProviderKey={vi.fn().mockResolvedValue(true)}
        textProviderSettings={textProviderSettings}
        textSettingsError={null}
        voiceProviderSettings={voiceProviderSettings}
        voiceSettingsError={null}
      />,
    );

    expect(screen.getByText("Voice and text")).toBeInTheDocument();
    expect(screen.getByText("NVIDIA API Catalog")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save and activate" })).not.toBeInTheDocument();

    const nvidiaCard = screen.getByText("NVIDIA API Catalog").closest("article");
    expect(nvidiaCard).not.toBeNull();
    expect(within(nvidiaCard!).getByText("openai/gpt-oss-20b")).toBeInTheDocument();
    expect(
      within(nvidiaCard!).getAllByText("stepfun-ai/step-3.7-flash"),
    ).toHaveLength(2);
    const savedKey = "fixture-" + "credential";
    await user.type(within(nvidiaCard!).getByLabelText("NVIDIA API key"), savedKey);
    await user.click(within(nvidiaCard!).getByRole("button", { name: "Save key" }));

    expect(saveTextProviderKey).toHaveBeenCalledWith({
      apiKey: savedKey,
      provider: "nvidia",
    });
  });

  it("saves an OpenAI-compatible endpoint with explicit model IDs", async () => {
    const user = userEvent.setup();
    const saveOpenAICompatibleProvider = vi.fn().mockResolvedValue(true);

    render(
      <ProviderSettingsPage
        canEdit
        isTextCredentialSaving={false}
        isTextSettingsLoading={false}
        isVoiceCredentialSaving={false}
        isVoiceSettingsLoading={false}
        saveOpenAICompatibleProvider={saveOpenAICompatibleProvider}
        saveTextProviderKey={vi.fn().mockResolvedValue(true)}
        saveVoiceProviderKey={vi.fn().mockResolvedValue(true)}
        textProviderSettings={textProviderSettings}
        textSettingsError={null}
        voiceProviderSettings={voiceProviderSettings}
        voiceSettingsError={null}
      />,
    );

    const providerCard = screen.getByText("OpenAI compatible").closest("article");
    expect(providerCard).not.toBeNull();
    await user.type(
      within(providerCard!).getByLabelText("Base URL"),
      "http://host.docker.internal:11434/v1",
    );
    await user.type(
      within(providerCard!).getByLabelText("Available model IDs"),
      "gpt-oss:20b{enter}qwen3:8b{enter}gpt-oss:20b",
    );
    await user.type(
      within(providerCard!).getByLabelText("Bearer API key (optional)"),
      "local-token",
    );
    await user.click(
      within(providerCard!).getByRole("button", { name: "Save endpoint" }),
    );

    expect(saveOpenAICompatibleProvider).toHaveBeenCalledWith({
      apiKey: "local-token", // pragma: allowlist secret
      baseUrl: "http://host.docker.internal:11434/v1",
      models: ["gpt-oss:20b", "qwen3:8b"],
    });
  });
});

const voiceProviderSettings: VoiceProviderSettings = {
  active_provider: "openai_realtime",
  active_settings: {
    agent_instructions: "You are a helpful voice assistant.",
    configured: false,
    initial_reply_instructions: "Greet the user.",
    model: "gpt-realtime-2.1-mini",
    preferred_language: "English",
    provider: "openai_realtime",
    voice: "marin",
  },
  providers: [
    {
      active: true,
      api_key_label: "OpenAI API key", // pragma: allowlist secret
      configured: false,
      credential_last_four: null,
      default_model: "gpt-realtime-2.1-mini",
      default_voice: "marin",
      docs_url: "https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini",
      label: "OpenAI",
      library: "livekit-agents[openai]",
      logo: { domain: "openai.com", label: "OpenAI", source: "domain" },
      model_options: [],
      note: "Ready.",
      provider: "openai_realtime",
      runtime_supported: true,
      suggested_models: ["gpt-realtime-2.1-mini"],
      suggested_voices: ["marin"],
    },
  ],
};

const textProviderSettings: TextProviderSettings = {
  active_provider: "openai",
  active_settings: {
    agent_instructions: "You are a helpful assistant.",
    allowed_subagent_models: ["gpt-5.4-mini"],
    configured: false,
    image_generation_model: "gpt-image-2",
    image_generation_provider: "openai",
    model: "gpt-5.4-mini",
    preferred_language: "English",
    provider: "openai",
    subagent_model: "gpt-5.4-mini",
    temperature: null,
  },
  image_input_enabled: true,
  providers: [
    {
      active: true,
      allowed_subagent_models: ["gpt-5.4-mini"],
      api_key_label: "OpenAI API key", // pragma: allowlist secret
      configured: false,
      credential_last_four: null,
      default_model: "gpt-5.4-mini",
      default_multimodal_model: "gpt-5.4-mini",
      default_browser_model: "gpt-5.4-mini",
      default_image_generation_model: "gpt-image-2",
      default_subagent_model: "gpt-5.4-mini",
      docs_url: "https://developers.openai.com/api/docs/models",
      label: "OpenAI",
      logo: { domain: "openai.com", label: "OpenAI", source: "domain" },
      image_generation_model_options: [],
      model_options: [],
      provider: "openai",
      runtime_supported: true,
      suggested_models: ["gpt-5.4-mini"],
    },
    {
      active: false,
      allowed_subagent_models: [],
      api_key_label: "API key (optional)", // pragma: allowlist secret
      base_url: null,
      configured: false,
      credential_last_four: null,
      default_model: "local-model",
      default_multimodal_model: null,
      default_browser_model: null,
      default_image_generation_model: null,
      default_subagent_model: "local-model",
      docs_url: "https://docs.ollama.com/api/openai-compatibility",
      label: "OpenAI compatible",
      logo: { domain: "openai.com", label: "OpenAI compatible", source: "domain" },
      image_generation_model_options: [],
      model_options: [],
      provider: "openai_compatible",
      runtime_supported: true,
      suggested_models: [],
    },
    {
      active: false,
      allowed_subagent_models: ["openai/gpt-oss-20b"],
      api_key_label: "NVIDIA API key", // pragma: allowlist secret
      configured: false,
      credential_last_four: null,
      default_model: "openai/gpt-oss-20b",
      default_multimodal_model: "stepfun-ai/step-3.7-flash",
      default_browser_model: "stepfun-ai/step-3.7-flash",
      default_image_generation_model: "black-forest-labs/flux.2-klein-4b",
      default_subagent_model: "openai/gpt-oss-20b",
      docs_url: "https://docs.api.nvidia.com/nim/re/reference/llm-apis",
      label: "NVIDIA API Catalog",
      logo: { domain: "nvidia.com", label: "NVIDIA", source: "domain" },
      image_generation_model_options: [],
      model_options: [
        {
          description: "Fast text and tool-calling model.",
          label: "GPT-OSS 20B",
          model: "openai/gpt-oss-20b",
          pricing_summary: null,
          recommended: true,
          supports_image_input: false,
          supports_tool_calling: true,
          supports_browser_use: false,
        },
        {
          description: "Multimodal browser and image model.",
          label: "Step 3.7 Flash",
          model: "stepfun-ai/step-3.7-flash",
          pricing_summary: null,
          recommended: false,
          supports_image_input: true,
          supports_tool_calling: true,
          supports_browser_use: true,
        },
      ],
      provider: "nvidia",
      runtime_supported: true,
      suggested_models: ["openai/gpt-oss-20b", "stepfun-ai/step-3.7-flash"],
    },
  ],
};
