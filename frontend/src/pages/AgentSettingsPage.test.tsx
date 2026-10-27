import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  loadDefaultAgent,
  type TextProviderSettings,
  type VoiceProviderSettings,
} from "../api";
import { AgentSettingsPage } from "./AgentSettingsPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    loadDefaultAgent: vi.fn(),
    updateAgent: vi.fn(),
  };
});

describe("AgentSettingsPage model policy", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(loadDefaultAgent).mockResolvedValue({
      created_at: "2026-07-18T09:00:00Z",
      description: null,
      display_name: "Voxos",
      id: "agent-1",
      is_default: true,
      owner_user_id: "user-1",
      slug: "default",
      status: "active",
      updated_at: "2026-07-18T09:00:00Z",
    });
  });

  it("shows model pricing and saves the explicit subagent policy", async () => {
    const user = userEvent.setup();
    const onSaveText = vi.fn().mockResolvedValue(true);
    const onSaveImageGeneration = vi.fn().mockResolvedValue(true);

    render(
      <AgentSettingsPage
        canEdit
        imageGenerationSettingsError={null}
        isImageGenerationSaving={false}
        isTextLoading={false}
        isTextSaving={false}
        isVoiceLoading={false}
        isVoiceSaving={false}
        onSaveImageGeneration={onSaveImageGeneration}
        onSaveText={onSaveText}
        onSaveVoice={vi.fn().mockResolvedValue(true)}
        provider={voiceProviderSettings.providers[0]}
        providerSettings={voiceProviderSettings}
        settings={voiceProviderSettings.active_settings}
        settingsError={null}
        textProviderSettings={textProviderSettings}
        textSettings={textProviderSettings.active_settings}
        textSettingsError={null}
      />,
    );

    expect(screen.getByText(/Audio: \$10 input/)).toBeInTheDocument();
    expect(screen.getByText(/\$5 input, \$0.50 cached input/)).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: /GPT-5.6 Luna/ }),
    ).toBeChecked();

    await user.selectOptions(
      screen.getAllByLabelText("Model")[1],
      "gpt-5.4-mini",
    );
    expect(screen.getByText(/\$0.75 input and \$4.50 output/)).toBeInTheDocument();
    await user.selectOptions(
      screen.getByLabelText("Default subagent model"),
      "gpt-5.6-luna",
    );
    await user.click(screen.getByRole("button", { name: "Save text defaults" }));

    expect(onSaveText).toHaveBeenCalledWith(
      expect.objectContaining({
        allowedSubagentModels: [
          "gpt-5.6-sol",
          "gpt-5.6-terra",
          "gpt-5.6-luna",
          "gpt-5.5",
          "gpt-5.4-mini",
          "gpt-5.4-nano",
        ],
        model: "gpt-5.4-mini",
        subagentModel: "gpt-5.6-luna",
      }),
    );
  });

  it("offers new catalog models when the saved subagent allowlist is stale", async () => {
    const user = userEvent.setup();
    const onSaveText = vi.fn().mockResolvedValue(true);
    const onSaveImageGeneration = vi.fn().mockResolvedValue(true);

    render(
      <AgentSettingsPage
        canEdit
        imageGenerationSettingsError={null}
        isImageGenerationSaving={false}
        isTextLoading={false}
        isTextSaving={false}
        isVoiceLoading={false}
        isVoiceSaving={false}
        onSaveImageGeneration={onSaveImageGeneration}
        onSaveText={onSaveText}
        onSaveVoice={vi.fn().mockResolvedValue(true)}
        provider={voiceProviderSettings.providers[0]}
        providerSettings={voiceProviderSettings}
        settings={voiceProviderSettings.active_settings}
        settingsError={null}
        textProviderSettings={nvidiaTextProviderSettings}
        textSettings={nvidiaTextProviderSettings.active_settings}
        textSettingsError={null}
      />,
    );

    const defaultSubagentModel = screen.getByLabelText(
      "Default subagent model",
    );
    const textModel = screen.getAllByLabelText("Model")[1];
    expect(
      within(textModel).getByRole("option", { name: "GPT-OSS 120B" }),
    ).toBeInTheDocument();
    expect(
      within(defaultSubagentModel).getByRole("option", {
        name: "GPT-OSS 120B",
      }),
    ).toBeInTheDocument();

    await user.selectOptions(defaultSubagentModel, "openai/gpt-oss-120b");

    expect(
      screen.getByRole("checkbox", { name: /GPT-OSS 120B/ }),
    ).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Save text defaults" }));
    expect(onSaveText).toHaveBeenCalledWith(
      expect.objectContaining({
        allowedSubagentModels: [
          "nvidia/nemotron-3-nano-30b-a3b",
          "openai/gpt-oss-120b",
        ],
        subagentModel: "openai/gpt-oss-120b",
      }),
    );

    expect(screen.getByLabelText("Image provider")).toHaveValue("nvidia");
    await user.selectOptions(
      screen.getByLabelText("Image model"),
      "black-forest-labs/flux.1-schnell",
    );
    expect(screen.getByText(/one to four diffusion steps/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save image defaults" }));
    expect(onSaveImageGeneration).toHaveBeenCalledWith({
      model: "black-forest-labs/flux.1-schnell",
      provider: "nvidia",
    });
  });

  it("lets users inspect image models for providers without a saved key", async () => {
    const user = userEvent.setup();
    const openai = textProviderSettings.providers[0];
    const xai = {
      ...openai,
      active: false,
      configured: false,
      credential_last_four: null,
      default_image_generation_model: "grok-imagine-image-quality",
      image_generation_model_options: [
        {
          description: "Highest-quality Grok image generation and editing model.",
          docs_url:
            "https://docs.x.ai/developers/models/grok-imagine-image-quality",
          hosted_api_supports_image_editing: true,
          label: "Grok Imagine Image Quality",
          model: "grok-imagine-image-quality",
          pricing_summary: "$0.05 per 1K output.",
          recommended: true,
          supports_image_editing: true,
        },
      ],
      label: "xAI",
      provider: "xai" as const,
    };

    render(
      <AgentSettingsPage
        canEdit
        imageGenerationSettingsError={null}
        isImageGenerationSaving={false}
        isTextLoading={false}
        isTextSaving={false}
        isVoiceLoading={false}
        isVoiceSaving={false}
        onSaveImageGeneration={vi.fn().mockResolvedValue(true)}
        onSaveText={vi.fn().mockResolvedValue(true)}
        onSaveVoice={vi.fn().mockResolvedValue(true)}
        provider={voiceProviderSettings.providers[0]}
        providerSettings={voiceProviderSettings}
        settings={voiceProviderSettings.active_settings}
        settingsError={null}
        textProviderSettings={{
          ...textProviderSettings,
          providers: [...textProviderSettings.providers, xai],
        }}
        textSettings={textProviderSettings.active_settings}
        textSettingsError={null}
      />,
    );

    await user.selectOptions(screen.getByLabelText("Image provider"), "xai");

    expect(screen.getByLabelText("Image model")).toHaveValue(
      "grok-imagine-image-quality",
    );
    expect(screen.getByText(/Highest-quality Grok/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save image defaults" })).toBeDisabled();
  });
});

const voiceProviderSettings: VoiceProviderSettings = {
  active_provider: "openai_realtime",
  active_settings: {
    agent_instructions: "You are a helpful voice assistant.",
    configured: true,
    initial_reply_instructions: "Say hello.",
    model: "gpt-realtime-2.1-mini",
    preferred_language: "English",
    provider: "openai_realtime",
    voice: "marin",
  },
  providers: [
    {
      active: true,
      api_key_label: "OpenAI API key", // pragma: allowlist secret
      configured: true,
      credential_last_four: "test",
      default_model: "gpt-realtime-2.1-mini",
      default_voice: "marin",
      docs_url: "https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini",
      label: "OpenAI",
      library: "livekit-agents[openai]",
      logo: { domain: "openai.com", label: "OpenAI", source: "domain" },
      model_options: [
        {
          description: "Fast and economical realtime voice.",
          label: "GPT Realtime 2.1 mini",
          model: "gpt-realtime-2.1-mini",
          pricing_summary: "Audio: $10 input and $20 output per 1M tokens.",
          recommended: true,
          supports_image_input: false,
          supports_tool_calling: true,
          supports_browser_use: false,
        },
      ],
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
  image_input_enabled: true,
  active_settings: {
    agent_instructions: "You are a helpful assistant.",
    allowed_subagent_models: [
      "gpt-5.6-sol",
      "gpt-5.6-terra",
      "gpt-5.6-luna",
      "gpt-5.5",
      "gpt-5.4-mini",
      "gpt-5.4-nano",
    ],
    configured: true,
    image_generation_model: "gpt-image-2",
    image_generation_provider: "openai",
    model: "gpt-5.6-sol",
    preferred_language: "English",
    provider: "openai",
    subagent_model: "gpt-5.6-terra",
    temperature: null,
  },
  providers: [
    {
      active: true,
      allowed_subagent_models: [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
      ],
      api_key_label: "OpenAI API key", // pragma: allowlist secret
      configured: true,
      credential_last_four: "test",
      default_model: "gpt-5.6-sol",
      default_multimodal_model: "gpt-5.6-sol",
      default_browser_model: "gpt-5.6-sol",
      default_image_generation_model: "gpt-image-2",
      default_subagent_model: "gpt-5.6-terra",
      docs_url: "https://developers.openai.com/api/docs/models",
      label: "OpenAI",
      logo: { domain: "openai.com", label: "OpenAI", source: "domain" },
      image_generation_model_options: [
        {
          description: "Current OpenAI image generation and editing model.",
          docs_url: "https://developers.openai.com/api/docs/models/gpt-image-2",
          label: "GPT Image 2",
          model: "gpt-image-2",
          pricing_summary: "Token-based image pricing.",
          recommended: true,
          supports_image_editing: true,
          hosted_api_supports_image_editing: true,
        },
      ],
      model_options: [
        {
          description: "Flagship agentic model.",
          label: "GPT-5.6 Sol",
          model: "gpt-5.6-sol",
          pricing_summary: "$5 input, $0.50 cached input, and $30 output per 1M tokens.",
          recommended: true,
          supports_image_input: true,
          supports_tool_calling: true,
          supports_browser_use: true,
        },
        {
          description: "Balanced delegated model.",
          label: "GPT-5.6 Terra",
          model: "gpt-5.6-terra",
          pricing_summary: "$2.50 input and $15 output per 1M tokens.",
          recommended: false,
          supports_image_input: true,
          supports_tool_calling: true,
          supports_browser_use: true,
        },
        {
          description: "Economical delegated model.",
          label: "GPT-5.6 Luna",
          model: "gpt-5.6-luna",
          pricing_summary: "$1 input and $6 output per 1M tokens.",
          recommended: false,
          supports_image_input: true,
          supports_tool_calling: true,
          supports_browser_use: true,
        },
        {
          description: "Previous frontier model with function calling.",
          label: "GPT-5.5",
          model: "gpt-5.5",
          pricing_summary: "$5 input and $30 output per 1M tokens.",
          recommended: false,
          supports_image_input: true,
          supports_tool_calling: true,
          supports_browser_use: true,
        },
        {
          description: "Fast tool-capable mini model.",
          label: "GPT-5.4 mini",
          model: "gpt-5.4-mini",
          pricing_summary: "$0.75 input and $4.50 output per 1M tokens.",
          recommended: false,
          supports_image_input: true,
          supports_tool_calling: true,
          supports_browser_use: true,
        },
        {
          description: "Low-cost model for simple tool-using tasks.",
          label: "GPT-5.4 nano",
          model: "gpt-5.4-nano",
          pricing_summary: "$0.20 input and $1.25 output per 1M tokens.",
          recommended: false,
          supports_image_input: true,
          supports_tool_calling: true,
          supports_browser_use: true,
        },
      ],
      provider: "openai",
      runtime_supported: true,
      suggested_models: [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
      ],
    },
  ],
};

const nvidiaTextProviderSettings: TextProviderSettings = {
  active_provider: "nvidia",
  image_input_enabled: true,
  active_settings: {
    agent_instructions: "You are a helpful assistant.",
    allowed_subagent_models: ["nvidia/nemotron-3-nano-30b-a3b"],
    configured: true,
    image_generation_model: "black-forest-labs/flux.2-klein-4b",
    image_generation_provider: "nvidia",
    model: "nvidia/nemotron-3-nano-30b-a3b",
    preferred_language: "English",
    provider: "nvidia",
    subagent_model: "nvidia/nemotron-3-nano-30b-a3b",
    temperature: null,
  },
  providers: [
    {
      active: true,
      allowed_subagent_models: ["nvidia/nemotron-3-nano-30b-a3b"],
      api_key_label: "NVIDIA API key", // pragma: allowlist secret
      configured: true,
      credential_last_four: "test",
      default_model: "openai/gpt-oss-20b",
      default_multimodal_model: "stepfun-ai/step-3.7-flash",
      default_browser_model: "stepfun-ai/step-3.7-flash",
      default_image_generation_model: "black-forest-labs/flux.2-klein-4b",
      default_subagent_model: "openai/gpt-oss-20b",
      docs_url: "https://docs.api.nvidia.com/nim/re/reference/llm-apis",
      label: "NVIDIA API Catalog",
      logo: { domain: "nvidia.com", label: "NVIDIA", source: "domain" },
      image_generation_model_options: [
        {
          description: "Fast image generation and editing model.",
          docs_url:
            "https://docs.api.nvidia.com/nim/reference/black-forest-labs-flux_2-klein-4b",
          label: "FLUX.2 Klein 4B",
          model: "black-forest-labs/flux.2-klein-4b",
          pricing_summary: "NVIDIA deployment pricing applies.",
          recommended: true,
          supports_image_editing: true,
          hosted_api_supports_image_editing: false,
        },
        {
          description: "Generates images in one to four diffusion steps.",
          docs_url:
            "https://docs.api.nvidia.com/nim/reference/black-forest-labs-flux_1-schnell",
          label: "FLUX.1 Schnell",
          model: "black-forest-labs/flux.1-schnell",
          pricing_summary: "NVIDIA deployment pricing applies.",
          recommended: false,
          supports_image_editing: false,
          hosted_api_supports_image_editing: false,
        },
      ],
      model_options: [
        {
          description: "Fast reasoning model.",
          label: "Nemotron 3 Nano 30B A3B",
          model: "nvidia/nemotron-3-nano-30b-a3b",
          pricing_summary: null,
          recommended: true,
          supports_image_input: false,
          supports_tool_calling: true,
          supports_browser_use: false,
        },
        {
          description: "General-purpose reasoning model.",
          label: "GPT-OSS 120B",
          model: "openai/gpt-oss-120b",
          pricing_summary: null,
          recommended: false,
          supports_image_input: false,
          supports_tool_calling: true,
          supports_browser_use: false,
        },
      ],
      provider: "nvidia",
      runtime_supported: true,
      suggested_models: ["nvidia/nemotron-3-nano-30b-a3b"],
    },
  ],
};
