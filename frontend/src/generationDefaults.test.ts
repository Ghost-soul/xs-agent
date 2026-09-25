import { describe, expect, it } from "vitest";
import type { GenerationSpec, ProviderProfile } from "./api";
import { generationFormDraft, initialGenerationSpec, initialNarrativeSelectionMode, profileDefaults, previewSpec, type GenerationSetup } from "./generationDefaults";
import { withCurrentTokenLimits } from "./generationTokenLimits";

const setup: GenerationSetup = { base_version_id: "v2", version: 2, characters: [{ id: "a", name: "林青" }, { id: "b", name: "江月" }], narrative_position: {}, style: { selection_mode: "specified", genre_card_id: "gl", secondary_genre_card_ids: [], matched_cards: [{ id: "gl", name: "百合" }] } };
const provider = { id: "configured", enabled: true, allow_story_data: true, credential_required: true, has_api_key: true, default_model: "writer", models: [{ id: "writer" }, { id: "chief" }] } as ProviderProfile;

describe("automatic generation defaults", () => {
  it("preserves the genre pair and leaves new narratives for the server draw", () => {
    const cards = [{ id: "world", name: "奇幻", layer: "genre" }, { id: "old-story", name: "身份调查", layer: "narrative" }, { id: "default-story", name: "经营", layer: "narrative" }];
    const source = { ...setup, available_cards: cards, style: { ...setup.style, genre_card_id: "world", secondary_genre_card_ids: ["default-story"], matched_cards: cards } };
    const saved = { ...initialGenerationSpec(source, [provider]), card_selection_policy: "legacy-v1" as const, focus_card_id: "old-story", supporting_card_id: "world", narrative_card_ids: [], plan_policy: "exact-v1" as const };
    const current = initialGenerationSpec(source, [provider], saved);
    expect(current.narrative_card_ids).toEqual([]);
    const preview = withCurrentTokenLimits(saved, current);
    expect(preview).toMatchObject({ card_selection_policy: "separate-v1", focus_card_id: "world", narrative_card_ids: [], narrative_policy: "plot-led-v3", plan_policy: "bounded-v1" });
    expect(withCurrentTokenLimits(saved, { ...current, supporting_card_id: null, narrative_card_ids: [] })).toMatchObject({ supporting_card_id: null, narrative_card_ids: [] });
    expect(withCurrentTokenLimits(saved, { ...current, narrative_card_ids: ["unavailable-card"] }).narrative_card_ids).toEqual(["unavailable-card"]);
    expect(saved.card_selection_policy).toBe("legacy-v1");
  });
  it.each(["legacy-v1", "causal-v1", "plot-led-v2"] as const)("upgrades %s previews and form drafts without changing saved prompt bindings", (policy) => {
    const saved = { ...initialGenerationSpec(setup, [provider]), narrative_policy: policy, direction: "保留作者方向" };
    const current = initialGenerationSpec(setup, [provider], saved, saved);
    expect(current).toMatchObject({ narrative_policy: "plot-led-v3", direction: "保留作者方向" });
    expect(previewSpec(saved).narrative_policy).toBe("plot-led-v3");
    expect(withCurrentTokenLimits(saved, current).narrative_policy).toBe("plot-led-v3");
    expect(saved.narrative_policy).toBe(policy);
  });
  it("does not inherit narratives from project defaults, earlier stages or legacy browser drafts", () => {
    const cards = [
      { id: "world", name: "奇幻", layer: "genre" }, { id: "side", name: "仙侠", layer: "genre" },
      ...["gl", "farm", "mystery"].map((id) => ({ id, name: id, layer: "narrative" })),
    ];
    const currentSetup = { ...setup, available_cards: cards, style: { ...setup.style, genre_card_id: "world", secondary_genre_card_ids: ["side", "gl", "farm"], matched_cards: cards } };
    const initial = initialGenerationSpec(currentSetup, [provider]);
    expect(initial).toMatchObject({ focus_card_id: "world", supporting_card_id: "side", narrative_card_ids: [], card_selection_policy: "separate-v1" });
    const draft = { ...initial, narrative_card_ids: ["gl", "farm", "mystery"] };
    expect(previewSpec(initialGenerationSpec(currentSetup, [provider], undefined, draft)).narrative_card_ids).toEqual([]);
    const cleared = generationFormDraft({ ...initial, supporting_card_id: null, narrative_card_ids: [] });
    expect(initialGenerationSpec(currentSetup, [provider], undefined, cleared)).toMatchObject({ supporting_card_id: null, narrative_card_ids: [] });
    const old = { ...initial, card_selection_policy: "legacy-v1" as const, focus_card_id: "mystery", supporting_card_id: "gl" };
    expect(initialGenerationSpec(currentSetup, [provider], undefined, old)).toMatchObject({ focus_card_id: "world", supporting_card_id: "side", narrative_card_ids: [] });
  });
  it("restores explicit manual choices, filters unavailable cards and keeps mode out of the model spec", () => {
    const cards = [{ id: "world", name: "世界", layer: "genre" }, ...["a", "b", "c"].map((id) => ({ id, name: id, layer: "narrative" }))];
    const source = { ...setup, available_cards: cards, style: { ...setup.style, genre_card_id: "world" } };
    const draft = generationFormDraft({ ...initialGenerationSpec(source, [provider]), narrative_card_ids: ["c", "a", "b", "a", "removed", "world"] }, "manual");
    expect(initialNarrativeSelectionMode(draft)).toBe("manual");
    const restored = initialGenerationSpec(source, [provider], undefined, draft);
    expect(restored.narrative_card_ids).toEqual(["c", "a", "b"]);
    expect(previewSpec(restored)).not.toHaveProperty("narrative_selection_mode");
    const random = generationFormDraft(restored, "random");
    expect(initialNarrativeSelectionMode(random)).toBe("random");
    expect(initialGenerationSpec(source, [provider], undefined, random).narrative_card_ids).toEqual(["c", "a", "b"]);
    const empty = generationFormDraft({ ...restored, narrative_card_ids: [] }, "manual");
    expect(initialGenerationSpec(source, [provider], undefined, empty).narrative_card_ids).toEqual([]);
    expect(initialNarrativeSelectionMode()).toBe("random");
  });
  it("keeps explicitly chosen feedback when upgrading an advisory draft but removes automatic edits", () => {
    const old = { ...generationFormDraft(initialGenerationSpec(setup, [provider])), feedback_policy: "advisory-v1" as const, feedback_defaults_revision: "advisory-v1", enable_checker: false, enable_reader: true, enable_editor: true, milestone_unit: 2 };
    const current = initialGenerationSpec(setup, [provider], undefined, old);
    expect(current).toMatchObject({ feedback_policy: "logic-v1", enable_checker: false, enable_reader: false, enable_editor: false, milestone_unit: null });
    expect(withCurrentTokenLimits(old, current).enable_editor).toBe(false);
    expect(old.enable_editor).toBe(true);
  });
  it("removes retired choices from browser drafts while preserving their historical values", () => {
    const old = { ...initialGenerationSpec(setup, [provider]), feedback_policy: "legacy-v1" as const, enable_reader: true, milestone_unit: 2, stage_mode: "single-unit-v1" as const, generate_title: true, roles: { reader: { model: "retired-reader", output_limit: 6000 } } };
    const migrated = initialGenerationSpec(setup, [provider], undefined, old);
    expect(migrated).toMatchObject({ writing_policy: "guided-v1", feedback_policy: "logic-v1", enable_reader: false, enable_checker: true, milestone_unit: null });
    const chosen = initialGenerationSpec(setup, [provider], undefined, generationFormDraft({ ...migrated, enable_reader: true, enable_checker: false }));
    expect(chosen).toMatchObject({ enable_reader: false, enable_checker: false });
    expect(withCurrentTokenLimits(old, chosen)).toMatchObject({ writing_policy: "guided-v1", feedback_policy: "logic-v1", enable_reader: false, enable_checker: false });
    expect(withCurrentTokenLimits(old, chosen)).toMatchObject({ milestone_unit: null, roles: {}, stage_mode: "single-unit-v1", generate_title: true });
    expect(old.enable_reader).toBe(true);
  });
  it("migrates the earlier 100k revision and prevents stale outputs from re-entering previews", () => {
    const old = { ...initialGenerationSpec(setup, [provider]), input_limit: 58000, chief_output_limit: 48000, writer_output_limit: 12000, auxiliary_output_limit: 6000, roles: { memory: { model: "writer", output_limit: 6000 } }, input_defaults_revision: "input-100k-v1", output_defaults_revision: "output-100k-v1" };
    const current = initialGenerationSpec(setup, [provider], undefined, old);
    const fresh = withCurrentTokenLimits(old, current);
    expect(fresh).toMatchObject({ input_limit: 200000, chief_output_limit: 100000, writer_output_limit: 100000, auxiliary_output_limit: 100000, roles: { memory: { model: "writer", output_limit: 100000 } } });
    expect(old.roles.memory.output_limit).toBe(6000);
    const edited = initialGenerationSpec(setup, [provider], undefined, generationFormDraft({ ...current, input_limit: 80000 }));
    expect(edited.input_limit).toBe(80000);
  });
  it("migrates all old role ceilings once and retains later explicit edits", () => {
    const old = { ...initialGenerationSpec(setup, [provider]), input_limit: 72000, chief_output_limit: 48000, writer_output_limit: 12000, auxiliary_output_limit: 6000, roles: { memory: { model: "writer", output_limit: 6000, tokenizer_id: "m-count" } } };
    const migrated = initialGenerationSpec(setup, [provider], undefined, old);
    expect(migrated).toMatchObject({ input_limit: 200000, chief_output_limit: 100000, writer_output_limit: 100000, auxiliary_output_limit: 100000, roles: { memory: { model: "writer", tokenizer_id: "m-count", output_limit: 100000 } } });
    expect(old.roles.memory.output_limit).toBe(6000);
    const edited = generationFormDraft({ ...migrated, writer_output_limit: 48000, roles: { memory: { model: "writer", output_limit: 32000 } } });
    const restored = initialGenerationSpec(setup, [provider], undefined, edited);
    expect(restored.writer_output_limit).toBe(48000);
    expect(restored.roles?.memory?.output_limit).toBe(32000);
    expect(previewSpec(restored)).not.toHaveProperty("output_defaults_revision");
  });
  it("upgrades only the input revision and retains already confirmed output choices", () => {
    const old = { ...generationFormDraft(initialGenerationSpec(setup, [provider])), input_defaults_revision: "input-100k-v1", input_limit: 100000, writer_output_limit: 48000, roles: { memory: { model: "writer", output_limit: 32000 } } };
    const value = initialGenerationSpec(setup, [provider], undefined, old);
    expect(value).toMatchObject({ input_limit: 200000, context_policy: "chief-focus-v4", writer_output_limit: 48000, roles: { memory: { output_limit: 32000 } } });
    expect(old.input_limit).toBe(100000);
  });
  it("uses 200000 for new stages and old drafts without rewriting saved batches", () => {
    const initial = initialGenerationSpec(setup, [provider]);
    expect(initial.input_limit).toBe(200000);
    const old = { ...initial, input_limit: 58000, direction: "保留作者方向" };
    expect(initialGenerationSpec(setup, [provider], old).input_limit).toBe(200000);
    const migrated = initialGenerationSpec(setup, [provider], old, old);
    expect(migrated).toMatchObject({ input_limit: 200000, direction: old.direction });
    expect(old.input_limit).toBe(58000);
    const explicit = generationFormDraft({ ...migrated, input_limit: 72000 });
    const restored = initialGenerationSpec(setup, [provider], old, explicit);
    expect(restored.input_limit).toBe(72000);
    expect(previewSpec(restored)).not.toHaveProperty("input_defaults_revision");
  });
  it("starts new stages in automatic mode while preserving an explicit new pause choice", () => {
    const old = { ...initialGenerationSpec(setup, [provider]), automation_policy: "legacy-v1", pause_after_plan: true } as GenerationSpec;
    expect(initialGenerationSpec(setup, [provider], undefined, old)).toMatchObject({ automation_policy: "stage-auto-v1", pause_after_plan: false });
    expect(old.pause_after_plan).toBe(true);
    expect(initialGenerationSpec(setup, [provider], undefined, { ...old, automation_policy: "stage-auto-v1" }).pause_after_plan).toBe(true);
  });
  it("uses a configured provider without choosing genres or granting calls", () => {
    const value = initialGenerationSpec(setup, [{ ...provider, id: "unready", has_api_key: false }, provider]);
    expect(value).toMatchObject({ profile_id: "configured", chief_model: "writer", writer_model: "writer", character_selection: "chief-auto-v1", relationship_scope: "genre-led", character_ids: [], viewpoint: "" });
    expect(previewSpec(value).direction).toContain("围绕选中的叙事内容");
    expect(value.direction).toBe("");
    expect(value.chief_output_limit).toBe(100000);
    expect(value.writer_output_limit).toBe(100000);
    expect(value.auxiliary_output_limit).toBe(100000);
    expect(initialGenerationSpec({ ...setup, style: { ...setup.style, genre_card_id: null, matched_cards: [] } }, [provider]).focus_card_id).toBe("");
  });

  it("reuses valid models and budget but not a prior story or hidden pair", () => {
    const previous = { ...initialGenerationSpec(setup, [provider]), direction: "上章方向", character_ids: ["a", "b"], relationship_scope: "specified_pair", relationship_character_ids: ["a", "b"], chief_model: "chief", writer_tokenizer_id: "writer-count", max_cost_cny: "5", roles: { reader: { model: "chief", output_limit: 7000 } } } as GenerationSpec;
    const value = initialGenerationSpec(setup, [provider], previous);
    expect(value).toMatchObject({ chief_model: "chief", writer_tokenizer_id: "writer-count", max_cost_cny: "5", roles: {}, direction: "", character_ids: [], relationship_scope: "genre-led", relationship_character_ids: [] });
    expect(previous.relationship_scope).toBe("specified_pair");
  });

  it("clears model-bound tokenizers and foreign roles when a provider changes", () => {
    const previous = { ...initialGenerationSpec(setup, [provider]), chief_model: "gone", chief_tokenizer_id: "wrong", roles: { reader: { model: "gone", output_limit: 6000 } } } as GenerationSpec;
    expect(initialGenerationSpec(setup, [provider], previous)).toMatchObject({ chief_model: "writer", chief_tokenizer_id: null, roles: {} });
    expect(profileDefaults({ ...provider, id: "other" }, previous)).toMatchObject({ profile_id: "other", chief_tokenizer_id: null, writer_tokenizer_id: null, roles: {} });
  });

  it("preserves explicit old draft restrictions as visible author text", () => {
    const draft = { ...initialGenerationSpec(setup, [provider]), relationship_scope: "specified_pair", relationship_character_ids: ["a", "b"], author_boundaries: "不表白", direction: "她主动留下", viewpoint: "林青", base_version_id: "v1", previous_stage_id: "old" } as GenerationSpec;
    const value = initialGenerationSpec(setup, [provider], undefined, draft);
    expect(value).toMatchObject({ direction: "她主动留下", viewpoint: "林青", relationship_scope: "genre-led", relationship_character_ids: [], previous_stage_id: null });
    expect(value.author_boundaries).toContain("不表白\n原草稿指定关系对象：林青、江月。");
    expect(initialGenerationSpec(setup, [provider], undefined, value).author_boundaries).toBe(value.author_boundaries);
    const neutral = initialGenerationSpec(setup, [provider], undefined, { ...draft, relationship_scope: "not_applicable", relationship_character_ids: [], author_boundaries: "" });
    expect(neutral.author_boundaries).toBe("");
    const limited = initialGenerationSpec(setup, [provider], undefined, { ...draft, relationship_scope: "non_romantic" });
    expect(limited.author_boundaries).toContain("只写非爱情关系");
  });

  it("leaves missing resources visible instead of sending stale credentials or models", () => {
    const old = initialGenerationSpec(setup, [provider]);
    expect(initialGenerationSpec(setup, [{ ...provider, enabled: false }], old)).toMatchObject({ profile_id: "", chief_model: "", writer_model: "", roles: {} });
  });
});
