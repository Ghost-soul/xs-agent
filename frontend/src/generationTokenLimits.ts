import type { GenerationDetail, GenerationSpec } from "./api";

export const INPUT_TOKEN_LIMIT = 200000;
export const TOKEN_LIMIT = 100000;

// Re-preview saved work using the visible current limits, never stale output fields.
export function withCurrentTokenLimits(saved: GenerationDetail["spec"] | GenerationSpec, current: GenerationSpec): GenerationSpec {
  return {
    ...saved,
    direction: ["围绕主导题材推进下一阶段，让人物选择产生可见后果，并从当前正式故事自然接续。", "从人物当前处境与未完成事件自然续写，让人物选择推动故事发展。"].includes(saved.direction) ? "围绕选中的叙事内容推进本阶段，让人物选择与后果改变故事，并从当前正式处境自然接续。" : saved.direction,
    input_limit: current.input_limit ?? INPUT_TOKEN_LIMIT,
    chief_output_limit: current.chief_output_limit ?? TOKEN_LIMIT,
    writer_output_limit: current.writer_output_limit ?? TOKEN_LIMIT,
    auxiliary_output_limit: current.auxiliary_output_limit ?? TOKEN_LIMIT,
    roles: Object.fromEntries(Object.entries(saved.roles ?? {}).filter(([role]) => role !== "reader").map(([role, value]) => [role, {
      ...value,
      output_limit: current.roles?.[role as keyof NonNullable<GenerationSpec["roles"]>]?.output_limit ?? current.auxiliary_output_limit ?? TOKEN_LIMIT,
    }])),
    context_policy: "world-bounded-v1",
    feedback_policy: "logic-v1",
    writing_policy: current.writing_policy ?? "guided-v1",
    narrative_policy: "causal-v1",
    plan_policy: "bounded-v1",
    length_policy: "unit-v1",
    chapter_count: null,
    target_characters: null,
    card_selection_policy: "separate-v1",
    focus_card_id: current.focus_card_id,
    supporting_card_id: current.supporting_card_id ?? null,
    narrative_card_ids: [...(current.narrative_card_ids ?? [])],
    enable_editor: current.feedback_policy === "logic-v1" ? false : current.enable_editor,
    enable_checker: current.enable_checker ?? true,
    enable_reader: false,
    milestone_unit: null,
  };
}
