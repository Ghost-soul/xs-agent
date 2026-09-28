import { describe, expect, it } from "vitest";
import type { GenerationSpec, ProviderProfile } from "./api";
import { fitOutputCapacities, outputCapacities } from "./generationCapacity";
import { generationFormDraft, initialGenerationSpec, type GenerationSetup } from "./generationDefaults";

const provider = { id: "local", enabled: true, allow_story_data: true, credential_required: false, default_model: "small", models: [{ id: "small", max_output_tokens: 32768 }, { id: "large", max_output_tokens: 100000 }] } as ProviderProfile;
const spec = { profile_id: "local", chief_model: "small", writer_model: "large", chief_output_limit: 100000, writer_output_limit: 80000, auxiliary_output_limit: 60000, roles: { memory: { model: "small", output_limit: 50000 }, checker: { model: "small", output_limit: 6000 } } } as unknown as GenerationSpec;

describe("endpoint output capacity", () => {
  it("adjusts oversized values per role without raising smaller manual limits", () => {
    const before = structuredClone(spec);
    const adjusted = { ...spec, ...fitOutputCapacities(spec, provider) };
    expect(adjusted).toMatchObject({ chief_output_limit: 32768, writer_output_limit: 80000, auxiliary_output_limit: 32768, roles: { memory: { output_limit: 32768 }, checker: { output_limit: 6000 } } });
    expect(outputCapacities(adjusted, provider).every((c) => c.capacity == null || c.requested <= c.capacity)).toBe(true);
    expect(spec).toEqual(before);
  });
  it("fits fresh defaults while retaining an explicit incompatible draft for visible correction", () => {
    const setup = { base_version_id: "v", version: 1, characters: [], narrative_position: {}, style: { selection_mode: "specified", genre_card_id: "world", secondary_genre_card_ids: [], matched_cards: [{ id: "world", name: "世界", layer: "genre" }] } } as GenerationSetup;
    expect(initialGenerationSpec(setup, [provider]).chief_output_limit).toBe(32768);
    const draft = generationFormDraft({ ...initialGenerationSpec(setup, [provider]), chief_output_limit: 100000 });
    expect(initialGenerationSpec(setup, [provider], undefined, draft).chief_output_limit).toBe(100000);
  });
});
