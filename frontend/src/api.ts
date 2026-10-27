import { getFirebaseIdToken } from "./firebase";

export type BrandLogo = {
  domain: string;
  label: string;
  source: "domain";
};

export type ProviderModelOption = {
  model: string;
  label: string;
  description: string;
  pricing_summary: string | null;
  recommended: boolean;
  supports_image_input: boolean;
  supports_tool_calling: boolean;
  supports_browser_use: boolean;
};

export type ProviderImageGenerationModelOption = {
  model: string;
  label: string;
  description: string;
  pricing_summary: string | null;
  docs_url: string;
  recommended: boolean;
  supports_image_editing: boolean;
  hosted_api_supports_image_editing: boolean;
};

export type VoiceSettings = {
  configured: boolean;
  provider: VoiceProvider;
  model: string;
  voice: string;
  preferred_language: string;
  agent_instructions: string;
  initial_reply_instructions: string | null;
};

export type VoiceProvider =
  | "openai_realtime"
  | "google_gemini_live"
  | "hume_evi3"
  | "elevenlabs_conversational_ai"
  | "mistral_voxtral"
  | "grok_voice_agent";

export type VoiceProviderSummary = {
  provider: VoiceProvider;
  label: string;
  api_key_label: string;
  configured: boolean;
  active: boolean;
  runtime_supported: boolean;
  default_model: string;
  default_voice: string;
  suggested_models: string[];
  model_options: ProviderModelOption[];
  suggested_voices: string[];
  credential_last_four: string | null;
  library: string;
  docs_url: string;
  note: string;
  logo: BrandLogo;
};

export type VoiceProviderSettings = {
  active_provider: VoiceProvider;
  active_settings: VoiceSettings;
  providers: VoiceProviderSummary[];
};

export type TextProvider =
  | "openai"
  | "openai_compatible"
  | "xai"
  | "nvidia";

export type TextSettings = {
  configured: boolean;
  provider: TextProvider;
  model: string;
  preferred_language: string;
  agent_instructions: string;
  temperature: number | null;
  allowed_subagent_models: string[];
  subagent_model: string;
  image_generation_provider: TextProvider;
  image_generation_model: string;
};

export type TextProviderSummary = {
  provider: TextProvider;
  label: string;
  api_key_label: string;
  configured: boolean;
  active: boolean;
  runtime_supported: boolean;
  default_model: string;
  default_multimodal_model: string | null;
  default_browser_model: string | null;
  suggested_models: string[];
  model_options: ProviderModelOption[];
  default_image_generation_model: string | null;
  image_generation_model_options: ProviderImageGenerationModelOption[];
  default_subagent_model: string;
  allowed_subagent_models: string[];
  credential_last_four: string | null;
  base_url?: string | null;
  docs_url: string;
  logo: BrandLogo;
};

export type TextProviderSettings = {
  active_provider: TextProvider;
  active_settings: TextSettings;
  image_input_enabled: boolean;
  providers: TextProviderSummary[];
};

export type SaveTextProviderKeyRequest = {
  provider: TextProvider;
  api_key: string;
  activate: boolean;
};

export type SaveOpenAICompatibleProviderRequest = {
  base_url: string;
  available_models: string[];
  api_key: string | null;
};

export type ActivateTextProviderRequest = {
  provider: TextProvider;
};

export type SaveTextPreferencesRequest = {
  provider: TextProvider;
  model: string;
  preferred_language: string;
  agent_instructions: string;
  temperature: number | null;
  allowed_subagent_models: string[];
  subagent_model: string;
};

export type SaveImageGenerationPreferencesRequest = {
  provider: TextProvider;
  model: string;
};

export type SaveOpenAIKeyRequest = {
  openai_api_key: string;
};

export type SaveVoiceProviderKeyRequest = {
  provider: VoiceProvider;
  api_key: string;
  activate: boolean;
};

export type ActivateVoiceProviderRequest = {
  provider: VoiceProvider;
};

export type SaveVoicePreferencesRequest = {
  provider: VoiceProvider;
  model: string;
  voice: string;
  preferred_language: string;
  agent_instructions: string;
  initial_reply_instructions: string | null;
};

export type LiveKitSettings = {
  configured: boolean;
  livekit_url: string | null;
};

export type SaveLiveKitSettingsRequest = {
  livekit_url: string;
  livekit_api_key: string;
  livekit_api_secret: string;
};

export type Contact = {
  id: string;
  user_id: string;
  display_name: string;
  phone_number: string;
  email: string | null;
  notes: string | null;
  is_admin: boolean;
  created_at: string;
  updated_at: string;
};

export type ContactListResponse = {
  contacts: Contact[];
};

export type CreateContactRequest = {
  display_name: string;
  phone_number: string;
  email?: string | null;
  notes?: string | null;
  is_admin?: boolean;
};

export type UpdateContactRequest = {
  display_name?: string;
  phone_number?: string;
  email?: string | null;
  notes?: string | null;
  is_admin?: boolean;
};

export type AgentStatus = "active" | "archived";

export type Agent = {
  id: string;
  owner_user_id: string;
  slug: string;
  display_name: string;
  description: string | null;
  status: AgentStatus;
  is_default: boolean;
  created_at: string;
  updated_at: string;
};

export type AgentListResponse = {
  agents: Agent[];
};

export type AgentUpdateRequest = {
  display_name?: string | null;
  description?: string | null;
};

export type AgentMemoryKind =
  | "preference"
  | "fact"
  | "instruction"
  | "relationship"
  | "project"
  | "task_context";

export type AgentMemoryStatus =
  | "proposed"
  | "active"
  | "archived"
  | "superseded"
  | "rejected"
  | "hidden";

export type AgentMemorySensitivity = "low" | "personal" | "sensitive";

export type AgentMemorySource =
  | "user_explicit"
  | "assistant_inferred"
  | "tool"
  | "reflection"
  | "admin";

export type AgentMemory = {
  id: string;
  user_id: string;
  kind: AgentMemoryKind;
  status: AgentMemoryStatus;
  sensitivity: AgentMemorySensitivity;
  source: AgentMemorySource;
  text: string;
  normalized_key: string;
  confidence: number;
  evidence_session_id: string | null;
  evidence_transcript_id: string | null;
  supersedes_memory_id: string | null;
  created_by_reflection_id: string | null;
  created_at: string;
  updated_at: string;
};

export type AgentMemoryListResponse = {
  memories: AgentMemory[];
};

export type CreateAgentMemoryRequest = {
  kind: AgentMemoryKind;
  text: string;
  sensitivity?: AgentMemorySensitivity;
  source?: AgentMemorySource;
  confidence?: number;
  evidence_session_id?: string | null;
  evidence_transcript_id?: string | null;
  created_by_reflection_id?: string | null;
  confirmed?: boolean;
};

export type UpdateAgentMemoryRequest = {
  text?: string | null;
  status?: AgentMemoryStatus | null;
  confirmed?: boolean;
};

export type AgentSkillStatus =
  | "active"
  | "archived"
  | "proposed"
  | "rejected";

export type AgentSkillScope = "developer" | "user";

export type AgentSkillCategory =
  | "general"
  | "voice"
  | "memory"
  | "planning"
  | "tool_use";

