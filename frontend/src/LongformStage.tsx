import { useEffect, useRef, useState } from "react";
import { api, errorMessage, isAbortError, jsonBody, StableWriteOperationKeys, type GenerationDetail, type GenerationSpec } from "./api";
import { actionName, plannedUnitCount } from "./GenerationProgress";

export function StageSettings({ spec, update, history }: { spec: GenerationSpec; update: (value: Partial<GenerationSpec>) => void; history: { id: string; status: string; direction: string }[] }) {
  return <fieldset><legend>叙事单元与阶段接续</legend>
    <label>生成方式<select value={spec.stage_mode ?? "single-unit-v1"} onChange={(e) => update({ stage_mode: e.target.value as GenerationSpec["stage_mode"], unit_limit: e.target.value === "longform-v1" ? 3 : 1, chapter_count: null, target_characters: null, length_policy: "unit-v1", milestone_unit: null, previous_stage_id: null })}><option value="longform-v1">连续完成计划中的叙事单元</option><option value="single-unit-v1">仅完成一个叙事单元</option></select></label>
    {spec.stage_mode === "longform-v1" && <><div className="generation-grid">
      <label>接续前一阶段（可选）<select value={spec.previous_stage_id ?? ""} onChange={(e) => update({ previous_stage_id: e.target.value || null })}><option value="">从当前正式版本重新设计</option>{history.filter((h) => h.status === "adopted").map((h) => <option key={h.id} value={h.id}>{h.direction}</option>)}</select></label>
    </div><p>每个单元完成后由 Memory 保存事实接力，再进入下一个单元。完整单元按原边界生成章节草稿，保留全部正文，由你审核采用。下一阶段接续当前正式末尾。</p></>}
  </fieldset>;
}

type Chapter = { id: string; number: number; ordinal: number; start: number; end: number; position: Record<string, unknown> | null; position_status: string; position_source?: { end: number; remaining_characters: number } | null; factual_changes: Record<string, unknown>; observations: unknown[]; diagnostics: string[]; facts_status: string };
type Suggestions = { candidate_sha256: string; manifest_sha256: string; chapters: Chapter[]; tail: { start: number; end: number } | null; deferred_fact_count: number };
type Approval = { chapter_id: string; title: string; narrative_position: Record<string, unknown>; factual_changes: Record<string, unknown>; facts_confirmed: boolean };
type Preview = { preview_sha256: string; chapters: unknown[]; full_stage: boolean; proposal_eligible: boolean };

function missingPosition(approval: Approval): string[] {
  return [["current_location", "章末地点"], ["recent_major_event", "本章实际事件"]]
    .filter(([key]) => !String(approval.narrative_position[key] ?? "").trim()).map(([, label]) => label);
}

