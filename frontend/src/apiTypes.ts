import type { components } from "./generated/api";
export type GenerationSpec = components["schemas"]["FrozenGenerationSpec"];
export type GenerationDetail = components["schemas"]["GenerationDetail"];
export type StoryPlan = components["schemas"]["StoryPlan"];
export type LocalSearchResponse = components["schemas"]["LocalSearchResponse"];
export type SearchResultResponse = components["schemas"]["SearchResultResponse"];
export type ReaderManifestResponse = components["schemas"]["ReaderManifestResponse"];
export type ReaderManifestChapterResponse = components["schemas"]["ReaderManifestChapterResponse"];
export type ReaderChapterResponse = components["schemas"]["ReaderChapterResponse"];
export type BookmarkResponse = components["schemas"]["BookmarkResponse"];
export type LocalTaskResponse = components["schemas"]["LocalTaskResponse"];
export type ProjectSetupDraftResponse = components["schemas"]["ProjectSetupDraftResponse"];
export type ProjectSetupPayload = components["schemas"]["ProjectSetupPayload"];
export type ImportPreviewResponse = components["schemas"]["ImportPreviewResponse"];
export type ImportCommitResponse = components["schemas"]["ImportCommitResponse"];

export type ServiceHealth = components["schemas"]["HealthResponse"];
export type ProjectSummary = components["schemas"]["ProjectSummary"];
export type ArchivedProjectSummary = components["schemas"]["ArchivedProjectSummary"];
export type ChapterWorkflow = components["schemas"]["ChapterSummary"];
export type VersionSummary = components["schemas"]["VersionSummary"];

export type StoryRestartPreview = {
  project_id: string;
  project_title: string;
  current_version: number;
  setting_source_version: number;
  chapter_count_to_clear: number;
  scene_count_to_clear: number;
  event_count_to_clear: number;
  timeline_constraint_count_to_clear: number;
  disclosure_count_to_clear: number;
  reader_promise_count_to_clear: number;
  preserved_character_count: number;
  preserved_world_rule_count: number;
  preserved_world_lore_count: number;
  preserved_plot_thread_count: number;
  manual_blueprint_review_required: true;
  provider_called: false;
};

export type VersionPrunePreview = {
  project_id: string;
  project_title: string;
  current_version: number;
  keep_latest: 10;
  total_count: number;
  retained_versions: number[];
  candidate_count: number;
  deletable_versions: number[];
  protected_versions: Array<{ number: number; reasons: string[] }>;
  estimated_reclaim_bytes: number;
  revisions_preserved: true;
  provider_outputs_preserved: true;
  audit_preserved: true;
  provider_called: false;
};

export type StoryForce = {
  id: string;
  layer: "world" | "outline";
  kind: string;
  name: string;
  description: string;
  goal: string;
  resources: string[];
  state: string;
  influence: number;
  trajectory: string;
  scope: string;
  active: boolean;
};

export type WorldLoreEntry = {
  id: string;
  category: "world_structure" | "geography" | "history" | "power_system" | "society" | "civilization" | "species" | "faction" | "core_secret";
  subsection: string;
  name: string;
  summary: string;
  details: string[];
  stability: "static" | "dynamic";
  risk_level: "low" | "high";
  source: "author" | "extracted";
  first_seen_chapter: number | null;
  evidence_quote: string;
};

export type WorldRuleEntry = { id: string; statement: string };

export type StoryCharacter = {
  id: string;
  name: string;
  description: string;
  personality: string;
  speech_style: string;
  decision_style: string;
  forbidden_behaviors: string[];
  current_state: string;
  development_history: string[];
  library_status: "active" | "retired";
  tier: "A" | "B" | "C";
  mind_state: CharacterMindState;
  aliases: string[];
  portrayal_profile: CharacterPortrayalProfile;
  location_id: string | null;
};

export type CharacterPortrayalProfile = {
  independent_goal: string;
  unique_competence: string;
  value_boundary: string;
  attention_bias: string[];
  conflict_method: string;
  stress_response: string;
  voice_examples: Array<{
    quote: string;
    source: "author" | "formal_chapter";
    chapter_ordinal: number | null;
  }>;
  anti_examples: string[];
};

