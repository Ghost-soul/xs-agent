import type { GenerationDetail, GenerationSpec, ProviderProfile } from "./api";
import { INPUT_TOKEN_LIMIT, TOKEN_LIMIT } from "./generationTokenLimits";
import { isGenreCard, type CreativeCardOption } from "./CreativeCards";
type SavedOrDraftSpec = GenerationDetail["spec"] | GenerationSpec;
export const inputDefaultsRevision = "input-200k-v2";
export const outputDefaultsRevision = "output-100k-v2";
export const feedbackDefaultsRevision = "logic-v1";
export type NarrativeSelectionMode = "random" | "manual";
export type GenerationFormDraft = GenerationSpec & { input_defaults_revision?: string; output_defaults_revision?: string; feedback_defaults_revision?: string; narrative_selection_mode?: NarrativeSelectionMode };

export function initialNarrativeSelectionMode(draft?: GenerationFormDraft): NarrativeSelectionMode {
  return draft?.narrative_selection_mode === "manual" ? "manual" : "random";
}

export function generationFormDraft(spec: GenerationSpec, mode: NarrativeSelectionMode = "random"): GenerationFormDraft {
  return { ...spec, input_defaults_revision: inputDefaultsRevision, output_defaults_revision: outputDefaultsRevision, feedback_defaults_revision: feedbackDefaultsRevision, narrative_selection_mode: mode };
}

export type GenerationSetup = {
  available_cards?: CreativeCardOption[];
  configuration_revision?: string;
  context_budget_revision?: string;
  output_budget_revision?: string;
  automation_revision?: string;
  base_version_id: string;
  version: number;
  characters: { id: string; name: string; library_status?: string }[];
  narrative_position: Record<string, unknown>;
  style: { selection_mode: string; genre_card_id: string | null; secondary_genre_card_ids: string[]; matched_cards: { id: string; name: string; layer?: string }[] };
};

export const defaultDirection = "围绕选中的叙事内容推进本阶段，让人物选择与后果改变故事，并从当前正式处境自然接续。";

export function eligibleProfiles(profiles: ProviderProfile[]) {
  return profiles.filter((p) => p.enabled && p.allow_story_data && (!p.credential_required || p.has_api_key));
}

export function profileDefaults(profile: ProviderProfile | undefined, previous?: SavedOrDraftSpec): Partial<GenerationSpec> {
  const same = previous?.profile_id === profile?.id;
  const valid = (model: string | undefined) => !!profile?.models.some((m) => m.id === model);
  const chief = same && valid(previous?.chief_model) ? previous!.chief_model : profile?.default_model ?? "";
  const writer = same && valid(previous?.writer_model) ? previous!.writer_model : profile?.default_model ?? "";
  return {
    profile_id: profile?.id ?? "", chief_model: chief, writer_model: writer,
    chief_tokenizer_id: same && chief === previous?.chief_model ? previous?.chief_tokenizer_id : null,
    writer_tokenizer_id: same && writer === previous?.writer_model ? previous?.writer_tokenizer_id : null,
    roles: same ? Object.fromEntries(Object.entries(previous?.roles ?? {}).filter(([, value]) => valid(value.model))) : {},
  };
}