export function LongformStage({ batch, base, disabled, onAdopted }: { batch: GenerationDetail; base: string; disabled: boolean; onAdopted: () => Promise<void> }) {
  const artifact = (kind: string) => batch.artifacts.find((a) => a.id === batch.state[`${kind}_id`]);
  const units = (artifact("units")?.payload as { items?: { ordinal: number; start: number; end: number; memory_id?: string }[] } | undefined)?.items ?? [];
  const early = artifact("early_review"), comparison = artifact("chief_comparison");
  const [suggestions, setSuggestions] = useState<Suggestions | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [count, setCount] = useState(1);
  const [pendingFacts, setPendingFacts] = useState<number[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [accept, setAccept] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const writes = useRef(new StableWriteOperationKeys());
  const previewBinding = JSON.stringify([batch.id, batch.state.candidate_id, suggestions?.manifest_sha256, count, approvals.slice(0, count), pendingFacts]);
  const currentBinding = useRef(previewBinding); currentBinding.current = previewBinding;
  const editable = !disabled && !["adopted", "archived", "outcome_uncertain", "draft"].includes(batch.status);
  useEffect(() => {
    setSuggestions(null); setApprovals([]); setPendingFacts([]); setPreview(null); setAccept(false); setError("");
    if (!batch.state.segments_id) return;
    const controller = new AbortController();
    void api<Suggestions>(`${base}/${batch.id}/stage-chapters`, { signal: controller.signal }).then((value) => {
      if (controller.signal.aborted) return;
      setSuggestions(value); setCount(value.chapters.length);
      setApprovals(value.chapters.map((c) => ({ chapter_id: c.id, title: ((artifact("title")?.payload as { chapters?: { id: string; titles: string[] }[] } | undefined)?.chapters?.find((t) => t.id === c.id)?.titles[0] ?? ""), narrative_position: c.position ?? { current_location: "", recent_major_event: "" }, factual_changes: c.factual_changes, facts_confirmed: false })));
    }).catch((e) => { if (!isAbortError(e)) setError(errorMessage(e)); });
    return () => controller.abort();
  }, [batch.id, batch.state.segments_id, batch.state.memory_id, base]);
  function change(index: number, patch: Partial<Approval>) { setApprovals((old) => old.map((a, i) => i === index ? { ...a, ...patch, facts_confirmed: patch.facts_confirmed ?? false } : a)); setPreview(null); setAccept(false); }
  async function perform(action: () => Promise<void>) { setBusy(true); setError(""); try { await action(); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); } }
  return <section className="generation-panel"><h3>阶段进度与逐章采用</h3>
    <p>已保存 {units.length} / {plannedUnitCount(batch)} 个单元（授权上限 {batch.spec.unit_limit}），{units.filter((u) => u.memory_id).length} 个完成事实接力。下一动作：{batch.next_action ? actionName(batch.next_action) : "请查看上方当前创作进度"}。</p>
    <ol>{units.map((u) => <li key={u.ordinal}>单元 {u.ordinal} · {u.end - u.start} 字 · {u.memory_id ? "接力已保存" : "等待事实接力"}</li>)}</ol>
    {early && <details><summary>首章早读及其精确范围</summary><pre>{JSON.stringify(early.payload, null, 2)}</pre></details>}
    {comparison && <details><summary>最近一次 Chief 题材对照</summary><pre>{JSON.stringify(comparison.payload, null, 2)}</pre></details>}
    {suggestions && <><p>完整章节 {suggestions.chapters.length}；{suggestions.tail ? `另有 ${suggestions.tail.end - suggestions.tail.start} 字尾稿保留在候选中` : "正文已完整拆章"}。证据尚未完整的事实 {suggestions.deferred_fact_count} 项。</p>
      {!suggestions.chapters.length && <p>当前长度或自然段边界尚不能形成完整章节。可继续剩余单元，或保存作者修改后重新核验。</p>}
      {editable && suggestions.chapters.length > 0 && <><label>本次采用范围<select value={count} onChange={(e) => { setCount(Number(e.target.value)); setPreview(null); setAccept(false); }}>{suggestions.chapters.map((_, i) => <option value={i + 1} key={i}>前 {i + 1} 章</option>)}</select></label>
        {suggestions.chapters.slice(0, count).map((chapter, index) => { const approval = approvals[index]; if (!approval) return null; return <fieldset key={chapter.id}><legend>第 {chapter.ordinal} 章 · {chapter.end - chapter.start} 字</legend>
          <p>事实提取：{chapter.facts_status}；章末现场：{chapter.position_status === "known" ? "有完整单元接力" : chapter.position_status === "reference" ? "已带入本章内较早的接力参考，请核对到章末是否有变化" : "拆分处无独立接力，请根据本章正文填写"}。</p>
          {chapter.position_status === "reference" && chapter.position_source && <details open><summary>参考之后至章末还有 {chapter.position_source.remaining_characters} 字，请核对现场变化</summary><p className="generation-prose">{String((artifact("candidate")?.payload as Record<string, unknown>)?.body ?? "").slice(chapter.position_source.end, chapter.end).trim()}</p></details>}
          <details><summary>本章正文与事实证据</summary><pre>{String((artifact("candidate")?.payload as Record<string, unknown>)?.body ?? "").slice(chapter.start, chapter.end)}</pre><pre>{JSON.stringify(chapter.observations, null, 2)}</pre></details>
          {chapter.diagnostics.map((d, i) => <p key={i} role="alert">{d}</p>)}
          <label>第 {chapter.ordinal} 章标题<input value={approval.title} onChange={(e) => change(index, { title: e.target.value })} placeholder={`第${chapter.ordinal}章`} /></label>
          {[["current_location", "章末地点"], ["recent_major_event", "本章实际事件"], ["current_conflict", "章末冲突"], ["in_progress", "未完成事项"]].map(([key, label]) => <label key={key}>{label}<textarea aria-required={key === "current_location" || key === "recent_major_event"} aria-invalid={(key === "current_location" || key === "recent_major_event") && !String(approval.narrative_position[key] ?? "").trim()} value={String(approval.narrative_position[key] ?? "")} onChange={(e) => change(index, { narrative_position: { ...approval.narrative_position, [key]: e.target.value } })} /></label>)}
          {missingPosition(approval).length > 0 && <p role="status">第 {chapter.ordinal} 章还需补充：{missingPosition(approval).join("、")}。填写后再核对本章事实。</p>}
          <details><summary>逐章事实高级修正</summary><JsonFacts value={approval.factual_changes} onChange={(value) => change(index, { factual_changes: value })} onPending={(pending) => { setPendingFacts((old) => pending ? [...new Set([...old, index])] : old.filter((i) => i !== index)); setPreview(null); setAccept(false); }} /></details>
          <label className="check"><input type="checkbox" disabled={pendingFacts.includes(index)} checked={approval.facts_confirmed && !pendingFacts.includes(index)} onChange={(e) => change(index, { facts_confirmed: e.target.checked })} />已核对本章事实和现场，没有带入后章结果</label>
        </fieldset>; })}
        <button disabled={busy || pendingFacts.some((i) => i < count) || !approvals.slice(0, count).every((a) => a.facts_confirmed && !missingPosition(a).length)} onClick={() => void perform(async () => { const binding = currentBinding.current; const result = await api<Preview>(`${base}/${batch.id}/stage-adoption-preview`, { method: "POST", body: jsonBody({ chapters: approvals.slice(0, count) }) }); if (currentBinding.current === binding) setPreview(result); })}>预览阶段采用</button>
        {preview && <><p>将创建 {preview.chapters.length} 个正式章节及一个新版本；{preview.proposal_eligible ? "完整阶段可供下一阶段接续" : "未采用正文仍保留，本次不自动沿用未来提案"}。</p><details><summary>将写入的逐章内容与事实</summary><pre>{JSON.stringify(preview, null, 2)}</pre></details><label className="check"><input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} />已读稿并接受当前题材写法</label><button disabled={busy || !accept} onClick={() => void perform(async () => { await writes.current.request(`${base}/${batch.id}/stage-adopt`, { method: "POST", body: jsonBody({ chapters: approvals.slice(0, count), preview_sha256: preview.preview_sha256, confirmed: true, accept_genre_deviation: true }) }); setPreview(null); await onAdopted(); })}>确认采用阶段并创建正式版本</button></>}
      </>}
    </>}
    {error && <p role="alert">{error}</p>}
  </section>;
}

function JsonFacts({ value, onChange, onPending }: { value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void; onPending: (pending: boolean) => void }) {
  const [draft, setDraft] = useState(JSON.stringify(value, null, 2));
  const [error, setError] = useState("");
  useEffect(() => { setDraft(JSON.stringify(value, null, 2)); }, [value]);
  return <><textarea className="code-input" aria-label="本章事实变化" value={draft} onChange={(e) => { setDraft(e.target.value); setError(""); onPending(true); }} /><button onClick={() => { try { const parsed: unknown = JSON.parse(draft); if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("事实变化必须是对象"); onChange(parsed as Record<string, unknown>); onPending(false); setError(""); } catch (e) { setError(errorMessage(e)); } }}>应用事实修正</button>{error && <p role="alert">{error}</p>}</>;
}
