import type { GenerationDetail } from "./api";
import { RepetitionNotice, type RepetitionObservation } from "./RepetitionNotice";
import { WriterOutputSummary, type WriterObservation } from "./WriterOutputSummary";

// Matches Python str.isspace(), counting Unicode code points rather than UTF-16 units.
export function countStoryCharacters(body: string): number {
  return [...body].filter((c) => !/[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]/u.test(c)).length;
}

export type ScaleStatus = { characters: number; status: "below" | "within" | "above" | "not_requested"; deficit: number; excess: number; target: { min_characters: number; max_characters: number } | null; units: { ordinal: number; characters: number }[] };

export function StageScaleProgress({ batch }: { batch: GenerationDetail }) {
  const scale = batch.state.stage_scale_status as ScaleStatus | undefined;
  const discrepancy = batch.state.plan_scope_discrepancy as { planned: number; acceptable: number[]; over_authorized_limit: boolean } | undefined;
  const units = batch.state.writer_unit_status as WriterObservation[] | undefined;
  return <>
    {scale?.target && <p aria-label="阶段篇幅">已保存正文 {scale.characters.toLocaleString()} / {scale.target.min_characters.toLocaleString()}–{scale.target.max_characters.toLocaleString()} 字；{scale.status === "below" ? `低于目标 ${scale.deficit.toLocaleString()} 字` : scale.status === "above" ? `超过参考上界 ${scale.excess.toLocaleString()} 字，完整正文保留` : "处于目标范围"}。不含空白与标题，包含标点。</p>}
    <RepetitionNotice value={batch.state.prose_repetition as RepetitionObservation | undefined} scope="当前阶段正文（包含跨单元）" />
    {units?.map((unit) => <WriterOutputSummary key={unit.call_id} value={unit} />)}
    {discrepancy && <p role="alert">计划已保存为 {discrepancy.planned} 个单元，本次要求 {discrepancy.acceptable.join("–")} 个。{discrepancy.over_authorized_limit ? "已超出授权上限，请减少计划后保存。" : "请在方案编辑中调整，或填写说明并保存以接受当前规模。"}此时尚未启动 Writer，也不会自动重发 Chief。</p>}
  </>;
}