export function initialGenerationSpec(setup: GenerationSetup, profiles: ProviderProfile[], previous?: SavedOrDraftSpec, draft?: GenerationFormDraft): GenerationSpec {
  const inherited = draft ?? previous;
  const available = eligibleProfiles(profiles);
  const profile = available.find((p) => p.id === inherited?.profile_id) ?? available[0];
  const initial: GenerationSpec = {
    workflow: "novel-run-v1", context_policy: "chief-focus-v4", automation_policy: "stage-auto-v1", stage_mode: "longform-v1", unit_limit: 3, character_selection: "chief-auto-v1",
    enable_editor: false, generate_title: false, base_version_id: setup.base_version_id,
    feedback_policy: "logic-v1", writing_policy: "guided-v1", enable_checker: true, enable_reader: false,
    focus_card_id: setup.style.genre_card_id ?? "", supporting_card_id: null,
    card_selection_policy: "separate-v1", narrative_card_ids: [], narrative_policy: "plot-led-v3", plan_policy: "bounded-v1",
    direction: "", author_boundaries: "", character_ids: [], viewpoint: "", relationship_scope: "genre-led", relationship_character_ids: [],
    profile_id: "", chief_model: "", writer_model: "", length_policy: "unit-v1", chapter_count: null, target_characters: null,
    input_limit: INPUT_TOKEN_LIMIT, chief_output_limit: TOKEN_LIMIT,
    auxiliary_output_limit: TOKEN_LIMIT,
    writer_output_limit: TOKEN_LIMIT, max_cost_cny: inherited?.max_cost_cny ?? "10",
    timeout_seconds: inherited?.timeout_seconds ?? 600, pause_after_plan: false,
  };
  // Reuse resource choices, not an earlier chapter's plot, pair or pinned cast.
  const { input_defaults_revision, output_defaults_revision, feedback_defaults_revision, narrative_selection_mode, ...draftSpec } = draft ?? {};
  const result = { ...initial, ...draftSpec, ...profileDefaults(profile, inherited), workflow: "novel-run-v1" as const, base_version_id: setup.base_version_id };
  // Migrate old form defaults once; later explicit edits survive browser reloads.
  result.input_limit = input_defaults_revision === inputDefaultsRevision ? draft!.input_limit ?? INPUT_TOKEN_LIMIT : INPUT_TOKEN_LIMIT;
  if (output_defaults_revision !== outputDefaultsRevision) {
    result.chief_output_limit = result.writer_output_limit = result.auxiliary_output_limit = TOKEN_LIMIT;
    result.roles = Object.fromEntries(Object.entries(result.roles ?? {}).map(([role, value]) => [role, { ...value, output_limit: TOKEN_LIMIT }]));
  }
  // Convert explicit old draft restrictions into visible, editable author text.
  if (draft && draft.relationship_scope !== "genre-led") {
    const names = (draft.relationship_character_ids ?? []).map((id) => setup.characters.find((c) => c.id === id)?.name ?? id).join("、");
    const legacy = {
      specified_pair: `原草稿指定关系对象：${names}。`,
      explore: names ? `原草稿限定关系探索对象：${names}，未指定最终配对。` : "",
      non_romantic: "原草稿要求：只写非爱情关系。",
      not_applicable: "",
    }[draft.relationship_scope ?? "not_applicable"];
    result.author_boundaries = [result.author_boundaries, legacy].filter(Boolean).join("\n");
  }
  result.relationship_scope = "genre-led";
  result.feedback_policy = "logic-v1";
  result.writing_policy = "guided-v1";
  result.narrative_policy = "plot-led-v3";
  result.plan_policy = "bounded-v1";
  result.length_policy = "unit-v1";
  result.chapter_count = result.target_characters = null;
  result.context_policy = "chief-focus-v4";
  if (result.direction === "围绕主导题材推进下一阶段，让人物选择产生可见后果，并从当前正式故事自然接续。") result.direction = defaultDirection;
  if (result.direction === "从人物当前处境与未完成事件自然续写，让人物选择推动故事发展。") result.direction = defaultDirection;
  result.enable_editor = false;
  result.enable_reader = false;
  result.milestone_unit = null;
  if (result.roles) delete result.roles.reader;
  if (![feedbackDefaultsRevision, "advisory-v1"].includes(feedback_defaults_revision ?? "")) {
    result.enable_checker = true;
    result.enable_reader = false;
    result.milestone_unit = null;
  }
  result.automation_policy = "stage-auto-v1";
  if (draft?.automation_policy !== "stage-auto-v1") result.pause_after_plan = false;
  result.relationship_character_ids = [];
  if (draft?.base_version_id !== setup.base_version_id) result.previous_stage_id = null;
  const cards = setup.available_cards ?? setup.style.matched_cards;
  const genre = (id: string | null | undefined) => cards.some((card) => card.id === id && isGenreCard(card));
  const defaultSecondary = setup.style.secondary_genre_card_ids.filter(genre);
  result.focus_card_id = genre(result.focus_card_id) ? result.focus_card_id : genre(setup.style.genre_card_id) ? setup.style.genre_card_id! : "";
  result.supporting_card_id = draft?.card_selection_policy === "separate-v1" && draft.supporting_card_id === null ? null : genre(result.supporting_card_id) ? result.supporting_card_id : defaultSecondary.length === 1 ? defaultSecondary[0] : null;
  if (result.supporting_card_id === result.focus_card_id) result.supporting_card_id = null;
  // Restore only explicitly saved choices from the mode-aware form, never an old random pair.
  result.narrative_card_ids = ["random", "manual"].includes(narrative_selection_mode ?? "")
    ? [...new Set(draft?.narrative_card_ids ?? [])].filter((id) => cards.some((card) => card.id === id && card.layer === "narrative"))
    : [];
  result.card_selection_policy = "separate-v1";
  return result;
}

export function previewSpec(spec: GenerationSpec): GenerationSpec {
  return { ...spec, enable_reader: false, milestone_unit: null, feedback_policy: "logic-v1", length_policy: "unit-v1", chapter_count: null, target_characters: null, plan_policy: "bounded-v1", card_selection_policy: "separate-v1", narrative_card_ids: spec.narrative_card_ids ?? [], narrative_policy: "plot-led-v3", writing_policy: "guided-v1", automation_policy: "stage-auto-v1", context_policy: "chief-focus-v4", direction: spec.direction.trim() || defaultDirection, relationship_scope: "genre-led", relationship_character_ids: [] };
}
