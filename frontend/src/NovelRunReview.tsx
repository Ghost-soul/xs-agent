import { useEffect, useRef, useState } from "react";
import { errorMessage, jsonBody, StableWriteOperationKeys, type GenerationDetail } from "./api";

import { INPUT_TOKEN_LIMIT, TOKEN_LIMIT } from "./generationTokenLimits";

type Props = { batch: GenerationDetail; base: string; onRefresh: () => Promise<unknown>; disabled: boolean };
type Preview = { preview_sha256: string; slots: string[]; maximum_cost_cny: string; input_limit?: number; output_limit?: number; feedback_policy?: string; enable_checker?: boolean; enable_reader?: boolean };
export function NovelRunReview({ batch, base, onRefresh, disabled }: Props) {
  const current = (kind: string) => batch.artifacts.find((a) => a.id === batch.state[`${kind}_id`])?.payload as Record<string, unknown> | undefined;
  const memory = current("memory"), checker = current("checker"), reader = current("review"), candidate = current("candidate");
  const [mode, setMode] = useState<"verify" | "local" | "rewrite">("verify");
  const [instruction, setInstruction] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [budget, setBudget] = useState("10");
  const [checkerEnabled, setCheckerEnabled] = useState(true);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const writes = useRef(new StableWriteOperationKeys());
  const candidateSha = batch.artifacts.find((a) => a.id === batch.state.candidate_id)?.sha256;
  const binding = JSON.stringify([batch.id, candidateSha, mode, instruction, selected, budget, checkerEnabled]);
  const currentBinding = useRef(binding); currentBinding.current = binding;
  const matched = preview?.feedback_policy === "logic-v1" && preview.enable_checker === checkerEnabled && preview.enable_reader === false;
  useEffect(() => { setPreview(null); setConfirmed(false); setSelected([]); }, [batch.id, candidateSha]);
  async function run(action: () => Promise<void>) { setBusy(true); setError(""); try { await action(); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); } }
  return <section className="generation-panel">
    <h3>事实接力与可选反馈</h3>
    <p>先阅读正文，故事方向与写法由你决定。Checker 提供事实核对，Reader 提供阅读感受，意见不构成采用条件。</p>
    <p>事实接力：{memory ? memory.status === "partial" ? "部分可用，可核对未接纳项" : "已保存" : "尚无当前稿的有效提取"}；检查：{checker ? "意见已保存" : "未提供反馈"}；Reader：{reader ? "阅读感受已保存" : "未提供反馈，不影响读稿采用"}。</p>
    {memory && <><p>{String(memory.outcome ?? "")}</p><details><summary>Memory 提出的实际变化与正文证据</summary>{((memory.changes ?? []) as Record<string, unknown>[]).map((change, index) => <article key={index}><p>{String(change.observation)}</p>{((change.evidence ?? []) as { id: string; text: string }[]).map((e) => <blockquote key={e.id}>{e.text}</blockquote>)}<details><summary>对象与字段变化</summary><pre>{JSON.stringify(change.value, null, 2)}</pre></details></article>)}{((memory.unresolved ?? []) as string[]).map((issue, i) => <p key={i}>待确认：{issue}</p>)}{((memory.diagnostics ?? []) as unknown[]).length > 0 && <details open><summary>未接纳项（有效变化已保留）</summary><pre>{JSON.stringify(memory.diagnostics, null, 2)}</pre></details>}</details></>}
    {checker && <details><summary>{checker.review_scope === "logic-only" ? "逻辑核对意见" : "事实核对意见"}</summary><p>{String(checker.explanation ?? "")}</p>{((checker.issues ?? []) as Record<string, unknown>[]).map((issue, i) => <p key={i}>{String(issue.observation ?? "")}</p>)}<details><summary>查看完整反馈与定位</summary><pre>{String(checker.raw_feedback ?? JSON.stringify(checker, null, 2))}</pre></details></details>}
    {reader && <details><summary>Reader 阅读感受</summary><p className="generation-prose">{String(reader.experience ?? reader.explanation ?? "")}</p><details><summary>完整反馈与阅读范围</summary><pre>{String(reader.raw_feedback ?? JSON.stringify(reader, null, 2))}</pre><pre>{JSON.stringify(reader.reading_scope, null, 2)}</pre></details></details>}
    {candidate && !["adopted", "archived", "outcome_uncertain"].includes(batch.status) && !batch.state.amendment_authorized_sha256 && <details><summary>作者修订或重新提取（独立预算）</summary><p>每阶段至多一次正文修订；改稿后更新事实接力，逻辑检查可选。仅预览不会发送请求。</p>
      <label>处理方式<select value={mode} onChange={(e) => { setMode(e.target.value as typeof mode); setPreview(null); }}><option value="verify">只更新当前稿的事实接力</option><option value="local">Editor 局部修订</option><option value="rewrite">Writer 结构改写</option></select></label>
      <label className="check"><input type="checkbox" checked={checkerEnabled} onChange={(e) => { setCheckerEnabled(e.target.checked); setPreview(null); setConfirmed(false); }} />修订后核对逻辑矛盾</label>
      <label>修改或核验意图<textarea value={instruction} onChange={(e) => { setInstruction(e.target.value); setPreview(null); }} /></label>
      {mode === "local" && <fieldset><legend>允许修改的段落（其余正文保持原文）</legend>{batch.passages?.map((p) => <label className="check" key={String(p.id)}><input type="checkbox" checked={selected.includes(String(p.id))} onChange={(e) => { setSelected(e.target.checked ? [...selected, String(p.id)] : selected.filter((id) => id !== p.id)); setPreview(null); }} />{String(candidate.body).slice(Number(p.start), Number(p.end))}</label>)}</fieldset>}
      <label>本次修订费用上限<input type="number" value={budget} min="0" max="10000" onChange={(e) => { setBudget(e.target.value); setPreview(null); }} /></label>
      <p>本次修订的输入及各角色输出上限均为 100,000 tokens，费用按此额度重新核算。</p>
      <button disabled={disabled || busy || !instruction.trim() || (mode === "local" && !selected.length)} onClick={() => void run(async () => { const value = await writes.current.request<Preview>(`${base}/${batch.id}/amendment-preview`, { method: "POST", body: jsonBody({ candidate_sha256: candidateSha, mode, instruction, paragraph_ids: selected, protected_paragraph_ids: [], max_cost_cny: budget, input_limit: INPUT_TOKEN_LIMIT, output_limit: TOKEN_LIMIT, writing_policy: "guided-v1", narrative_policy: "plot-led-v3", feedback_policy: "logic-v1", enable_checker: checkerEnabled, enable_reader: false }) }); if (currentBinding.current === binding) { setPreview(value); setConfirmed(false); } })}>预览修订与核验费用</button>
      {preview && <><p>{preview.slots.length} 次有限调用，费用上界 ¥{preview.maximum_cost_cny}。输入上限 {preview.input_limit?.toLocaleString()}，各角色输出上限 {preview.output_limit?.toLocaleString()} tokens。</p><label className="check"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />确认本次修订范围、资料外发与费用</label><button disabled={disabled || busy || !confirmed || !matched || preview.input_limit !== INPUT_TOKEN_LIMIT || preview.output_limit !== TOKEN_LIMIT} onClick={() => void run(async () => { await writes.current.request(`${base}/${batch.id}/amendment-authorize`, { method: "POST", body: jsonBody({ preview_sha256: preview.preview_sha256, confirmed: true }) }); await onRefresh(); })}>授权本次修订并核验</button>{(preview.input_limit !== INPUT_TOKEN_LIMIT || preview.output_limit !== TOKEN_LIMIT) && <p role="alert">后端尚未返回二十万输入／十万输出的修订预览，请加载更新后重新核算。</p>}{!matched && <p role="alert">反馈选项尚未由后端确认，请加载更新后重新预览。</p>}</>}
    </details>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
