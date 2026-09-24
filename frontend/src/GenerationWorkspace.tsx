import { useEffect, useRef, useState } from "react";
import { api, errorMessage, isAbortError, jsonBody, StableWriteOperationKeys, type GenerationDetail, type GenerationSpec, type ProviderProfile } from "./api";
import { deleteLocalDraft, readLocalDraft, sha256, writeLocalDraft } from "./localDrafts";
import { startPolling } from "./polling";
import { clearDirtySurface, setDirtySurface } from "./unsavedChanges";
import "./styles/generation.css";
import { LongformStage } from "./LongformStage";
import { NovelRunReview } from "./NovelRunReview";
import { CastPreview } from "./CharacterScope";

import { GenerationSettings } from "./GenerationSettings";
import { GenerationProgress } from "./GenerationProgress";
import { GenerationCallLog } from "./GenerationCallLog";
import { ChiefPlanFields, ChiefPlanFiles, type Plan } from "./ChiefPlanEditor";
import { InputRecovery } from "./InputRecovery";
import { MemoryRecovery } from "./MemoryRecovery";
import { StepRecovery } from "./StepRecovery";
import { withCurrentTokenLimits } from "./generationTokenLimits";
import { StageContinuation } from "./StageContinuation";
import { GenerationJourney } from "./GenerationJourney";
import { CandidateReader, type CandidateReading } from "./CandidateReader";
import { JsonDetails } from "./JsonDetails";
import { generationJourney, type GenerationPane } from "./generationFlow";
import { generationFormDraft, initialGenerationSpec, initialNarrativeSelectionMode, previewSpec, type GenerationFormDraft, type GenerationSetup as Setup, type NarrativeSelectionMode } from "./generationDefaults";

type Summary = { id: string; direction: string; status: string; created_at: string };
type Artifact = { id: string; kind: string; sha256: string; payload: Record<string, unknown> };
type PlanDraft = { planText: string; authorNote: string; questionAnswers: Record<string, string>; deferredQuestions: string[]; baseSha: string };
type Preview = { preview_sha256: string; needs_genre_acknowledgement: boolean; candidate_sha256: string; factual_delta: unknown };
const labels: Record<string, string> = { draft: "待确认预算", queued: "准备下一步", running: "模型处理中", paused: "已暂停", awaiting_plan: "等待确认故事方案", needs_attention: "请阅读并处理", ready: "可以审阅采用", failed: "调用失败", outcome_uncertain: "结果未知，待核对", adopted: "已采用", archived: "历史记录", plan: "Chief 设计", write: "Writer 正文", review: "Chief 复核", realized: "已呈现（模型意见）", partial: "部分呈现", missing: "未呈现", unknown: "待判断" };
const text = (value: unknown) => typeof value === "string" ? value : "";
const pretty = (value: unknown) => JSON.stringify(value, null, 2);
function artifact(batch: GenerationDetail | null, kind: string): Artifact | undefined {
  return batch?.artifacts.find((a) => a.id === batch.state[`${kind}_id`]) as Artifact | undefined;
}