export type AgentSkill = {
  id: string;
  scope: AgentSkillScope;
  owner_user_id: string | null;
  name: string;
  slug: string;
  category: AgentSkillCategory;
  status: AgentSkillStatus;
  description: string;
  body_markdown: string;
  created_by_reflection_id: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type AgentSkillListResponse = {
  skills: AgentSkill[];
};

export type CreateAgentSkillRequest = {
  name: string;
  slug?: string | null;
  category: AgentSkillCategory;
  description: string;
  body_markdown: string;
  created_by_reflection_id?: string | null;
  confirmed?: boolean;
};

export type UpdateAgentSkillRequest = {
  name?: string | null;
  category?: AgentSkillCategory | null;
  description?: string | null;
  body_markdown?: string | null;
  confirmed?: boolean;
};

export type TaskStatus =
  | "proposed"
  | "active"
  | "in_progress"
  | "blocked"
  | "completed"
  | "cancelled"
  | "archived";

export type TaskSource = "user" | "agent" | "reflection" | "tool";
export type TaskPriority = "low" | "normal" | "high";

export type TaskStep = {
  id: string;
  plan_id: string;
  user_id: string;
  position: number;
  title: string;
  status: TaskStatus;
  evidence_session_id: string | null;
  created_at: string;
  updated_at: string;
};

export type TaskPlan = {
  id: string;
  user_id: string;
  session_id: string | null;
  parent_task_id: string | null;
  objective: string;
  acceptance_criteria: string[];
  source: TaskSource;
  priority: TaskPriority;
  status: TaskStatus;
  allow_parallel_steps: boolean;
  steps: TaskStep[];
  created_at: string;
  updated_at: string;
};

export type TaskPlanListResponse = {
  task_plans: TaskPlan[];
};

export type CreateTaskPlanRequest = {
  objective: string;
  steps: string[];
  acceptance_criteria?: string[];
  session_id?: string | null;
  parent_task_id?: string | null;
  source?: TaskSource;
  priority?: TaskPriority;
  status?: TaskStatus;
  allow_parallel_steps?: boolean;
};

export type UpdateTaskPlanRequest = {
  objective?: string | null;
  acceptance_criteria?: string[] | null;
  priority?: TaskPriority | null;
  status?: TaskStatus | null;
  allow_parallel_steps?: boolean | null;
};

export type UpdateTaskStepRequest = {
  status: TaskStatus;
  note?: string | null;
  evidence_session_id?: string | null;
};

export type ToolCapability =
  | "read"
  | "write"
  | "external_side_effect"
  | "sensitive_data";

export type ToolRisk = "low" | "medium" | "high";
export type ToolGrantStatus = "allowed" | "denied";
export type ToolGrantScope = "internal" | "mcp_server" | "integration";

export type ToolPolicy = {
  tool_name: string;
  capabilities: ToolCapability[];
  risk: ToolRisk;
  confirmation_required: boolean;
};

export type ToolGrant = {
  id: string;
  user_id: string;
  agent_id: string;
  tool_name: string;
  tool_source: ToolGrantScope;
  mcp_server_id: string | null;
  integration_id: string | null;
  status: ToolGrantStatus;
  risk: ToolRisk;
};

export type ToolGrantUpdate = {
  tool_name: string;
  tool_source: ToolGrantScope;
  mcp_server_id?: string | null;
  integration_id?: string | null;
  status: ToolGrantStatus;
  risk?: ToolRisk;
};

export type ToolGrantListResponse = {
  grants: ToolGrant[];
  internal_tools: ToolPolicy[];
};

export type ToolRouteMode = "automatic" | "pinned";

export type ToolRouteSelection = {
  tool_name: string;
  tool_source: ToolGrantScope;
  mcp_server_id: string | null;
  integration_id: string | null;
};

export type ToolRouteDefinition = {
  action: string;
  label: string;
  description: string;
};

export type ToolRoutePreference = {
  id: string | null;
  user_id: string;
  agent_id: string;
  action: string;
  mode: ToolRouteMode;
  pinned_tool: ToolRouteSelection | null;
};

export type ToolRouteCandidate = {
  action: string;
  provider_label: string;
  reference: ToolRouteSelection & { risk: ToolRisk };
  automatic_priority: number;
};

export type ToolRoutePreferenceUpdate = {
  action: string;
  mode: ToolRouteMode;
  pinned_tool?: ToolRouteSelection | null;
};

export type ToolRoutePreferenceListResponse = {
  definitions: ToolRouteDefinition[];
  preferences: ToolRoutePreference[];
  candidates: ToolRouteCandidate[];
};

export type McpTransportType = "streamable_http" | "sse";

export type McpAuthType =
  | "none"
  | "static_bearer"
  | "api_key_header"
  | "oauth_authorization_code"
  | "oauth_client_credentials"
  | "private_key_jwt";

export type McpServerStatus =
  | "disabled"
  | "untested"
  | "needs_auth"
  | "connected"
  | "failed";

export type McpAuthSummary = {
  authenticated: boolean;
  needs_reauth: boolean;
  expires_at: string | null;
  scopes: string[];
};

export type McpPresetCategory =
  | "mcp_gateways"
  | "travel_lifestyle"
  | "payments_commerce"
  | "productivity_collaboration"
  | "web_search_research"
  | "browser_automation"
  | "scraping_actor_platforms"
  | "developer_operations"
  | "business_systems"
  | "knowledge_documents"
  | "data_analytics"
  | "smart_home_environment";

export type McpPresetCompatibility =
  | "direct_remote"
  | "custom_remote"
  | "gateway_required";

export type McpPresetToolRisk = "low" | "medium" | "high";

export type McpPresetAuthGuide = {
  auth_type: McpAuthType;
  label: string;
  description: string;
  requires_secret: boolean;
  api_key_header_name: string | null;
  oauth_scopes: string[];
  requires_oauth_client: boolean;
  oauth_token_endpoint_auth_method: string | null;
  requires_provider_oauth: boolean;
  provider_oauth_label: string | null;
  provider_oauth_description: string | null;
};

export type McpPresetToolGuide = {
  name: string;
  label: string | null;
  description: string | null;
  risk: McpPresetToolRisk;
  recommended: boolean;
  read_only: boolean;
  disabled_by_default: boolean;
};

export type McpServicePresetVariant = {
  id: string;
  label: string;
  compatibility: McpPresetCompatibility;
  url: string | null;
  url_placeholder: string | null;
  trusted_private_host: string | null;
  transport: McpTransportType;
  auth: McpPresetAuthGuide;
  optional_auth: McpPresetAuthGuide | null;
  starter_allowed_tools: string[];
  risky_tools: string[];
  warnings: string[];
};

export type McpServicePreset = {
  id: string;
  category: McpPresetCategory;
  label: string;
  description: string;
  cost_posture: string;
  free_available: boolean;
  catalog_version: string;
  researched_at: string;
  variants: McpServicePresetVariant[];
  tool_guidance: McpPresetToolGuide[];
  setup_steps: string[];
  docs_urls: string[];
  warnings: string[];
  logo: BrandLogo;
};

export type McpPresetListResponse = {
  catalog_version: string;
  researched_at: string;
  presets: McpServicePreset[];
};

export type McpDiscoveredToolDefinition = {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  guidance: McpPresetToolGuide | null;
};

export type McpServerSafeMetadata = {
  preset_id: string | null;
  preset_variant_id: string | null;
  preset_label: string | null;
  catalog_version: string | null;
  researched_at: string | null;
  category: McpPresetCategory | null;
  docs_urls: string[];
  warnings: string[];
  tool_guidance: McpPresetToolGuide[];
  tool_definitions: McpDiscoveredToolDefinition[];
  platform_default: boolean;
  default_allowed_tools: string[];
  logo: BrandLogo | null;
};

export type McpServer = {
  id: string;
  label: string;
  url: string;
  transport: McpTransportType;
  auth_type: McpAuthType;
  enabled: boolean;
  non_admin_tools_enabled: boolean;
  trust_state: "trusted" | "untrusted";
  status: McpServerStatus;
  status_message: string | null;
  allowed_tools: string[];
  discovered_tools: string[];
  safe_metadata: McpServerSafeMetadata;
  logo: BrandLogo | null;
  auth: McpAuthSummary;
  optional_auth: McpPresetAuthGuide | null;
  optional_auth_configured: boolean;
  provider_auth: McpAuthSummary | null;
  created_at: string;
  updated_at: string;
};

export type McpServerListResponse = {
  servers: McpServer[];
};

export type BrowserAccountStatus =
  | "setup_required"
  | "setup_in_progress"
  | "ready"
  | "expired"
  | "revoked"
  | "deletion_pending"
  | "error";

export type BrowserAccount = {
  id: string;
  label: string;
  provider: "stagehand_local" | "browser_use_cloud" | "browserless";
  provider_connection_id: string | null;
  primary_origin: string;
  authentication_origins: string[];
  browser_country_code: string;
  authentication_mode: "managed_profile" | "stored_credentials";
  status: BrowserAccountStatus;
  error_category: string | null;
  agent_ids: string[];
  has_credentials: boolean;
  last_verified_at: string | null;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
};

export type BrowserAccountListResponse = {
  accounts: BrowserAccount[];
};

export type CreateBrowserAccountRequest = {
  label: string;
  primary_origin: string;
  authentication_origins: string[];
  browser_country_code: string;
  provider: "stagehand_local" | "browser_use_cloud" | "browserless";
  provider_connection_id?: string;
  agent_ids: string[];
  username?: string;
  password?: string;
};

export type UpdateBrowserAccountRequest = {
  label: string;
  primary_origin: string;
  authentication_origins: string[];
  browser_country_code: string;
};

export type BrowserAccountSetup = {
  account: BrowserAccount;
  live_url: string;
  expires_at: string;
};

export type CreateMcpServerRequest = {
  label: string;
  url: string;
  transport: McpTransportType;
  auth_type: McpAuthType;
  enabled: boolean;
  non_admin_tools_enabled?: boolean;
  allowed_tools: string[];
  api_key_header_name?: string | null;
};

export type CreateMcpServerFromPresetRequest = {
  label?: string | null;
  variant_id?: string | null;
  enabled: boolean;
  non_admin_tools_enabled?: boolean;
  allowed_tools?: string[] | null;
  url?: string | null;
};

export type UpdateMcpServerRequest = {
  label?: string;
  url?: string;
  transport?: McpTransportType;
  enabled?: boolean;
  non_admin_tools_enabled?: boolean;
  allowed_tools?: string[];
  api_key_header_name?: string | null;
};

export type SaveMcpStaticAuthRequest = {
  secret: string;
  api_key_header_name?: string | null;
};

export type StartMcpOAuthRequest = {
  redirect_uri: string;
  scopes: string[];
  client_name?: string;
  client_id?: string | null;
  client_secret?: string | null;
  authorization_endpoint?: string | null;
  token_endpoint?: string | null;
  registration_endpoint?: string | null;
  token_endpoint_auth_method?: string | null;
};

export type StartMcpOAuthResponse = {
  authorization_url: string;
  expires_at: string;
};

export type ChatSummary = {
  id: string;
  agent_id: string;
  title: string | null;
  provider: TextProvider;
  model: string;
  status: string;
  started_at: string;
  updated_at: string;
  supports_image_input: boolean;
};

export type ChatListResponse = {
  chats: ChatSummary[];
  image_input_enabled: boolean;
  image_generation_enabled: boolean;
  image_editing_enabled: boolean;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system" | "tool" | "unknown";
  text: string;
  source: string;
  interrupted?: boolean;
  is_final?: boolean;
  client_turn_id?: string | null;
  created_at: string;
  attachments: ChatAttachmentSummary[];
  place_cards: ChatPlaceCard[];
};

export type ChatPlaceCard = {
  provider: "google_maps";
  place_id: string;
  title: string;
  place_url: string;
  attribution_url: string;
  directions_url: string | null;
  latitude: number | null;
  longitude: number | null;
  position: number;
};

export type ChatAttachmentSummary = {
  id: string;
  mime_type: "image/jpeg" | "image/png";
  byte_size: number;
  width: number;
  height: number;
  position: number | null;
};

export type ChatRuntimeEvent = {
  id: string;
  kind: ChatStreamEventKind;
  payload_json: Record<string, unknown>;
  created_at: string;
};

export type ChatToolEvent = {
  id: string;
  call_id: string;
  name: string;
  arguments_json: Record<string, unknown>;
  output_text: string | null;
  is_error: boolean;
  created_at: string;
};

export type ChatUsageEvent = {
  id: string;
  usage_type: string;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  raw_usage_json: Record<string, unknown>;
  created_at: string;
};

export type ChatDelegatedRun = {
  id: string;
  status: AgentRunStatus;
  task_prompt: string;
  provider: string | null;
  model: string | null;
  chat_id: string | null;
  output_text: string | null;
  output_created_at: string | null;
  runtime_events: ChatRuntimeEvent[];
  tool_events: ChatToolEvent[];
  created_at: string;
  completed_at: string | null;
};

export type ChatDetail = {
  chat: ChatSummary;
  messages: ChatMessage[];
  runtime_events: ChatRuntimeEvent[];
  tool_events: ChatToolEvent[];
  usage_events: ChatUsageEvent[];
  delegated_runs: ChatDelegatedRun[];
};

export type ChatDelegatedRunListResponse = {
  delegated_runs: ChatDelegatedRun[];
};

export type CreateChatRequest = {
  agent_id?: string | null;
  title?: string | null;
  provider?: TextProvider | null;
  model?: string | null;
};

export type UpdateChatRequest = {
  title?: string | null;
  provider?: TextProvider | null;
  model?: string | null;
};

export type ChatStreamEventKind =
  | "run_started"
  | "message_delta"
  | "message_completed"
  | "tool_call_started"
  | "tool_call_delta"
  | "tool_call_completed"
  | "tool_call_failed"
  | "usage"
  | "run_completed"
  | "run_cancelled"
  | "run_failed"
  | "chat_deleted";

export type ChatStreamEvent = {
  id?: string;
  kind: ChatStreamEventKind;
  session_id?: string;
  run_id?: string | null;
  payload: Record<string, unknown>;
  created_at?: string;
};

export type StreamChatMessageOptions = {
  attachmentIds?: readonly string[];
  clientTurnId?: string;
  location?: import("./location").AgentLocationSnapshot | null;
  signal?: AbortSignal;
  onEvent: (event: ChatStreamEvent) => void;
};

export type AgentRunKind =
  | "text_chat_turn"
  | "communication_reply"
  | "scheduled_agent_turn"
  | "subagent"
  | "manual_agent_task"
  | "webhook_agent_task"
  | "codex_cloud_task";

export type AgentRunStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelling"
  | "cancelled"
  | "lost";

export type AgentRunEventKind =
  | "run_enqueued"
  | "run_started"
  | "run_heartbeat"
  | "run_retry_scheduled"
  | "run_completed"
  | "run_failed"
  | "run_cancel_requested"
  | "run_cancelled"
  | "run_lost"
  | "run_redacted"
  | "message_delta"
  | "message_completed"
  | "tool_call_started"
  | "tool_call_completed"
  | "tool_call_failed"
  | "usage"
  | "subagent_started"
  | "subagent_completed"
  | "schedule_enqueued"
  | "progress";

export type AgentRunSummary = {
  id: string;
  user_id: string;
  agent_id: string;
  kind: AgentRunKind;
  status: AgentRunStatus;
  queue: string;
  priority: number;
  session_id: string | null;
  chat_id: string | null;
  parent_run_id: string | null;
  root_run_id: string | null;
  schedule_id: string | null;
  webhook_id: string | null;
  task_plan_id: string | null;
  attempt: number;
  max_attempts: number;
  created_at: string;
  updated_at: string;
  next_attempt_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  heartbeat_at: string | null;
  lease_owner: string | null;
  lease_expires_at: string | null;
  cancel_requested_at: string | null;
  cancel_reason: string | null;
  error_type: string | null;
  error_message: string | null;
  child_run_count: number;
};

export type AgentRunDetail = AgentRunSummary & {
  payload_json: Record<string, unknown>;
  result_json: Record<string, unknown>;
};

export type AgentRunEvent = {
  id: string;
  run_id: string;
  user_id: string;
  agent_id: string;
  kind: AgentRunEventKind;
  payload_json: Record<string, unknown>;
  delivery_state: "pending" | "streamed" | "acknowledged";
  created_at: string;
};

export type RunListResponse = {
  runs: AgentRunSummary[];
};

export type RunEventListResponse = {
  events: AgentRunEvent[];
};

export type CodexAccountStatus =
  | "connected"
  | "reauthentication_required";

export type CodexAccount = {
  connected: boolean;
  id: string | null;
  status: CodexAccountStatus | null;
  email: string | null;
  plan_type: string | null;
  environment_id: string | null;
  environment_label: string | null;
  authenticated_at: string | null;
  updated_at: string | null;
};

export type CodexAuthSessionStatus =
  | "queued"
  | "waiting_for_user"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "expired";

export type CodexAuthSession = {
  id: string;
  status: CodexAuthSessionStatus;
  verification_url: string | null;
  user_code: string | null;
  error_message: string | null;
  expires_at: string;
  created_at: string;
  updated_at: string;
};

export type CodexCloudTask = {
  id: string;
  run: AgentRunDetail;
  environment_id: string;
  branch: string | null;
  attempts: number;
  submission_state:
    | "pending"
    | "submitting"
    | "submitted"
    | "submission_uncertain";
  remote_task_id: string | null;
  remote_task_url: string | null;
  remote_status:
    | "pending"
    | "running"
    | "ready"
    | "failed"
    | "cancelled"
    | "unknown";
  summary: string | null;
  created_at: string;
  updated_at: string;
};

export type CreateCodexCloudTaskRequest = {
  agent_id: string;
  chat_id?: string | null;
  prompt: string;
  branch?: string | null;
  attempts?: number;
  idempotency_key?: string | null;
};

export type ScheduleKind = "one_shot" | "interval" | "cron";
export type ScheduleStatus = "active" | "paused" | "completed" | "archived";
export type ScheduleCatchUpPolicy = "skip_missed" | "run_once";

export type AgentSchedule = {
  id: string;
  user_id: string;
  agent_id: string;
  chat_id: string | null;
  browser_account_id: string | null;
  name: string;
  kind: ScheduleKind;
  status: ScheduleStatus;
  timezone: string;
  payload_json: Record<string, unknown>;
  run_at: string | null;
  interval_seconds: number | null;
  cron_expression: string | null;
  next_run_at: string | null;
  last_run_at: string | null;
  last_run_id: string | null;
  last_run_status: AgentRunStatus | null;
  max_concurrent_runs: number;
  catch_up_policy: ScheduleCatchUpPolicy;
  misfire_grace_seconds: number;
  retry_policy_json: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};

export type ScheduleListResponse = {
  schedules: AgentSchedule[];
};

export type CreateScheduleRequest = {
  agent_id: string;
  chat_id?: string | null;
  browser_account_id?: string | null;
  name: string;
  kind: ScheduleKind;
  timezone?: string;
  payload_json?: Record<string, unknown>;
  run_at?: string | null;
  interval_seconds?: number | null;
  max_concurrent_runs?: number;
  catch_up_policy?: ScheduleCatchUpPolicy;
  misfire_grace_seconds?: number;
  retry_policy_json?: Record<string, unknown>;
  enabled?: boolean;
};

export type UpdateScheduleRequest = Partial<
  Pick<
    CreateScheduleRequest,
    | "name"
    | "chat_id"
    | "timezone"
    | "payload_json"
    | "run_at"
    | "interval_seconds"
    | "max_concurrent_runs"
    | "catch_up_policy"
    | "misfire_grace_seconds"
    | "retry_policy_json"
  >
>;

export type AgentWebhook = {
  id: string;
  user_id: string;
  agent_id: string;
  name: string;
  instruction: string;
  enabled: boolean;
  trigger_path: string;
  trigger_url: string | null;
  created_at: string;
  updated_at: string;
};

export type AgentWebhookCredential = AgentWebhook & {
  token: string;
};

export type AgentWebhookListResponse = {
  webhooks: AgentWebhook[];
};

export type CreateAgentWebhookRequest = {
  agent_id: string;
  name: string;
  instruction: string;
};

export type UpdateAgentWebhookRequest = Partial<
  Pick<AgentWebhook, "name" | "instruction" | "enabled">
>;

export type StreamRunEventsOptions = {
  runId?: string;
  sinceEventId?: string;
  signal?: AbortSignal;
  onEvent: (event: AgentRunEvent) => void;
};

export type IntegrationProviderType = "twilio" | "telnyx" | "whatsapp";
export type IntegrationStatus =
  | "disabled"
  | "needs_auth"
  | "untested"
  | "connected"
  | "failed"
  | "revoked";
export type IntegrationAuthType = "api_key" | "linked_device";
export type IntegrationCapability =
  | "webhook_events"
  | "inbound_voice"
  | "outbound_voice"
  | "sms_read"
  | "sms_send"
  | "whatsapp_read"
  | "whatsapp_send"
  | "media_stream"
  | "tool_actions";
export type IntegrationResourceStatus =
  | "connected"
  | "disabled"
  | "failed"
  | "failed_disconnect_cleanup";
export type IntegrationResourceType = "phone_number";

export type Integration = {
  id: string;
  provider: IntegrationProviderType;
  label: string;
  status: IntegrationStatus;
  enabled: boolean;
  auth_type: IntegrationAuthType;
  setup_state: string;
  default_agent_id: string | null;
  safe_metadata: Record<string, unknown>;
  logo: BrandLogo;
  last_checked_at: string | null;
  status_message: string | null;
  created_at: string;
  updated_at: string;
};

export type IntegrationListResponse = {
  integrations: Integration[];
};

export type IntegrationCredentialSummary = {
  id: string;
  secret_kind:
    | "twilio_api_key_secret"
    | "twilio_auth_token"
    | "provider_api_secret"
    | "telnyx_stream_auth_token"
    | "whatsapp_session_data_key"; // pragma: allowlist secret
  status: "active" | "failed" | "revoked";
  public_identifier: string | null;
  last_four: string | null;
  created_at: string;
  updated_at: string;
  revoked_at: string | null;
};

export type IntegrationResource = {
  id: string;
  integration_id: string;
  provider: IntegrationProviderType;
  resource_type: IntegrationResourceType;
  external_id: string;
  display_name: string;
  status: IntegrationResourceStatus;
  capabilities: IntegrationCapability[];
  safe_metadata: Record<string, unknown>;
  retained_metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type IntegrationResourceListResponse = {
  resources: IntegrationResource[];
};

export type TwilioPhoneNumberCandidate = {
  sid: string;
  phone_number: string;
  friendly_name: string | null;
  capabilities: IntegrationCapability[];
  voice_url: string | null;
  voice_method: string | null;
  sms_url: string | null;
  sms_method: string | null;
  status_callback: string | null;
  status_callback_method: string | null;
};

export type TwilioCredentialSetupRequest = {
  label: string;
  account_sid: string;
  api_key_sid: string;
  api_key_secret: string;
  auth_token: string;
};

export type TwilioCredentialSetupResponse = {
  integration: Integration;
  credentials: IntegrationCredentialSummary[];
  phone_numbers: TwilioPhoneNumberCandidate[];
};

export type TelnyxPhoneSetupRequest = {
  label: string;
  phone_number: string;
  api_key: string;
  webhook_public_key: string;
  confirm_number_assignment: boolean;
  transcript_storage_enabled: boolean;
};

export type TelnyxPhoneSetupResponse = {
  integration: Integration;
  credentials: IntegrationCredentialSummary[];
  resources: IntegrationResource[];
};

export type IntegrationReadinessStatus = "ready" | "not_ready" | "unknown";

export type IntegrationReadinessCheck = {
  name: string;
  status: IntegrationReadinessStatus;
  code: string;
  message: string;
};

export type TelnyxProviderReadiness = {
  phone_number_id: string;
  phone_number: string;
  phone_number_status: string | null;
  capabilities: IntegrationCapability[];
  connection_id: string | null;
  messaging_profile_id: string | null;
  outbound_voice_profile_id: string | null;
  connection_assignment_matches: boolean;
  messaging_assignment_matches: boolean;
  outbound_voice_profile_assignment_matches: boolean;
  voice_application_found: boolean;
  voice_application_active: boolean | null;
  voice_webhook_matches: boolean;
  voice_webhook_api_version: string | null;
  messaging_profile_found: boolean;
  messaging_profile_enabled: boolean | null;
  messaging_webhook_matches: boolean;
  messaging_webhook_api_version: string | null;
  outbound_voice_profile_found: boolean;
  outbound_voice_profile_enabled: boolean | null;
  messaging_destinations: string[];
  voice_destinations: string[];
};

export type TelnyxReadinessResponse = {
  integration_id: string;
  resource_id: string;
  checked_at: string;
  repaired: boolean;
  provider: TelnyxProviderReadiness;
  checks: IntegrationReadinessCheck[];
};

export type IntegrationMessageStatus =
  | "received"
  | "queued"
  | "sent"
  | "delivered"
  | "delivery_unconfirmed"
  | "failed"
  | "unknown";

export type IntegrationMessageActivity = {
  id: string;
  integration_id: string;
  resource_id: string;
  provider: IntegrationProviderType;
  direction: "inbound" | "outbound" | "unknown";
  remote_party: string | null;
  status: IntegrationMessageStatus;
  external_message_id: string;
  agent_run_id: string | null;
  agent_run_status: string | null;
  conversation_id: string | null;
  reply_id: string | null;
  reply_status:
    | "pending"
    | "generated"
    | "dispatching"
    | "provider_accepted"
    | "delivered"
    | "failed"
    | "suppressed"
    | "ambiguous"
    | null;
  response_profile: string | null;
  sms_encoding: string | null;
  estimated_segments: number | null;
  condensation_attempted: boolean;
  overflow_fallback_used: boolean;
  failure_code: string | null;
  failure_title: string | null;
  created_at: string;
  updated_at: string;
};

export type IntegrationMessageActivityListResponse = {
  messages: IntegrationMessageActivity[];
  offset: number;
  limit: number;
  next_offset: number | null;
};

export type IntegrationCallActivity = {
  id: string;
  integration_id: string;
  resource_id: string;
  provider: IntegrationProviderType;
  direction: "inbound" | "outbound" | "unknown";
  remote_party: string | null;
  status: "initiating" | "ringing" | "in_progress" | "completed" | "failed" | "rejected";
  external_call_id: string;
  contact_id: string | null;
  contact_display_name: string | null;
  agent_id: string | null;
  failure_code: string | null;
  started_at: string | null;
  answered_at: string | null;
  ended_at: string | null;
  created_at: string;
  updated_at: string;
};

export type IntegrationCallActivityListResponse = {
  calls: IntegrationCallActivity[];
  offset: number;
  limit: number;
  next_offset: number | null;
};

export type TwilioResourceSelection = {
  phone_number_sid: string;
  capabilities: IntegrationCapability[];
  transcript_storage_enabled: boolean;
};

export type TwilioResourceConnectRequest = {
  resources: TwilioResourceSelection[];
  overwrite_existing_webhooks: boolean;
};

export type TwilioResourceConnectResponse = {
  integration: Integration;
  resources: IntegrationResource[];
};

export type WhatsAppLinkMethod = "qr" | "pairing_code";

export type WhatsAppSessionState =
  | "awaiting_link"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "waiting_for_capacity"
  | "needs_relink"
  | "disconnecting"
  | "failed"
  | "disabled";

export type WhatsAppSetupOption = {
  source_resource_id: string;
  provider: Exclude<IntegrationProviderType, "whatsapp">;
  phone_number: string;
  line_type?: string | null;
};

export type WhatsAppSetupOptionsResponse = {
  options: WhatsAppSetupOption[];
};

export type WhatsAppStatus = {
  integration_id: string;
  source_resource_id: string;
  phone_number: string;
  state: WhatsAppSessionState;
  status_message: string | null;
  validated_at: string | null;
  connected_at: string | null;
  challenge_expires_at: string | null;
};

export type WhatsAppChallenge = WhatsAppStatus & {
  method: WhatsAppLinkMethod;
  value: string;
};

export type WhatsAppLinkRequest = {
  source_resource_id: string;
  method: WhatsAppLinkMethod;
};

export type WhatsAppMobileRegistrationState =
  | "armed"
  | "armed_voice"
  | "code_received"
  | "expired";

export type WhatsAppMobileRegistrationChannel = "sms" | "voice";

export type WhatsAppMobileRegistrationCandidate = {
  candidate_id: string;
  channel: WhatsAppMobileRegistrationChannel;
  code: string;
  sender: string;
  received_at: string;
};

export type WhatsAppMobileRegistrationStatus = {
  registration_id: string;
  source_resource_id: string;
  phone_number: string;
  state: WhatsAppMobileRegistrationState;
  capture_expires_at: string;
  candidates: WhatsAppMobileRegistrationCandidate[];
};

export type WhatsAppMobileRegistrationRequest = {
  source_resource_id: string;
  channel: WhatsAppMobileRegistrationChannel;
};

export type WhatsAppMobileRegistrationCandidateRequest = {
  candidate_id: string;
};

export type WhatsAppMobileRegistrationCompleteRequest =
  WhatsAppMobileRegistrationCandidateRequest & {
    method: WhatsAppLinkMethod;
  };

export type WhatsAppChallengeRequest = {
  method: WhatsAppLinkMethod;
};

export class ApiRequestError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

export const defaultApiBaseUrl = import.meta.env.PROD
  ? window.location.origin
  : (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:7100");
let activeApiBaseUrl = defaultApiBaseUrl.replace(/\/$/, "");

export function setApiBaseUrl(baseUrl: string): void {
  activeApiBaseUrl = baseUrl.replace(/\/$/, "");
}

export async function loadVoiceSettings(): Promise<VoiceSettings> {
  return requestJson<VoiceSettings>("/api/voice/settings", {
    method: "GET",
  });
}

export async function loadVoiceProviderSettings(): Promise<VoiceProviderSettings> {
  return requestJson<VoiceProviderSettings>("/api/voice/provider-settings", {
    method: "GET",
  });
}

export async function saveVoiceSettings(
  request: SaveVoicePreferencesRequest,
): Promise<VoiceSettings> {
  return requestJson<VoiceSettings>("/api/voice/settings", {
    body: JSON.stringify(request),
    method: "PUT",
  });
}

export async function saveOpenAIKey(
  request: SaveOpenAIKeyRequest,
): Promise<VoiceSettings> {
  return requestJson<VoiceSettings>("/api/voice/settings/credential", {
    body: JSON.stringify(request),
    method: "PUT",
  });
}

export async function saveVoiceProviderKey(
  request: SaveVoiceProviderKeyRequest,
): Promise<VoiceProviderSettings> {
  return requestJson<VoiceProviderSettings>(
    "/api/voice/settings/provider-credential",
    {
      body: JSON.stringify(request),
      method: "PUT",
    },
  );
}

export async function activateVoiceProvider(
  request: ActivateVoiceProviderRequest,
): Promise<VoiceProviderSettings> {
  return requestJson<VoiceProviderSettings>("/api/voice/settings/active-provider", {
    body: JSON.stringify(request),
    method: "PUT",
  });
}

export async function loadTextProviderSettings(): Promise<TextProviderSettings> {
  return requestJson<TextProviderSettings>("/api/text/provider-settings", {
    method: "GET",
  });
}

export async function saveTextProviderKey(
  request: SaveTextProviderKeyRequest,
): Promise<TextProviderSettings> {
  return requestJson<TextProviderSettings>(
    "/api/text/settings/provider-credential",
    {
      body: JSON.stringify(request),
      method: "PUT",
    },
  );
}

export async function saveOpenAICompatibleProvider(
  request: SaveOpenAICompatibleProviderRequest,
): Promise<TextProviderSettings> {
  return requestJson<TextProviderSettings>(
    "/api/text/settings/openai-compatible",
    {
      body: JSON.stringify(request),
      method: "PUT",
    },
  );
}

export async function activateTextProvider(
  request: ActivateTextProviderRequest,
): Promise<TextProviderSettings> {
  return requestJson<TextProviderSettings>("/api/text/settings/active-provider", {
    body: JSON.stringify(request),
    method: "PUT",
  });
}

export async function saveTextSettings(
  request: SaveTextPreferencesRequest,
): Promise<TextSettings> {
  return requestJson<TextSettings>("/api/text/settings", {
    body: JSON.stringify(request),
    method: "PUT",
  });
}

export async function saveImageGenerationSettings(
  request: SaveImageGenerationPreferencesRequest,
): Promise<TextSettings> {
  return requestJson<TextSettings>("/api/text/settings/image-generation", {
    body: JSON.stringify(request),
    method: "PUT",
  });
}

export async function loadLiveKitSettings(): Promise<LiveKitSettings> {
  return requestJson<LiveKitSettings>("/api/livekit/settings", {
    method: "GET",
  });
}

export async function saveLiveKitSettings(
  request: SaveLiveKitSettingsRequest,
): Promise<LiveKitSettings> {
  return requestJson<LiveKitSettings>("/api/livekit/settings", {
    body: JSON.stringify(request),
    method: "PUT",
  });
}

export async function loadContacts(): Promise<Contact[]> {
  const response = await requestJson<ContactListResponse>("/api/contacts", {
    method: "GET",
  });
  return response.contacts;
}

export async function createContact(
  request: CreateContactRequest,
): Promise<Contact> {
  return requestJson<Contact>("/api/contacts", {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function updateContact(
  contactId: string,
  request: UpdateContactRequest,
): Promise<Contact> {
  return requestJson<Contact>(`/api/contacts/${contactId}`, {
    body: JSON.stringify(request),
    method: "PATCH",
  });
}

export async function deleteContact(contactId: string): Promise<void> {
  await requestEmpty(`/api/contacts/${contactId}`, { method: "DELETE" });
}

export async function loadAgents(): Promise<Agent[]> {
  const response = await requestJson<AgentListResponse>("/api/agents", {
    method: "GET",
  });
  return response.agents;
}

export async function loadDefaultAgent(): Promise<Agent> {
  return requestJson<Agent>("/api/agents/default", {
    method: "GET",
  });
}

export async function updateAgent(
  agentId: string,
  request: AgentUpdateRequest,
): Promise<Agent> {
  return requestJson<Agent>(`/api/agents/${agentId}`, {
    body: JSON.stringify(request),
    method: "PATCH",
  });
}

export async function loadToolGrants(
  agentId: string,
): Promise<ToolGrantListResponse> {
  return requestJson<ToolGrantListResponse>(
    `/api/agents/${agentId}/tool-grants`,
    {
      method: "GET",
    },
  );
}

export async function loadToolRoutes(
  agentId: string,
): Promise<ToolRoutePreferenceListResponse> {
  return requestJson<ToolRoutePreferenceListResponse>(
    `/api/agents/${agentId}/tool-routes`,
    { method: "GET" },
  );
}

export async function loadIntegrations(): Promise<Integration[]> {
  const response = await requestJson<IntegrationListResponse>("/api/integrations", {
    method: "GET",
  });
  return response.integrations;
}

export async function loadIntegrationResources(
  integrationId: string,
): Promise<IntegrationResource[]> {
  const response = await requestJson<IntegrationResourceListResponse>(
    `/api/integrations/${integrationId}/resources`,
    { method: "GET" },
  );
  return response.resources;
}

export async function setupTwilioIntegration(
  request: TwilioCredentialSetupRequest,
): Promise<TwilioCredentialSetupResponse> {
  return requestJson<TwilioCredentialSetupResponse>(
    "/api/integrations/twilio/setup",
    {
      body: JSON.stringify(request),
      method: "POST",
    },
  );
}

export async function setupTelnyxIntegration(
  request: TelnyxPhoneSetupRequest,
): Promise<TelnyxPhoneSetupResponse> {
  return requestJson<TelnyxPhoneSetupResponse>("/api/integrations/telnyx/setup", {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function verifyTelnyxIntegration(
  integrationId: string,
): Promise<TelnyxReadinessResponse> {
  return requestJson<TelnyxReadinessResponse>(
    `/api/integrations/${integrationId}/telnyx/readiness`,
    { method: "GET" },
  );
}

export async function repairTelnyxIntegration(
  integrationId: string,
): Promise<TelnyxReadinessResponse> {
  return requestJson<TelnyxReadinessResponse>(
    `/api/integrations/${integrationId}/telnyx/repair`,
    {
      body: JSON.stringify({ confirm_provider_changes: true }),
      method: "POST",
    },
  );
}

export async function loadIntegrationMessageActivity(
  integrationId: string,
): Promise<IntegrationMessageActivityListResponse> {
  return requestJson<IntegrationMessageActivityListResponse>(
    `/api/integrations/${integrationId}/messages?limit=10&offset=0`,
    { method: "GET" },
  );
}

export async function loadIntegrationCallActivity(
  integrationId: string,
): Promise<IntegrationCallActivityListResponse> {
  return requestJson<IntegrationCallActivityListResponse>(
    `/api/integrations/${integrationId}/calls?limit=10&offset=0`,
    { method: "GET" },
  );
}

export async function connectTwilioResources(
  integrationId: string,
  request: TwilioResourceConnectRequest,
): Promise<TwilioResourceConnectResponse> {
  return requestJson<TwilioResourceConnectResponse>(
    `/api/integrations/${integrationId}/twilio/resources`,
    {
      body: JSON.stringify(request),
      method: "POST",
    },
  );
}

export async function disconnectIntegration(integrationId: string): Promise<void> {
  await requestEmpty(`/api/integrations/${integrationId}`, { method: "DELETE" });
}

export async function loadWhatsAppOptions(
  signal?: AbortSignal,
): Promise<WhatsAppSetupOption[]> {
  const response = await requestJson<WhatsAppSetupOptionsResponse>(
    "/api/integrations/whatsapp/options",
    {
      method: "GET",
      signal,
    },
  );
  return response.options;
}

export async function linkWhatsApp(
  request: WhatsAppLinkRequest,
): Promise<WhatsAppStatus> {
  return requestJson<WhatsAppStatus>("/api/integrations/whatsapp/link", {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function startWhatsAppMobileRegistration(
  request: WhatsAppMobileRegistrationRequest,
): Promise<WhatsAppMobileRegistrationStatus> {
  return requestJson<WhatsAppMobileRegistrationStatus>(
    "/api/integrations/whatsapp/mobile-registration",
    {
      body: JSON.stringify(request),
      cache: "no-store",
      method: "POST",
    },
  );
}

export async function loadWhatsAppMobileRegistration(
  registrationId: string,
  signal?: AbortSignal,
): Promise<WhatsAppMobileRegistrationStatus> {
  return requestJson<WhatsAppMobileRegistrationStatus>(
    `/api/integrations/whatsapp/mobile-registration/${registrationId}`,
    {
      cache: "no-store",
      method: "GET",
      signal,
    },
  );
}

export async function rejectWhatsAppMobileRegistrationCandidate(
  registrationId: string,
  request: WhatsAppMobileRegistrationCandidateRequest,
): Promise<WhatsAppMobileRegistrationStatus> {
  return requestJson<WhatsAppMobileRegistrationStatus>(
    `/api/integrations/whatsapp/mobile-registration/${registrationId}/reject`,
    {
      body: JSON.stringify(request),
      cache: "no-store",
      method: "POST",
    },
  );
}

export async function completeWhatsAppMobileRegistration(
  registrationId: string,
  request: WhatsAppMobileRegistrationCompleteRequest,
): Promise<WhatsAppStatus> {
  return requestJson<WhatsAppStatus>(
    `/api/integrations/whatsapp/mobile-registration/${registrationId}/complete`,
    {
      body: JSON.stringify(request),
      method: "POST",
    },
  );
}

export async function cancelWhatsAppMobileRegistration(
  registrationId: string,
): Promise<void> {
  await requestEmpty(
    `/api/integrations/whatsapp/mobile-registration/${registrationId}`,
    {
      method: "DELETE",
    },
  );
}

export async function loadWhatsAppStatus(
  integrationId: string,
  signal?: AbortSignal,
): Promise<WhatsAppStatus> {
  return requestJson<WhatsAppStatus>(
    `/api/integrations/whatsapp/${integrationId}/status`,
    {
      method: "GET",
      signal,
    },
  );
}

export async function loadWhatsAppChallenge(
  integrationId: string,
  signal?: AbortSignal,
): Promise<WhatsAppChallenge> {
  return requestJson<WhatsAppChallenge>(
    `/api/integrations/whatsapp/${integrationId}/challenge`,
    {
      cache: "no-store",
      method: "GET",
      signal,
    },
  );
}

export async function regenerateWhatsAppChallenge(
  integrationId: string,
  request: WhatsAppChallengeRequest,
): Promise<WhatsAppChallenge> {
  return requestJson<WhatsAppChallenge>(
    `/api/integrations/whatsapp/${integrationId}/challenge`,
    {
      body: JSON.stringify(request),
      method: "POST",
    },
  );
}

export async function relinkWhatsApp(
  integrationId: string,
  request: WhatsAppChallengeRequest,
): Promise<WhatsAppStatus> {
  return requestJson<WhatsAppStatus>(
    `/api/integrations/whatsapp/${integrationId}/relink`,
    {
      body: JSON.stringify(request),
      method: "POST",
    },
  );
}

export async function saveToolGrants(
  agentId: string,
  grants: ToolGrantUpdate[],
): Promise<ToolGrantListResponse> {
  return requestJson<ToolGrantListResponse>(
    `/api/agents/${agentId}/tool-grants`,
    {
      body: JSON.stringify({ grants }),
      method: "PUT",
    },
  );
}

export async function saveToolRoutes(
  agentId: string,
  preferences: ToolRoutePreferenceUpdate[],
): Promise<ToolRoutePreferenceListResponse> {
  return requestJson<ToolRoutePreferenceListResponse>(
    `/api/agents/${agentId}/tool-routes`,
    {
      body: JSON.stringify({ preferences }),
      method: "PUT",
    },
  );
}

export async function loadAgentMemories(
  statuses?: readonly AgentMemoryStatus[],
  limit?: number,
): Promise<AgentMemory[]> {
  const response = await requestJson<AgentMemoryListResponse>(
    agentStatusPath("/api/agents/memories", statuses, limit),
    { method: "GET" },
  );
  return response.memories;
}

export async function createAgentMemory(
  request: CreateAgentMemoryRequest,
): Promise<AgentMemory> {
  return requestJson<AgentMemory>("/api/agents/memories", {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function updateAgentMemory(
  memoryId: string,
  request: UpdateAgentMemoryRequest,
): Promise<AgentMemory> {
  return requestJson<AgentMemory>(`/api/agents/memories/${memoryId}`, {
    body: JSON.stringify(request),
    method: "PATCH",
  });
}

export async function archiveAgentMemory(memoryId: string): Promise<AgentMemory> {
  return requestJson<AgentMemory>(`/api/agents/memories/${memoryId}`, {
    method: "DELETE",
  });
}

export async function deleteAgentMemory(memoryId: string): Promise<void> {
  return requestEmpty(`/api/agents/memories/${memoryId}/permanent`, {
    method: "DELETE",
  });
}

export async function loadAgentSkills(
  statuses?: readonly AgentSkillStatus[],
): Promise<AgentSkill[]> {
  const response = await requestJson<AgentSkillListResponse>(
    agentStatusPath("/api/agents/skills", statuses),
    { method: "GET" },
  );
  return response.skills;
}

export async function createAgentSkill(
  request: CreateAgentSkillRequest,
): Promise<AgentSkill> {
  return requestJson<AgentSkill>("/api/agents/skills", {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function updateAgentSkill(
  slug: string,
  request: UpdateAgentSkillRequest,
): Promise<AgentSkill> {
  return requestJson<AgentSkill>(`/api/agents/skills/${slug}`, {
    body: JSON.stringify(request),
    method: "PATCH",
  });
}

export async function archiveAgentSkill(slug: string): Promise<AgentSkill> {
  return requestJson<AgentSkill>(`/api/agents/skills/${slug}`, {
    method: "DELETE",
  });
}

export async function deleteAgentSkill(slug: string): Promise<void> {
  return requestEmpty(`/api/agents/skills/${slug}/permanent`, {
    method: "DELETE",
  });
}

export async function loadTaskPlans(
  statuses?: readonly TaskStatus[],
  limit?: number,
): Promise<TaskPlan[]> {
  const response = await requestJson<TaskPlanListResponse>(
    agentStatusPath("/api/agents/task-plans", statuses, limit),
    { method: "GET" },
  );
  return response.task_plans;
}

export async function createTaskPlan(
  request: CreateTaskPlanRequest,
): Promise<TaskPlan> {
  return requestJson<TaskPlan>("/api/agents/task-plans", {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function updateTaskPlan(
  planId: string,
  request: UpdateTaskPlanRequest,
): Promise<TaskPlan> {
  return requestJson<TaskPlan>(`/api/agents/task-plans/${planId}`, {
    body: JSON.stringify(request),
    method: "PATCH",
  });
}

export async function archiveTaskPlan(planId: string): Promise<TaskPlan> {
  return requestJson<TaskPlan>(`/api/agents/task-plans/${planId}`, {
    method: "DELETE",
  });
}

export async function deleteTaskPlan(planId: string): Promise<void> {
  return requestEmpty(`/api/agents/task-plans/${planId}/permanent`, {
    method: "DELETE",
  });
}

export async function updateTaskStep(
  stepId: string,
  request: UpdateTaskStepRequest,
): Promise<TaskPlan> {
  return requestJson<TaskPlan>(`/api/agents/task-steps/${stepId}`, {
    body: JSON.stringify(request),
    method: "PATCH",
  });
}

export async function loadMcpServers(): Promise<McpServer[]> {
  const response = await requestJson<McpServerListResponse>("/api/mcp/servers", {
    method: "GET",
  });
  return response.servers;
}

export async function loadBrowserAccounts(): Promise<BrowserAccount[]> {
  const response = await requestJson<BrowserAccountListResponse>(
    "/api/browser-accounts",
    { method: "GET" },
  );
  return response.accounts;
}

export async function createBrowserAccount(
  request: CreateBrowserAccountRequest,
): Promise<BrowserAccount> {
  return requestJson<BrowserAccount>("/api/browser-accounts", {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function updateBrowserAccount(
  accountId: string,
  request: UpdateBrowserAccountRequest,
): Promise<BrowserAccount> {
  return requestJson<BrowserAccount>(`/api/browser-accounts/${accountId}`, {
    body: JSON.stringify(request),
    method: "PUT",
  });
}

export async function setBrowserAccountAgents(
  accountId: string,
  agentIds: readonly string[],
): Promise<BrowserAccount> {
  return requestJson<BrowserAccount>(
    `/api/browser-accounts/${accountId}/agents`,
    {
      body: JSON.stringify({ agent_ids: agentIds }),
      method: "PUT",
    },
  );
}

export async function updateBrowserAccountCredentials(
  accountId: string,
  username: string,
  password: string,
): Promise<BrowserAccount> {
  return requestJson<BrowserAccount>(
    `/api/browser-accounts/${accountId}/credentials`,
    {
      body: JSON.stringify({ username, password }),
      method: "PUT",
    },
  );
}

export async function startBrowserAccountSetup(
  accountId: string,
): Promise<BrowserAccountSetup> {
  return requestJson<BrowserAccountSetup>(
    `/api/browser-accounts/${accountId}/setup/start`,
    { method: "POST", cache: "no-store" },
  );
}

export async function reauthenticateBrowserAccount(
  accountId: string,
): Promise<BrowserAccountSetup> {
  return requestJson<BrowserAccountSetup>(
    `/api/browser-accounts/${accountId}/reauthenticate`,
    { method: "POST", cache: "no-store" },
  );
}

export async function completeBrowserAccountSetup(
  accountId: string,
): Promise<BrowserAccount> {
  return requestJson<BrowserAccount>(
    `/api/browser-accounts/${accountId}/setup/complete`,
    { method: "POST", cache: "no-store" },
  );
}

export async function cancelBrowserAccountSetup(
  accountId: string,
): Promise<BrowserAccount> {
  return requestJson<BrowserAccount>(
    `/api/browser-accounts/${accountId}/setup/cancel`,
    { method: "POST", cache: "no-store" },
  );
}

export async function revokeBrowserAccount(
  accountId: string,
): Promise<BrowserAccount> {
  return requestJson<BrowserAccount>(
    `/api/browser-accounts/${accountId}/revoke`,
    { method: "POST", cache: "no-store" },
  );
}

export async function deleteBrowserAccount(accountId: string): Promise<void> {
  await requestEmpty(`/api/browser-accounts/${accountId}`, {
    method: "DELETE",
    cache: "no-store",
  });
}

export async function loadMcpPresets(): Promise<McpPresetListResponse> {
  return requestJson<McpPresetListResponse>("/api/mcp/presets", {
    method: "GET",
  });
}

export async function createMcpServer(
  request: CreateMcpServerRequest,
): Promise<McpServer> {
  return requestJson<McpServer>("/api/mcp/servers", {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function createMcpServerFromPreset(
  presetId: string,
  request: CreateMcpServerFromPresetRequest,
): Promise<McpServer> {
  return requestJson<McpServer>(`/api/mcp/presets/${presetId}/servers`, {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function updateMcpServer(
  serverId: string,
  request: UpdateMcpServerRequest,
): Promise<McpServer> {
  return requestJson<McpServer>(`/api/mcp/servers/${serverId}`, {
    body: JSON.stringify(request),
    method: "PATCH",
  });
}

export async function deleteMcpServer(serverId: string): Promise<void> {
  await requestEmpty(`/api/mcp/servers/${serverId}`, { method: "DELETE" });
}

export async function saveMcpStaticAuth(
  serverId: string,
  request: SaveMcpStaticAuthRequest,
): Promise<McpServer> {
  return requestJson<McpServer>(`/api/mcp/servers/${serverId}/auth/static`, {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function startMcpOAuth(
  serverId: string,
  request: StartMcpOAuthRequest,
): Promise<StartMcpOAuthResponse> {
  return requestJson<StartMcpOAuthResponse>(
    `/api/mcp/servers/${serverId}/auth/start`,
    {
      body: JSON.stringify(request),
      method: "POST",
    },
  );
}

export async function revokeMcpAuth(serverId: string): Promise<McpServer> {
  return requestJson<McpServer>(`/api/mcp/servers/${serverId}/auth/revoke`, {
    method: "POST",
  });
}

export async function testMcpServer(serverId: string): Promise<McpServer> {
  return requestJson<McpServer>(`/api/mcp/servers/${serverId}/test`, {
    method: "POST",
  });
}

export async function loadChats(): Promise<ChatSummary[]> {
  const response = await requestJson<ChatListResponse>("/api/chats", {
    method: "GET",
  });
  return response.chats;
}

export async function createChat(
  request: CreateChatRequest,
): Promise<ChatSummary> {
  return requestJson<ChatSummary>("/api/chats", {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function loadChat(chatId: string): Promise<ChatDetail> {
  return requestJson<ChatDetail>(`/api/chats/${chatId}`, {
    method: "GET",
  });
}

export async function loadChatDelegatedRuns(
  chatId: string,
): Promise<ChatDelegatedRun[]> {
  const response = await requestJson<ChatDelegatedRunListResponse>(
    `/api/chats/${chatId}/delegated-runs`,
    { method: "GET" },
  );
  return response.delegated_runs;
}

export async function updateChat(
  chatId: string,
  request: UpdateChatRequest,
): Promise<ChatSummary> {
  return requestJson<ChatSummary>(`/api/chats/${chatId}`, {
    body: JSON.stringify(request),
    method: "PATCH",
  });
}

export async function deleteChat(chatId: string): Promise<void> {
  await requestEmpty(`/api/chats/${chatId}`, { method: "DELETE" });
}

export async function uploadChatAttachment(
  chatId: string,
  file: File,
  clientDraftId: string,
): Promise<ChatAttachmentSummary> {
  const idToken = await getFirebaseIdToken();
  const body = new FormData();
  body.append("file", file);
  body.append("client_draft_id", clientDraftId);
  body.append("client_source", "web");
  const response = await fetch(
    `${activeApiBaseUrl}/api/chats/${chatId}/attachments`,
    {
      body,
      headers: { Authorization: `Bearer ${idToken}` },
      method: "POST",
    },
  );
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  return (await response.json()) as ChatAttachmentSummary;
}

export async function deleteChatAttachment(
  chatId: string,
  attachmentId: string,
): Promise<void> {
  await requestEmpty(
    `/api/chats/${chatId}/attachments/${attachmentId}`,
    { method: "DELETE" },
  );
}

export async function loadChatAttachment(
  chatId: string,
  attachmentId: string,
  variant: "full" | "thumbnail" = "thumbnail",
): Promise<Blob> {
  const idToken = await getFirebaseIdToken();
  const params = new URLSearchParams({ variant });
  const response = await fetch(
    `${activeApiBaseUrl}/api/chats/${chatId}/attachments/${attachmentId}/content?${params}`,
    {
      headers: { Authorization: `Bearer ${idToken}` },
      method: "GET",
    },
  );
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  return response.blob();
}

export async function streamChatMessage(
  chatId: string,
  text: string,
  options: StreamChatMessageOptions,
): Promise<void> {
  const idToken = await getFirebaseIdToken();
  const response = await fetch(
    `${activeApiBaseUrl}/api/chats/${chatId}/messages/stream`,
    {
      body: JSON.stringify({
        text,
        attachment_ids: options.attachmentIds ?? [],
        client_turn_id: options.clientTurnId,
        location: options.location ?? undefined,
      }),
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${idToken}`,
      },
      method: "POST",
      signal: options.signal,
    },
  );

  if (!response.ok) {
    throw new ApiRequestError(response.status, await errorMessage(response));
  }

  if (response.body === null) {
    throw new Error("Streaming response did not include a body");
  }

  await parseSseStream<ChatStreamEvent>(response.body, options.onEvent);
}

export async function loadRuns(
  options: {
    statuses?: readonly AgentRunStatus[];
    kinds?: readonly AgentRunKind[];
    agentId?: string;
    scheduleId?: string;
    limit?: number;
  } = {},
): Promise<AgentRunSummary[]> {
  const params = new URLSearchParams();
  for (const status of options.statuses ?? []) {
    params.append("status", status);
  }
  for (const kind of options.kinds ?? []) {
    params.append("kind", kind);
  }
  if (options.agentId !== undefined) {
    params.set("agent_id", options.agentId);
  }
  if (options.scheduleId !== undefined) {
    params.set("schedule_id", options.scheduleId);
  }
  if (options.limit !== undefined) {
    params.set("limit", String(options.limit));
  }
  const response = await requestJson<RunListResponse>(
    withQuery("/api/runs", params),
    { method: "GET" },
  );
  return response.runs;
}

export async function loadCodexAccount(): Promise<CodexAccount> {
  return requestJson<CodexAccount>("/api/codex-cloud/account", {
    method: "GET",
  });
}

export async function startCodexLogin(): Promise<CodexAuthSession> {
  return requestJson<CodexAuthSession>("/api/codex-cloud/account/login", {
    cache: "no-store",
    method: "POST",
  });
}

export async function loadCodexAuthSession(
  authSessionId: string,
): Promise<CodexAuthSession> {
  return requestJson<CodexAuthSession>(
    `/api/codex-cloud/auth-sessions/${authSessionId}`,
    { cache: "no-store", method: "GET" },
  );
}

export async function cancelCodexLogin(
  authSessionId: string,
): Promise<CodexAuthSession> {
  return requestJson<CodexAuthSession>(
    `/api/codex-cloud/auth-sessions/${authSessionId}/cancel`,
    { method: "POST" },
  );
}

export async function saveCodexEnvironment(request: {
  environment_id: string;
  environment_label?: string | null;
}): Promise<CodexAccount> {
  return requestJson<CodexAccount>("/api/codex-cloud/account/environment", {
    body: JSON.stringify(request),
    method: "PUT",
  });
}

export async function disconnectCodexAccount(): Promise<void> {
  await requestEmpty("/api/codex-cloud/account", { method: "DELETE" });
}

export async function createCodexCloudTask(
  request: CreateCodexCloudTaskRequest,
): Promise<CodexCloudTask> {
  return requestJson<CodexCloudTask>("/api/codex-cloud/tasks", {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function loadCodexCloudTasks(): Promise<CodexCloudTask[]> {
  const response = await requestJson<{ tasks: CodexCloudTask[] }>(
    "/api/codex-cloud/tasks",
    { method: "GET" },
  );
  return response.tasks;
}

export async function loadActiveRuns(): Promise<AgentRunSummary[]> {
  const response = await requestJson<RunListResponse>("/api/runs/active", {
    method: "GET",
  });
  return response.runs;
}

export async function loadRun(runId: string): Promise<AgentRunDetail> {
  return requestJson<AgentRunDetail>(`/api/runs/${runId}`, { method: "GET" });
}

export async function loadRunEvents(
  runId: string,
  sinceEventId?: string,
): Promise<AgentRunEvent[]> {
  const params = new URLSearchParams();
  if (sinceEventId !== undefined) {
    params.set("since_event_id", sinceEventId);
  }
  const response = await requestJson<RunEventListResponse>(
    withQuery(`/api/runs/${runId}/events`, params),
    { method: "GET" },
  );
  return response.events;
}

export async function cancelRun(runId: string, reason?: string): Promise<AgentRunDetail> {
  return requestJson<AgentRunDetail>(`/api/runs/${runId}/cancel`, {
    body: JSON.stringify({ reason: reason ?? null }),
    method: "POST",
  });
}

export async function retryRun(runId: string): Promise<AgentRunDetail> {
  return requestJson<AgentRunDetail>(`/api/runs/${runId}/retry`, {
    method: "POST",
  });
}

export async function streamRunEvents(
  options: StreamRunEventsOptions,
): Promise<void> {
  const path =
    options.runId === undefined
      ? "/api/runs/events/stream"
      : `/api/runs/${options.runId}/events/stream`;
  let cursor = options.sinceEventId;
  let retryCount = 0;
  let reconnecting = false;

  while (true) {
    options.signal?.throwIfAborted();
    const idToken = await getFirebaseIdToken(reconnecting);
    options.signal?.throwIfAborted();
    const params = new URLSearchParams();
    if (cursor !== undefined) {
      params.set("since_event_id", cursor);
    }
    let response: Response;
    try {
      response = await fetch(`${activeApiBaseUrl}${withQuery(path, params)}`, {
        headers: {
          Authorization: `Bearer ${idToken}`,
        },
        method: "GET",
        signal: options.signal,
      });
    } catch (error) {
      options.signal?.throwIfAborted();
      if (!(error instanceof TypeError)) {
        throw error;
      }
      await waitForRunEventRetry(runEventRetryDelay(retryCount++), options.signal);
      reconnecting = true;
      continue;
    }

    if (response.status === 429 || response.status === 503) {
      const delay = runEventRetryDelay(retryCount++, response.headers.get("Retry-After"));
      await errorMessage(response);
      await waitForRunEventRetry(delay, options.signal);
      reconnecting = true;
      continue;
    }
    if (!response.ok) {
      throw new ApiRequestError(response.status, await errorMessage(response));
    }
    if (response.body === null) {
      throw new Error("Streaming response did not include a body");
    }
    try {
      await parseSseStream<AgentRunEvent>(response.body, (event) => {
        options.onEvent(event);
        cursor = event.id;
        retryCount = 0;
      }, {
        signal: options.signal,
        readError: (cause) => new RunEventStreamTransportError("Run event stream disconnected", { cause }),
      });
    } catch (error) {
      options.signal?.throwIfAborted();
      if (!(error instanceof RunEventStreamTransportError)) {
        throw error;
      }
    }
    await waitForRunEventRetry(runEventRetryDelay(retryCount++), options.signal);
    reconnecting = true;
  }
}

class RunEventStreamTransportError extends Error {}

function runEventRetryDelay(retryCount: number, retryAfter: string | null = null): number {
  const maxDelay = 30_000;
  if (retryAfter !== null && retryAfter.trim() !== "") {
    const seconds = Number(retryAfter);
    const delay = Number.isFinite(seconds)
      ? seconds * 1_000
      : Date.parse(retryAfter) - Date.now();
    if (Number.isFinite(delay) && delay >= 0) {
      return Math.min(maxDelay, Math.max(1_000, delay));
    }
  }
  return Math.min(maxDelay, 1_000 * 2 ** Math.min(retryCount, 5));
}

function waitForRunEventRetry(delay: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const onAbort = () => {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", onAbort);
      reject(signal?.reason);
    };
    const timeout = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, delay);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function loadSchedules(
  statuses?: readonly ScheduleStatus[],
): Promise<AgentSchedule[]> {
  const params = new URLSearchParams();
  for (const status of statuses ?? []) {
    params.append("status", status);
  }
  const response = await requestJson<ScheduleListResponse>(
    withQuery("/api/schedules", params),
    { method: "GET" },
  );
  return response.schedules;
}

export async function createSchedule(
  request: CreateScheduleRequest,
): Promise<AgentSchedule> {
  return requestJson<AgentSchedule>("/api/schedules", {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function updateSchedule(
  scheduleId: string,
  request: UpdateScheduleRequest,
): Promise<AgentSchedule> {
  return requestJson<AgentSchedule>(`/api/schedules/${scheduleId}`, {
    body: JSON.stringify(request),
    method: "PATCH",
  });
}

export async function deleteSchedule(scheduleId: string): Promise<void> {
  await requestEmpty(`/api/schedules/${scheduleId}`, { method: "DELETE" });
}

export async function runScheduleNow(scheduleId: string): Promise<AgentSchedule> {
  return requestJson<AgentSchedule>(`/api/schedules/${scheduleId}/run-now`, {
    method: "POST",
  });
}

export async function pauseSchedule(scheduleId: string): Promise<AgentSchedule> {
  return requestJson<AgentSchedule>(`/api/schedules/${scheduleId}/pause`, {
    method: "POST",
  });
}

export async function resumeSchedule(scheduleId: string): Promise<AgentSchedule> {
  return requestJson<AgentSchedule>(`/api/schedules/${scheduleId}/resume`, {
    method: "POST",
  });
}

export async function loadAgentWebhooks(): Promise<AgentWebhook[]> {
  const response = await requestJson<AgentWebhookListResponse>(
    "/api/agent-webhooks",
    { method: "GET" },
  );
  return response.webhooks;
}

export async function createAgentWebhook(
  request: CreateAgentWebhookRequest,
): Promise<AgentWebhookCredential> {
  return requestJson<AgentWebhookCredential>("/api/agent-webhooks", {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function updateAgentWebhook(
  webhookId: string,
  request: UpdateAgentWebhookRequest,
): Promise<AgentWebhook> {
  return requestJson<AgentWebhook>(`/api/agent-webhooks/${webhookId}`, {
    body: JSON.stringify(request),
    method: "PATCH",
  });
}

export async function rotateAgentWebhookToken(
  webhookId: string,
): Promise<AgentWebhookCredential> {
  return requestJson<AgentWebhookCredential>(
    `/api/agent-webhooks/${webhookId}/rotate-token`,
    { method: "POST" },
  );
}

export async function deleteAgentWebhook(webhookId: string): Promise<void> {
  await requestEmpty(`/api/agent-webhooks/${webhookId}`, { method: "DELETE" });
}

export function mcpOAuthRedirectUri(): string {
  return `${activeApiBaseUrl}/api/mcp/auth/callback`;
}

function agentStatusPath(
  path: string,
  statuses?: readonly string[],
  limit?: number,
): string {
  const params = new URLSearchParams();
  for (const statusValue of statuses ?? []) {
    params.append("status", statusValue);
  }
  if (limit !== undefined) {
    params.set("limit", String(limit));
  }

  const query = params.toString();
  return query === "" ? path : `${path}?${query}`;
}

function withQuery(path: string, params: URLSearchParams): string {
  const query = params.toString();
  return query === "" ? path : `${path}?${query}`;
}

async function requestJson<ResponseBody>(
  path: string,
  init: RequestInit,
): Promise<ResponseBody> {
  const idToken = await getFirebaseIdToken();
  const response = await fetch(`${activeApiBaseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${idToken}`,
      ...init.headers,
    },
  });

  if (!response.ok) {
    throw new ApiRequestError(response.status, await errorMessage(response));
  }

  return response.json() as Promise<ResponseBody>;
}

async function requestEmpty(path: string, init: RequestInit): Promise<void> {
  const idToken = await getFirebaseIdToken();
  const response = await fetch(`${activeApiBaseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${idToken}`,
      ...init.headers,
    },
  });

  if (!response.ok) {
    throw new ApiRequestError(response.status, await errorMessage(response));
  }
}

async function errorMessage(response: Response): Promise<string> {
  const fallback = `Request failed with ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim() !== "") {
      return body.detail;
    }
  } catch {
    return fallback;
  }

  return fallback;
}

async function parseSseStream<EventBody>(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: EventBody) => void,
  options?: {
    signal?: AbortSignal;
    readError: (cause: unknown) => Error;
  },
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      options?.signal?.throwIfAborted();
      let result: ReadableStreamReadResult<Uint8Array>;
      try {
        result = await reader.read();
      } catch (error) {
        throw options?.readError(error) ?? error;
      }
      options?.signal?.throwIfAborted();
      if (result.done) {
        break;
      }
      buffer += decoder.decode(result.value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        options?.signal?.throwIfAborted();
        const event = parseSseFrame<EventBody>(part);
        if (event !== null) {
          onEvent(event);
        }
      }
    }

    buffer += decoder.decode();
    const finalEvent = parseSseFrame<EventBody>(buffer);
    if (finalEvent !== null) {
      onEvent(finalEvent);
    }
  } finally {
    reader.releaseLock();
  }
}

function parseSseFrame<EventBody>(frame: string): EventBody | null {
  const dataLine = frame
    .split("\n")
    .find((line) => line.startsWith("data: "));
  if (dataLine === undefined) {
    return null;
  }

  const parsed = JSON.parse(dataLine.slice(6)) as EventBody;
  return parsed;
}

export type ImprovementCandidate = {
  payload:
    | { kind: "memory"; memory_kind: AgentMemoryKind; text: string; sensitivity: AgentMemorySensitivity; target: string | null }
    | { kind: "skill"; name: string; slug: string; category: AgentSkillCategory; description: string; body_markdown: string; revise: boolean }
    | { kind: "diagnostic"; text: string };
  evidence: { kind: "session" | "transcript" | "tool" | "run" | "error"; key: string }[];
  reason: string;
  confidence: number;
};
export type AgentImprovement = {
  id: string;
  status: string;
  candidate: ImprovementCandidate | null;
  content_hash: string | null;
  decision_mode: string | null;
  policy_version: string | null;
  reasons: string[];
  assessment: { confidence: number; reason: string; model_revision: string | null } | null;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  created_at: string;
  reviewed_at: string | null;
};
export type LearningSettings = { automatic: boolean; chosen_at: string | null };
export type ImprovementEvidence = {
  items: { reference: { kind: string; key: string }; conversation: string; category: string; summary: string; created_at: string; context_withheld: boolean }[];
  unavailable: number;
  truncated: boolean;
  coverage: string;
};
export function loadAgentImprovements(before?: AgentImprovement): Promise<AgentImprovement[]> {
  const query = before ? `?${new URLSearchParams({ before: before.created_at, before_id: before.id })}` : "";
  return requestJson<AgentImprovement[]>(`/api/improvements${query}`, { method: "GET" });
}
export function loadLearningSettings(): Promise<LearningSettings> {
  return requestJson<LearningSettings>("/api/improvements/settings", { method: "GET" });
}
export function saveLearningSettings(automatic: boolean): Promise<LearningSettings> {
  return requestJson<LearningSettings>("/api/improvements/settings", { method: "PUT", body: JSON.stringify({ automatic }) });
}
export function reviewAgentImprovement(proposal: AgentImprovement, action: "approve" | "reject" | "undo"): Promise<AgentImprovement> {
  return requestJson<AgentImprovement>(`/api/improvements/${encodeURIComponent(proposal.id)}/review`, { method: "POST", body: JSON.stringify({ content_hash: proposal.content_hash, action }) });
}
export function loadImprovementEvidence(id: string): Promise<ImprovementEvidence> {
  return requestJson<ImprovementEvidence>(`/api/improvements/${encodeURIComponent(id)}/evidence`, { method: "GET" });
}