export type CharacterMindState = {
  beliefs: Array<{ content: string; confidence: number }>;
  desires: Array<{ content: string; intensity: number }>;
  fears: Array<{ content: string; intensity: number }>;
  current_emotions: Array<{ emotion: string; intensity: number; cause: string }>;
  internal_conflicts: Array<{ side_a: string; side_b: string; pressure: number }>;
  coping_strategy: string;
};

export type Foreshadowing = {
  id: string;
  content: string;
  importance: "low" | "medium" | "high";
  introduced_chapter: number;
  last_advanced_chapter: number;
  expected_payoff: string;
  recovery_condition: string;
  status: "candidate" | "active" | "lightly_reinforced" | "advanced" | "ready_for_payoff" | "fulfilled" | "abandoned";
  related_characters: string[];
  related_plot_threads: string[];
  related_open_questions: string[];
  reader_visible: boolean;
  payoff_readiness: number;
  reminder_after_chapters: number;
  history: string[];
};

export type ScheduledDevelopment = {
  id: string;
  layer: "world" | "outline";
  name: string;
  trigger: string;
  outcome: string;
  timing: string;
  probability: number;
  status: "pending" | "triggered" | "cancelled";
  potential_impact: string;
};

export type PlotThread = {
  id: string;
  name: string;
  status: "open" | "resolved" | "abandoned";
  priority: number;
  last_advanced_chapter: number | null;
  summary: string;
  thread_type: "main" | "side";
  goal: string;
  progress: number;
  related_characters: string[];
};

export type StoryFoundation = { theme: string; core_expression: string; personal_side: string; opposing_side: string; long_term_conflict: string; primary_driver: string; secondary_drivers: string[] };
export type OpenQuestion = { id: string; question: string; category: "character" | "world" | "plot"; status: "open" | "partial" | "answered" | "abandoned"; raised_chapter: number | null; current_clues: string; answer_plan: string; importance: number };
export type NarrativePhase = { id: string; name: string; goal: string; expected_change: string; status: "pending" | "active" | "completed" | "cancelled" };
export type PlotHistoryDecision = { id: string; plan: string; status: "completed" | "cancelled" | "revised"; reason: string; chapter_ordinal: number | null };

export type StoryBlueprint = {
  project_id: string;
  version: number;
  version_id: string;
  characters: StoryCharacter[];
  relationships: Array<{
    id: string;
    source_character_id: string;
    target_character_id: string;
    relation_type: string;
    description: string;
  }>;
  story_forces: StoryForce[];
  scheduled_developments: ScheduledDevelopment[];
  plot_threads: PlotThread[];
  story_foundation: StoryFoundation;
  open_questions: OpenQuestion[];
  foreshadowings: Foreshadowing[];
  narrative_phases: NarrativePhase[];
  plot_history: PlotHistoryDecision[];
  narrative_position: {
    current_phase: string;
    current_time: string;
    current_location: string;
    current_characters: string[];
    recent_major_event: string;
    current_conflict: string;
    in_progress: string;
    horizon: string;
    notes: string;
  };
  world_lore: WorldLoreEntry[];
  world_rules: WorldRuleEntry[];
};

export type ProviderModelOption = {
  id: string;
  label: string | null;
  input_price_cny_per_million: string | null;
  output_price_cny_per_million: string | null;
  context_window: number | null;
  max_output_tokens: number | null;
  streaming_enabled?: boolean | null;
  supports_reasoning_effort?: boolean | null;
  structured_output_modes?: Array<"json_schema_strict" | "json_object" | "prompt_only_schema">;
  supports_usage?: boolean | null;
  supports_streaming?: boolean | null;
  stream_terminal_reliable?: boolean | null;
  finish_reason_semantics?: Record<string, string> | null;
  reasoning_tokens_billed_as_output?: boolean | null;
  verified_at?: string | null;
  verification_version?: string | null;
  verification_evidence?: Record<string, unknown> | null;
};

export type ProviderProfile = {
  id: string;
  profile_revision?: string;
  display_name: string;
  protocol: "deepseek_chat" | "openai_responses" | "openai_chat_completions";
  base_url: string;
  enabled: boolean;
  is_local: boolean;
  credential_required: boolean;
  allow_story_data: boolean;
  structured_output_mode: "json_schema" | "json_object" | "prompt_only";
  supports_reasoning_effort: boolean;
  streaming_enabled: boolean;
  default_model: string;
  models: ProviderModelOption[];
  built_in: boolean;
  has_api_key: boolean;
};