export function GenerationWorkspace({ projectId, onAdopted }: { projectId: string; onAdopted: () => Promise<void> }) {
  const base = `/api/projects/${projectId}/generation-batches`;
  const [setup, setSetup] = useState<Setup | null>(null);
  const [profiles, setProfiles] = useState<ProviderProfile[]>([]);
  const [history, setHistory] = useState<Summary[]>([]);
  const [spec, setSpec] = useState<GenerationSpec | null>(null);
  const [narrativeMode, setNarrativeMode] = useState<NarrativeSelectionMode>("random");
  const [batch, setBatch] = useState<GenerationDetail | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [body, setBody] = useState("");
  const [planText, setPlanText] = useState("");
  const [authorNote, setAuthorNote] = useState("");
  const [questionAnswers, setQuestionAnswers] = useState<Record<string, string>>({});
  const [deferredQuestions, setDeferredQuestions] = useState<string[]>([]);
  const [position, setPosition] = useState<Record<string, unknown>>({});
  const [changes, setChanges] = useState("{}");
  const [factsConfirmed, setFactsConfirmed] = useState(false);
  const [title, setTitle] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [acceptDeviation, setAcceptDeviation] = useState(false);
  const [confirmedPreview, setConfirmedPreview] = useState<string | null>(null);
  const confirmationBinding = batch ? `${batch.id}:${batch.preview_sha256}` : null;
  const costConfirmed = confirmationBinding !== null && confirmedPreview === confirmationBinding;
  const [unknownNote, setUnknownNote] = useState("");
  const [savedPlanDraft, setSavedPlanDraft] = useState<PlanDraft | null>(null);
  const [savedDraft, setSavedDraft] = useState<{ body: string; baseSha: string } | null>(null);
  const [selectedPane, setSelectedPane] = useState<GenerationPane | null>(null);
  const [reading, setReading] = useState<CandidateReading | null>(null);
  const settingsRef = useRef<HTMLDetailsElement>(null);
  const planEditorRef = useRef<HTMLDetailsElement>(null);
  const writes = useRef(new StableWriteOperationKeys());
  const savedHere = useRef<{ candidate?: string; plan?: string }>({});
  const alive = useRef(true);
  const selection = useRef(0);
  const candidate = artifact(batch, "candidate");
  const planArtifact = artifact(batch, "plan");
  const plan = planArtifact?.payload as Plan | undefined;
  const review = artifact(batch, "review")?.payload;
  const memory = artifact(batch, "memory");
  const longform = batch?.revision === "genre-led-longform-v1";
  const frozenCards = (batch?.snapshot.cards ?? []) as { id: string; name: string }[];
  const narrativeNames = (batch?.spec.narrative_card_ids ?? []).map((id) => frozenCards.find((card) => card.id === id)?.name ?? id).join("、");
  const novel = longform || batch?.revision === "genre-led-novel-run-v1";
  const questionItems = (artifact(batch, "questions")?.payload.items ?? []) as { question: string; status: string; scope: string }[];
  const active = batch?.status === "queued" || batch?.status === "running";
  const journey = batch ? generationJourney(batch) : null;
  const pane = selectedPane ?? journey?.pane ?? "plan";
  function openPane(next: GenerationPane, editPlan = false) {
    setSelectedPane(next);
    window.requestAnimationFrame(() => {
      if (editPlan && planEditorRef.current) planEditorRef.current.open = true;
      const target = editPlan ? planEditorRef.current : document.getElementById(`generation-pane-${next}`);
      target?.scrollIntoView?.({ block: "start" });
      target?.focus({ preventScroll: true });
    });
  }
  function openSettings() {
    if (!settingsRef.current) return;
    settingsRef.current.open = true;
    settingsRef.current.scrollIntoView?.({ block: "start" });
    settingsRef.current.querySelector("summary")?.focus({ preventScroll: true });
  }
  const planDirty = !!plan && (planText !== pretty(plan) || !!authorNote.trim() || Object.keys(questionAnswers).length > 0 || deferredQuestions.length > 0);
  const unitItems = (artifact(batch, "units")?.payload.items ?? []) as { memory_id?: string }[];
  const planEditingReady = batch?.plan_edit_revision === "author-plan-v1";
  const frozenCharacters = ((batch?.snapshot.context as { characters?: { id: string; name: string }[] } | undefined)?.characters ?? []).filter((c) => !unitItems.length || plan?.scenes.some((scene) => scene.character_ids.includes(c.id)));
  const planEditable = !!batch && ["awaiting_plan", "paused", "needs_attention"].includes(batch.status) && !unitItems.some((u) => !u.memory_id) && !batch.calls.some((c) => c.action === (longform ? `write:${unitItems.length + 1}` : "write"));
  const candidateDirty = !!candidate && body !== text(candidate.payload.body);
  function openReading(fromEditor = false) {
    if (!candidate || !batch) return;
    setReading({
      candidateId: candidate.id,
      body: fromEditor ? body : text(candidate.payload.body),
      units: artifact(batch, "units")?.payload.items,
      complete: candidate.payload.complete !== false,
      unsaved: fromEditor && candidateDirty,
      adopted: batch.status === "adopted",
    });
  }
  const dirtyKey = `generation:${projectId}:${batch?.id ?? "new"}`;

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void Promise.all([api<Setup>(base + "/setup", { signal: controller.signal }), api<ProviderProfile[]>("/api/provider-profiles", { signal: controller.signal }), api<Summary[]>(base, { signal: controller.signal })]).then(async ([value, availableProfiles, records]) => {
      if (controller.signal.aborted) return;
      setSetup(value); setProfiles(availableProfiles); setHistory(records);
      setPosition(value.narrative_position);
      const previous = records[0] ? await api<GenerationDetail>(`${base}/${records[0].id}`, { signal: controller.signal }) : null;
      if (controller.signal.aborted) return;
      const draft = await readLocalDraft<GenerationFormDraft>(`generation-form:${projectId}`).catch(() => null);
      if (controller.signal.aborted) return;
      setSpec(initialGenerationSpec(value, availableProfiles, previous?.spec, draft?.payload));
      setNarrativeMode(initialNarrativeSelectionMode(draft?.payload));
      if (previous) setBatch(previous);
    }).catch((e: unknown) => { if (!isAbortError(e)) setError(errorMessage(e)); });
    return () => { alive.current = false; controller.abort(); };
  }, [base, projectId]);

  useEffect(() => {
    if (!spec) return;
    const payload = generationFormDraft(spec, narrativeMode);
    const timer = window.setTimeout(() => { void sha256(pretty(payload)).then((hash) => writeLocalDraft({ schema_version: "local-draft-v1", draft_key: `generation-form:${projectId}`, project_id: projectId, surface: "generation", resource_id: projectId, base_version: setup?.version ?? null, base_sha256: null, payload, payload_sha256: hash, updated_at: new Date().toISOString() })).catch(() => setNotice("浏览器草稿存储不可用；请先建立预览保存创作设置。")); }, 400);
    return () => window.clearTimeout(timer);
  }, [spec, narrativeMode, projectId, setup?.version]);

  useEffect(() => {
    setBody(text(candidate?.payload.body)); setPreview(null); setFactsConfirmed(false); setAcceptDeviation(false); setSavedDraft(null);
    let current = true;
    if (candidate && candidate.id !== savedHere.current.candidate) void readLocalDraft<{ body: string; baseSha: string }>(dirtyKey).then((draft) => { if (current && draft && draft.payload.body !== text(candidate.payload.body)) setSavedDraft(draft.payload); }).catch(() => undefined);
    return () => { current = false; };
  }, [candidate?.id, dirtyKey]);
  useEffect(() => {
    setPlanText(plan ? pretty(plan) : ""); setAuthorNote(""); setQuestionAnswers({}); setDeferredQuestions([]); setSavedPlanDraft(null);
    let current = true;
    if (planArtifact && planArtifact.id !== savedHere.current.plan) void readLocalDraft<PlanDraft>(`${dirtyKey}:plan`).then((draft) => {
      if (current && draft && (draft.payload.planText !== pretty(plan) || draft.payload.authorNote.trim() || Object.keys(draft.payload.questionAnswers).length || draft.payload.deferredQuestions.length)) setSavedPlanDraft(draft.payload);
    }).catch(() => undefined);
    return () => { current = false; };
  }, [planArtifact?.id, dirtyKey]);
  useEffect(() => {
    setConfirmedPreview(null); setTitle(""); setChanges("{}"); setPreview(null); setSelectedPane(null); setReading(null);
    if (batch && settingsRef.current) settingsRef.current.open = false;
    const context = batch?.snapshot.context as { narrative_position?: Record<string, unknown> } | undefined;
    setPosition({ ...context?.narrative_position, recent_major_event: "", notes: "" });
  }, [batch?.id]);
  useEffect(() => {
    if (memory) { setPosition(memory.payload.position as Record<string, unknown>); setChanges(pretty(memory.payload.factual_changes)); }
    setFactsConfirmed(false); setPreview(null);
  }, [memory?.id]);
  useEffect(() => {
    const titles = artifact(batch, "title")?.payload.titles as string[] | undefined;
    if (titles?.length) setTitle((previous) => previous || titles[0]);
  }, [batch?.state.title_id]);

  useEffect(() => {
    if (!batch || !active) return;
    const id = batch.id;
    return startPolling({ intervalMs: 2500, poll: async (signal) => {
      const value = await api<GenerationDetail>(`${base}/${id}`, { signal });
      if (!signal.aborted) setBatch((current) => current?.id === id ? value : current);
    }, onError: (e) => setError(errorMessage(e)) });
  }, [batch?.id, active, base]);

  async function refresh() {
    if (!batch) return;
    const id = batch.id;
    const value = await api<GenerationDetail>(`${base}/${id}`);
    if (alive.current) setBatch((current) => current?.id === id ? value : current);
  }
  async function perform(work: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await work(); } catch (e) { if (alive.current) setError(errorMessage(e)); }
    finally { if (alive.current) setBusy(false); }
  }
  async function requestPreview(value: GenerationSpec) {
    return writes.current.request<GenerationDetail>(narrativeMode === "random" ? `${base}/random-preview` : base, {
      method: "POST", body: jsonBody({ ...value, narrative_card_ids: narrativeMode === "random" ? [] : value.narrative_card_ids ?? [] }),
    });
  }
  async function saveBody() {
    if (!batch || !candidate) return;
    const value = await writes.current.request<GenerationDetail>(`${base}/${batch.id}/candidate`, { method: "PUT", body: jsonBody({ body, expected_body_sha256: await sha256(text(candidate.payload.body)) }) });
    savedHere.current.candidate = artifact(value, "candidate")?.id;
    if (alive.current) { setBatch(value); clearDirtySurface(dirtyKey); setNotice("已保存为新候选，旧复核不再用于此稿。"); }
    await deleteLocalDraft(dirtyKey).catch(() => { if (alive.current) setNotice("正文已保存；浏览器旧草稿清理失败，请以已保存版本为准。"); });
  }
  async function savePlan() {
    if (!batch) return;
    const value = await writes.current.request<GenerationDetail>(`${base}/${batch.id}/plan`, { method: "PUT", body: jsonBody({ plan: JSON.parse(planText), expected_plan_sha256: planArtifact?.sha256 ?? null, author_note: authorNote, question_answers: questionAnswers, deferred_questions: deferredQuestions }) });
    savedHere.current.plan = artifact(value, "plan")?.id;
    if (alive.current) { setBatch(value); setSavedPlanDraft(null); clearDirtySurface(`${dirtyKey}:plan`); setNotice("已保存新的故事方案版本，Chief 原稿保留；尚未调用模型。"); }
    await deleteLocalDraft(`${dirtyKey}:plan`).catch(() => { if (alive.current) setNotice("方案已保存；浏览器旧草稿清理失败，请以已保存版本为准。"); });
  }
  useEffect(() => {
    const key = `${dirtyKey}:plan`;
    setDirtySurface(key, "故事方案与答复", planDirty && !active, { save: savePlan, discard: async () => { setPlanText(plan ? pretty(plan) : ""); setAuthorNote(""); setQuestionAnswers({}); setDeferredQuestions([]); await deleteLocalDraft(key); } });
    const timer = planDirty && !active && planArtifact ? window.setTimeout(() => {
      const payload: PlanDraft = { planText, authorNote, questionAnswers, deferredQuestions, baseSha: planArtifact.sha256 };
      void sha256(pretty(payload)).then((hash) => writeLocalDraft({ schema_version: "local-draft-v1", draft_key: key, project_id: projectId, surface: "generation", resource_id: batch!.id, base_version: setup?.version ?? null, base_sha256: planArtifact.sha256, payload, payload_sha256: hash, updated_at: new Date().toISOString() })).catch(() => setNotice("方案本地备份失败，请先保存方案或下载文件。"));
    }, 400) : null;
    return () => { if (timer) window.clearTimeout(timer); clearDirtySurface(key); };
  }, [dirtyKey, planText, authorNote, active, questionAnswers, deferredQuestions, planArtifact?.id]);
  useEffect(() => {
    setDirtySurface(dirtyKey, "候选正文", candidateDirty, { save: saveBody, discard: async () => { setBody(text(candidate?.payload.body)); await deleteLocalDraft(dirtyKey); } });
    const timer = candidateDirty ? window.setTimeout(() => { void sha256(body).then((hash) => writeLocalDraft({ schema_version: "local-draft-v1", draft_key: dirtyKey, project_id: projectId, surface: "generation", resource_id: batch!.id, base_version: setup?.version ?? null, base_sha256: candidate!.sha256, payload: { body, baseSha: candidate!.sha256 }, payload_sha256: hash, updated_at: new Date().toISOString() })).catch(() => setNotice("本地草稿备份失败，请保存候选正文。")); }, 400) : null;
    return () => { if (timer) window.clearTimeout(timer); clearDirtySurface(dirtyKey); };
  }, [dirtyKey, candidateDirty, body, candidate?.id]);

  const update = (patch: Partial<GenerationSpec>) => setSpec((current) => current && ({ ...current, ...patch }));
  async function selectBatch(id: string) {
    if (!id.trim()) return;
    if (candidateDirty || planDirty) throw new Error("请先保存或撤销正文和方案修改，再切换批次。");
    const ticket = ++selection.current;
    const value = await api<GenerationDetail>(`${base}/${id}`);
    if (alive.current && ticket === selection.current) setBatch(value);
  }
  const canEdit = batch && !active && !["outcome_uncertain", "adopted", "archived"].includes(batch.status);
  const configurationReady = setup?.configuration_revision === "author-intent-v1" && setup?.context_budget_revision === "world-bounded-v1" && setup?.output_budget_revision === "chief-output-v1" && setup?.automation_revision === "stage-auto-v1";
  const planRetry = batch?.plan_retry_preview as { chief_output_limit: number; auxiliary_output_limit: number; previous_output_limit: number; reason?: string } | null | undefined;

  if (!setup || !spec) return <section className="generation-workspace"><h2>阶段创作</h2><p role={error ? "alert" : "status"}>{error || "正在读取正式起点与作者资料…"}</p></section>;
  return <section className="generation-workspace">
    <header className="generation-header"><div><span className="eyebrow">NovelRun</span><h2>阶段创作</h2><p>作者意图引导故事，Chief 设计、Writer 创作。</p></div>{batch && <button disabled={busy || active} onClick={openSettings}>新建阶段</button>}</header>
    {error && <p role="alert" className="error-banner">{error}</p>}{notice && <p role="status">{notice}</p>}
    {history.length > 0 && <label>创作记录<select aria-label="创作记录" value={batch?.id ?? ""} disabled={busy || candidateDirty || planDirty} onChange={(e) => void perform(() => selectBatch(e.target.value))}><option value="" disabled>选择批次</option>{history.map((h) => <option key={h.id} value={h.id}>{h.direction} · {labels[batch?.id === h.id ? batch.status : h.status] ?? h.status}</option>)}{batch && !history.some((h) => h.id === batch.id) && <option value={batch.id}>当前预览</option>}</select></label>}
    {batch && journey && <>
      {candidate && text(candidate.payload.body).trim() && <section className="generation-reading-entry" aria-label="已写正文">
        <div><strong>已写正文</strong><p>{Array.from(text(candidate.payload.body)).length.toLocaleString()} 字符 · {candidate.payload.complete === false ? "已保存部分内容" : "已保存，可随时阅读"}</p></div>
        <button type="button" className="primary-button" onClick={() => openReading()}>阅读已写正文</button>
      </section>}
      {reading && <CandidateReader reading={reading} hasNewerVersion={reading.candidateId !== candidate?.id} onClose={() => setReading(null)} />}
      <section className={`generation-panel generation-current tone-${journey.tone}`} aria-label="当前阶段与下一步" id="generation-current" tabIndex={-1}>
        <div className="section-title"><span className="eyebrow">{journey.tone === "working" ? "系统自动处理中" : journey.tone === "done" ? "阶段记录" : "当前需要你的操作"}</span><button disabled={busy} onClick={() => void perform(refresh)}>刷新状态</button></div>
        <GenerationJourney journey={journey} />
        <GenerationProgress batch={batch} compact />
        <button type="button" onClick={() => openPane("calls")}>查看各角色 Prompt 与输出</button>
        <div className="generation-next"><strong>下一步</strong><p>{journey.next}</p></div>
        <div className="generation-actions">
        {planRetry && <div className="generation-blocked"><p>使用当前设置重新预览：输入 {spec.input_limit?.toLocaleString()} tokens、Chief 输出 {spec.chief_output_limit?.toLocaleString()}、Writer 输出 {spec.writer_output_limit?.toLocaleString()}、其他角色默认输出 {spec.auxiliary_output_limit?.toLocaleString()}，采用当前显示的题材并{narrativeMode === "random" ? "重新随机抽取两张叙事卡" : "使用当前手动选择的叙事卡"}，保留本次人物、作者要求及费用上限，重新计算费用。原失败记录保留。</p><button className="primary-button" disabled={busy || candidateDirty || !!authorNote.trim() || !!planText.trim() || !configurationReady} onClick={() => void perform(async () => {
          const retrySpec = withCurrentTokenLimits(batch.spec, spec);
          const value = await requestPreview(retrySpec);
          if (alive.current) { setBatch(value); setSpec(retrySpec); setHistory(await api<Summary[]>(base)); setNotice("已按当前创作设置建立新预览；请核对费用后授权，尚未调用模型。"); }
        })}>{planRetry.reason === "obsolete_genre_quota" ? "取消旧题材配额并建立新预览（不调用模型）" : "提高 Chief 输出并重新预览（不调用模型）"}</button></div>}
        {batch.spec.card_selection_policy === "separate-v1" && <div aria-label="本阶段叙事卡"><strong>{batch.snapshot.narrative_selection_policy === "random-two-v1" ? "本次随机叙事卡" : "本阶段叙事卡"}</strong><p>{narrativeNames || "无"}</p></div>}
        {(["draft", "paused", "awaiting_plan"].includes(batch.status)) && <div className="generation-cost-summary"><strong>本次费用上界 ¥{text(batch.snapshot.maximum_cost_cny)}</strong><span>Chief {batch.spec.chief_model} · Writer {batch.spec.writer_model}</span><span>最多 {Number(batch.snapshot.maximum_calls ?? 3)} 个预留调用；继续沿用原预算。</span></div>}
        {((batch.snapshot.blockers ?? []) as string[]).map((b) => <p role="alert" key={b}>{b}</p>)}
        {batch.status === "draft" && (batch.snapshot.blockers as unknown[])?.length > 0 && <div className="generation-blocked">
          <p>当前预览未通过本地预检，勾选费用确认也不能启动。可按当前设置的输入上限 {spec.input_limit?.toLocaleString()} tokens 重新检查分词配置并选择旧材料，完整题材卡和当前连续性保留。建立新预览会{narrativeMode === "random" ? "重新随机抽取两张叙事卡" : "使用当前手动选择的叙事卡"}。</p>
          <button className="primary-button" disabled={busy || candidateDirty || !configurationReady} onClick={() => void perform(async () => {
            const value = await requestPreview(withCurrentTokenLimits(batch.spec, spec));
            if (alive.current) { setBatch(value); setHistory(await api<Summary[]>(base)); setNotice("已建立新的检查预览，原记录保留；尚未授权或调用模型。"); }
          })}>重新检查并建立预览（不调用模型）</button>
        </div>}
        {["draft", "paused", "awaiting_plan"].includes(batch.status) && batch.next_action && !(longform && planArtifact && configurationReady && batch.status !== "draft") && <><label className="check"><input type="checkbox" checked={costConfirmed} onChange={(e) => setConfirmedPreview(e.target.checked ? confirmationBinding : null)} />确认本批模型、完整题材卡及选中故事资料的外发范围与费用上限</label><button className="primary-button" disabled={busy || !costConfirmed || (batch.snapshot.blockers as unknown[])?.length > 0} onClick={() => void perform(async () => { const receipt = await writes.current.request<{ id: string; status: string }>(`${base}/${batch.id}/authorize`, { method: "POST", body: jsonBody({ preview_sha256: batch.preview_sha256, confirmed: true }) }); if (alive.current) setBatch((current) => current?.id === receipt.id ? { ...current, status: receipt.status } : current); })}>{batch.status === "draft" ? (longform ? "授权并开始阶段创作" : "授权并开始一章创作") : "确认并继续剩余动作"}</button></>}
        {active && <button disabled={busy} onClick={() => void perform(async () => { await writes.current.request(`${base}/${batch.id}/pause`, { method: "POST", body: jsonBody({ confirmed: true }) }); if (alive.current) setNotice("已收到暂停请求，本次响应保存后暂停。"); await refresh(); })}>本次响应保存后暂停</button>}
        {batch.status === "outcome_uncertain" && !batch.step_recovery_available && <><p>该调用可能已计费。请先在供应商处核对；关闭未知状态不会发送请求，后续新批次需重新确认费用。</p><label>核对结论<textarea value={unknownNote} onChange={(e) => setUnknownNote(e.target.value)} /></label><button disabled={busy || !unknownNote.trim()} onClick={() => void perform(async () => { setBatch(await writes.current.request<GenerationDetail>(`${base}/${batch.id}/resolve-unknown`, { method: "POST", body: jsonBody({ confirmed: true, note: unknownNote }) })); })}>记录核对结论并关闭未知状态</button></>}
      <InputRecovery batch={batch} base={base} disabled={busy || candidateDirty || planDirty} onContinued={(value) => setBatch((current) => current?.id === value.id ? value : current)} />
      <MemoryRecovery batch={batch} base={base} disabled={busy || candidateDirty || planDirty} onContinued={(value) => setBatch((current) => current?.id === value.id ? value : current)} />
      <StepRecovery batch={batch} base={base} disabled={busy || candidateDirty || planDirty} onContinued={(value) => setBatch((current) => current?.id === value.id ? value : current)} />
      {longform && configurationReady && <StageContinuation batch={batch} base={base} disabled={busy || candidateDirty || planDirty} onContinued={(value) => setBatch((current) => current?.id === value.id ? value : current)} />}
        {journey.link && <button className="primary-button" onClick={() => openPane(journey.pane, journey.editPlan)}>{journey.link}</button>}
        {journey.tone === "review" && <button onClick={() => openPane("review")}>查看反馈与采用</button>}
        {(candidateDirty || planDirty) && <p className="generation-unsaved">有未保存修改，请到对应页面保存或撤销后再继续。</p>}
        </div>
      </section>
      <div className="generation-tabs" role="tablist" aria-label="本阶段资料" onKeyDown={(event) => {
        const order: GenerationPane[] = ["plan", "body", "review", "calls"];
        const focused = (event.target as HTMLElement).getAttribute("data-pane") as GenerationPane | null;
        const index = order.indexOf(focused ?? pane);
        const next = event.key === "ArrowRight" ? order[(index + 1) % order.length] : event.key === "ArrowLeft" ? order[(index + order.length - 1) % order.length] : event.key === "Home" ? order[0] : event.key === "End" ? order.at(-1) : undefined;
        if (next) { event.preventDefault(); setSelectedPane(next); document.getElementById(`generation-tab-${next}`)?.focus(); }
      }}>
        {([["plan", "故事方案"], ["body", "候选正文"], ["review", "审核与采用"], ["calls", "调用详情"]] as const).map(([key, label]) => <button type="button" role="tab" data-pane={key} id={`generation-tab-${key}`} aria-controls={`generation-pane-${key}`} aria-selected={pane === key} tabIndex={pane === key ? 0 : -1} key={key} onClick={() => setSelectedPane(key)}>{label}{key === "body" && candidateDirty ? " · 未保存" : ""}</button>)}
      </div>
      <div className="generation-tab-panel" id="generation-pane-plan" role="tabpanel" aria-labelledby="generation-tab-plan" hidden={pane !== "plan"} tabIndex={-1}>
      {!plan && <p className="generation-empty">{active ? "Chief 完成设计后，故事方案会出现在这里。" : "尚无已保存的故事方案，请先完成上方当前操作。"}</p>}
      {artifact(batch, "chief_comparison") && <section className="generation-panel"><h3>最近一次 Chief 剧情调整</h3><p>{text(artifact(batch, "chief_comparison")!.payload.assessment)}</p><details><summary>对照证据与修订记录</summary><pre>{pretty(artifact(batch, "chief_comparison")!.payload)}</pre></details></section>}
      {plan && <section className="generation-panel"><h3>Chief 的故事方案</h3><p>{plan.chapter_goal}</p><p>衔接：{plan.bridge}</p><ol>{plan.scenes.map((s, i) => <li key={i}><strong>{s.event}</strong><p>{s.choice_and_response}</p><p>后果：{s.consequence}</p>{s.focus_percent !== undefined && <small>主导 {s.focus_percent}% · 过渡 {s.transition_percent}% · 其他 {s.other_percent}%（占{longform ? "全阶段" : "整章"}）</small>}</li>)}</ol><p>主要转折：{plan.major_turn}</p>{plan.world_context !== undefined ? <p>世界观与背景依据：{plan.world_context || "按正式设定自然展开"}</p> : <p>题材如何改变结果：{plan.genre_causal_role}</p>}{(novel ? questionItems.filter((q) => q.status === "pending").map((q) => q.question) : plan.questions ?? []).map((q) => <p role="alert" key={q}>待决：{q}</p>)}</section>}
      {plan && <ChiefPlanFiles key={batch.id} batch={batch} />}
      {planEditable && plan && <details ref={planEditorRef} tabIndex={-1}><summary>编辑 Chief 方案与故事方向</summary><p>{["background-v1", "guided-v1"].includes(batch.spec.writing_policy) ? "只规划事件、人物选择和后果，不填写题材百分比。" : "历史合同：各单元篇幅合计 100%，主导题材至少 70%。"}已写单元保留原方案。</p>
        {savedPlanDraft && <p>发现方案浏览器草稿{savedPlanDraft.baseSha !== planArtifact?.sha256 ? "，已保存版本发生变化，请核对后恢复" : ""}。<button disabled={busy} onClick={() => { setPlanText(savedPlanDraft.planText); setAuthorNote(savedPlanDraft.authorNote); setQuestionAnswers(savedPlanDraft.questionAnswers); setDeferredQuestions(savedPlanDraft.deferredQuestions); setSavedPlanDraft(null); }}>恢复方案草稿</button><button disabled={busy} onClick={() => { void deleteLocalDraft(`${dirtyKey}:plan`); setSavedPlanDraft(null); }}>丢弃方案草稿</button></p>}
        <ChiefPlanFields value={planText} onChange={setPlanText} writtenUnits={unitItems.length} disabled={busy || !planEditingReady} characters={frozenCharacters} />
        {!planEditingReady && <p role="status">完整方案编辑与输入容量恢复需加载新版后端后使用；已保存方案可查看与下载。</p>}{novel && questionItems.filter((q) => q.status === "pending").map((q) => <div key={q.question}><label>{q.scope === "later" ? "后续问题" : "当前问题"}：{q.question}<textarea value={questionAnswers[q.question] ?? ""} disabled={busy || !planEditingReady || deferredQuestions.includes(q.question)} onChange={(e) => setQuestionAnswers({ ...questionAnswers, [q.question]: e.target.value })} /></label><label className="check"><input type="checkbox" disabled={busy || !planEditingReady} checked={deferredQuestions.includes(q.question)} onChange={(e) => { setDeferredQuestions(e.target.checked ? [...deferredQuestions, q.question] : deferredQuestions.filter((item) => item !== q.question)); const answers = { ...questionAnswers }; delete answers[q.question]; setQuestionAnswers(answers); }} />明确延期；在方案中避开依赖它的发展</label></div>)}<label>答复与修改说明<textarea disabled={busy || !planEditingReady} value={authorNote} onChange={(e) => setAuthorNote(e.target.value)} /></label><div className="button-row"><button disabled={busy || !planEditingReady || !authorNote.trim()} onClick={() => void perform(savePlan)}>保存方案（不调用模型）</button><button disabled={busy || !planDirty} onClick={() => { setPlanText(pretty(plan)); setAuthorNote(""); setQuestionAnswers({}); setDeferredQuestions([]); void deleteLocalDraft(`${dirtyKey}:plan`); }}>撤销方案修改</button></div></details>}
      {artifact(batch, "writer_issue") && <section className="generation-panel"><h3>Writer 报告冲突</h3><pre>{pretty(artifact(batch, "writer_issue")!.payload)}</pre></section>}
      </div>
      <div className="generation-tab-panel" id="generation-pane-body" role="tabpanel" aria-labelledby="generation-tab-body" hidden={pane !== "body"} tabIndex={-1}>
      {!candidate && <p className="generation-empty">Writer 尚未产出正文。完成上方操作后，正文会自动显示在这里。</p>}
      {candidate && <section className="generation-panel"><h3>候选正文{candidate.payload.complete === false ? "（供应商截断，未完成）" : ""}</h3>
        <button type="button" disabled={!body.trim()} onClick={() => openReading(true)}>阅读模式</button>
        {savedDraft && <p>发现浏览器草稿{savedDraft.baseSha !== candidate.sha256 ? "，其原稿已变化，请核对" : ""}。<button onClick={() => { setBody(savedDraft.body); setSavedDraft(null); }}>恢复到编辑框</button><button onClick={() => { void deleteLocalDraft(dirtyKey); setSavedDraft(null); }}>丢弃本地草稿</button></p>}
        <label>正文<textarea className="generation-manuscript" aria-label="正文" readOnly={!canEdit} value={body} onChange={(e) => { setBody(e.target.value); setPreview(null); }} /></label>
        <div className="button-row"><button disabled={busy || !candidateDirty || !canEdit} onClick={() => void perform(saveBody)}>保存候选修改</button><button disabled={!candidateDirty} onClick={() => setBody(text(candidate.payload.body))}>撤销未保存修改</button><button onClick={() => { const url = URL.createObjectURL(new Blob([body], { type: "text/plain;charset=utf-8" })); const link = document.createElement("a"); link.href = url; link.download = "候选正文.txt"; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); }}>下载正文</button></div>
      </section>}
      {candidate && !active && <button className="primary-button" onClick={() => openPane("review")}>下一步：查看反馈与采用</button>}
      </div>
      <div className="generation-tab-panel" id="generation-pane-review" role="tabpanel" aria-labelledby="generation-tab-review" hidden={pane !== "review"} tabIndex={-1}>
      <p className="generation-review-intro">{active ? "内部流程仍在进行。这里展示已保存的阶段材料，完成后再核对和采用。" : "先读正文与独立反馈，再核对逐章事实，最后预览并确认采用。采用前，正文仍是候选稿。"}</p>
      {novel && <NovelRunReview batch={batch} base={base} onRefresh={refresh} disabled={busy || !!active || candidateDirty} />}
      {review && <section className="generation-panel"><h3>{novel ? "独立阅读：" : "正文复核："}{labels[text(review.outcome)] ?? "待判断"}</h3><p>{text(review.explanation)}</p><p>{review.ratio ? `主导题材篇幅区间约 ${Math.floor(Number((review.ratio as Record<string, unknown>).lower) * 100)}%–${Math.ceil(Number((review.ratio as Record<string, unknown>).upper) * 100)}%（依据模型分类，需人工阅读核对）` : novel ? "冷读不接作者目标；请对照实际阅读感受判断题材效果。" : "题材占比未知"}</p><p>{text(review.revision_advice)}</p><details><summary>观察证据与连续性建议</summary><pre>{pretty(review)}</pre></details></section>}
      {longform && <LongformStage batch={batch} base={base} disabled={busy || !!active || candidateDirty} onAdopted={async () => { await refresh(); await onAdopted(); const latest = await api<Setup>(base + "/setup"); setSetup(latest); update({ base_version_id: latest.base_version_id }); }} />}
      {candidate && canEdit && !longform && <section className="generation-panel"><h3>采用前核对</h3><p>只将本章实际发生的事写入正式资料；题材目标与模型建议不自动成为事实。</p><label>章节标题<input value={title} onChange={(e) => { setTitle(e.target.value); setPreview(null); }} /></label><div className="generation-grid">{[["current_location", "当前地点"], ["current_time", "当前时间"], ["recent_major_event", "本章实际完成的事件"], ["current_conflict", "当前冲突"], ["in_progress", "仍在进行的事项"], ["notes", "实际关系后果与其他接续事实"]].map(([key, label]) => <label key={key}>{label}<textarea value={text(position[key])} onChange={(e) => { setPosition({ ...position, [key]: e.target.value }); setPreview(null); setFactsConfirmed(false); }} /></label>)}</div>
        <details><summary>资料变化详情与高级修正</summary><p>新版由 Memory 提出变化，下面显示将采用的完整内容；可纠正错误项。没有提取到不代表没有发生。</p><textarea className="code-input" value={changes} onChange={(e) => { setChanges(e.target.value); setPreview(null); setFactsConfirmed(false); }} /></details>
        {novel && <label className="check"><input type="checkbox" checked={factsConfirmed} onChange={(e) => { setFactsConfirmed(e.target.checked); setPreview(null); }} />我已核对本章实际事实、当前现场和资料变化；缺失或错误项已补充</label>}
        <button disabled={busy || candidateDirty || (!!novel && !factsConfirmed)} onClick={() => void perform(async () => { setPreview(await api<Preview>(`${base}/${batch.id}/adoption-preview`, { method: "POST", body: jsonBody({ title, narrative_position: position, factual_changes: JSON.parse(changes), facts_confirmed: factsConfirmed }) })); })}>预览正式采用</button>
        {preview && <><p>将创建一个新正式章节和版本。候选校验：{preview.candidate_sha256.slice(0, 12)}</p><details><summary>将写入的资料变更</summary><pre>{pretty(preview.factual_delta)}</pre></details>{preview.needs_genre_acknowledgement && <label className="check"><input type="checkbox" checked={acceptDeviation} onChange={(e) => setAcceptDeviation(e.target.checked)} />我已读稿，接受题材偏离或复核未知，保留当前写法</label>}<button disabled={busy || candidateDirty || (preview.needs_genre_acknowledgement && !acceptDeviation)} onClick={() => void perform(async () => { await writes.current.request(`${base}/${batch.id}/adopt`, { method: "POST", body: jsonBody({ confirmed: true, preview_sha256: preview.preview_sha256, title, narrative_position: position, factual_changes: JSON.parse(changes), facts_confirmed: factsConfirmed, accept_genre_deviation: acceptDeviation }) }); await refresh(); await onAdopted(); const latest = await api<Setup>(base + "/setup"); setSetup(latest); update({ base_version_id: latest.base_version_id }); setPreview(null); setNotice("已创建正式章节和新版本。"); })}>确认采用并创建正式版本</button></>}
      </section>}
      </div>
      <div className="generation-tab-panel" id="generation-pane-calls" role="tabpanel" aria-labelledby="generation-tab-calls" hidden={pane !== "calls"} tabIndex={-1}>
      <GenerationCallLog key={`${base}/${batch.id}`} base={base} batch={batch} busy={busy} onRevalidate={(callId) => perform(async () => { await api(`${base}/${batch.id}/calls/${callId}/revalidate`, { method: "POST", body: jsonBody({ confirmed: true }) }); await refresh(); })} />
      </div>
      <details className="generation-budget"><summary>本次设置、模型与预算明细</summary><p>方向：{batch.spec.direction}</p><p>{batch.spec.card_selection_policy === "separate-v1" ? "主题材" : "原阶段重点卡"}：{(setup.available_cards ?? setup.style.matched_cards).find((c) => c.id === batch.spec.focus_card_id)?.name ?? batch.spec.focus_card_id}</p><p>{batch.spec.card_selection_policy === "separate-v1" ? "副题材" : "原阶段副卡"}：{batch.spec.supporting_card_id ? (setup.available_cards ?? setup.style.matched_cards).find((c) => c.id === batch.spec.supporting_card_id)?.name ?? batch.spec.supporting_card_id : "无"}</p>{batch.spec.card_selection_policy === "separate-v1" && <p>本阶段叙事卡：{narrativeNames || "无"}</p>}
        <p>常规动作预算最多 {Number(batch.snapshot.normal_calls ?? 3)} 次调用，最多 {Number(batch.snapshot.maximum_calls ?? 3)} 个预留槽位，费用上界 ¥{text(batch.snapshot.maximum_cost_cny)}；设计输入计数/上界 {String(batch.snapshot.plan_input_tokens ?? "不可用")} tokens。</p>
        <p>原冻结预览 Chief 输出上限 {(batch.spec.chief_output_limit ?? 6000).toLocaleString()} tokens（包含推理），Writer 输出上限 {(batch.spec.writer_output_limit ?? 12000).toLocaleString()} tokens；其他角色默认输出上限 {(batch.spec.auxiliary_output_limit ?? batch.spec.chief_output_limit ?? 6000).toLocaleString()} tokens。</p>
        {artifact(batch, "memory_output_authorization") && <p>已追加恢复授权：输入 {String(artifact(batch, "memory_output_authorization")!.payload.input_limit)}，{artifact(batch, "memory_output_authorization")!.payload.all_roles ? "所有剩余角色" : "剩余 Memory"}输出 {String(artifact(batch, "memory_output_authorization")!.payload.output_limit)} tokens；阶段总预算 ¥{String(artifact(batch, "memory_output_authorization")!.payload.max_cost_cny)}。</p>}
        <p>冻结模型配置：{batch.spec.profile_id} · Chief {batch.spec.chief_model} · Writer {batch.spec.writer_model}。{batch.spec.relationship_scope === "genre-led" && "人物、视角和关系发展以作者描述与人物处境为依据。"}</p>
        <CastPreview batch={batch} />
        <JsonDetails title="本次完整输入来源与预算" value={batch.snapshot} />
      </details>
    </>}
    <details ref={settingsRef} open={!batch} className="generation-new-settings"><summary>新阶段设置 · 正式起点 v{setup.version}</summary>
      <p>这里用于建立新的创作预览，不会修改当前阶段。先选题材、叙事卡选择方式与单元上限，再核对预览中的卡片和费用。</p>
      <GenerationSettings spec={spec} setup={setup} profiles={profiles} history={history} update={update} narrativeMode={narrativeMode} setNarrativeMode={setNarrativeMode} />
      {!configurationReady && <p role="alert">简化创作设置需要加载新版后端；已有批次仍可查看和审核。</p>}
      <button className="primary-button" disabled={busy || candidateDirty || planDirty || active || !configurationReady || !spec.profile_id || !spec.chief_model || !spec.writer_model || !spec.focus_card_id} onClick={() => void perform(async () => { const value = await requestPreview(previewSpec(spec)); if (alive.current) { setBatch(value); setHistory(await api<Summary[]>(base)); } })}>建立新预览（不调用模型）</button>
    </details>
  </section>;
}
