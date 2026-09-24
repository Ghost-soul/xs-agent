import { useEffect, useMemo, useState } from "react";

import { api, commandHeaders, jsonBody } from "./api";

type Decision = "candidate" | "approved" | "excluded" | "false_positive";
type Sample = {
  sample_id: string; category: string; chapter_ordinal: number; chapter_title: string;
  start_offset: number; end_offset: number; text: string | null; source_status: string;
  decision: Decision; approved_dimensions: string[]; prohibited_transfer: string[]; author_note: string;
};
type Manifest = {
  manifest_id: string; source_name: string; source_path: string; source_sha256: string;
  byte_size: number; encoding: string; parser_version: string; source_status: string;
  provider_excerpt_allowed: boolean;
  parse_report: { total_chapters: number; retained_chapters: number; excluded_chapters: number; exclusion_reasons: Record<string, number>; candidate_counts: Record<string, number>; provider_calls: number };
};
type ContractItem = { dimension: string; rule: string; evidence_sample_ids?: string[] };
type Profile = {
  profile_id: string; manifest_id: string; version: number; status: "draft" | "active" | "superseded";
  structural_ranges: Record<string, unknown>; positive_contract: ContractItem[];
  negative_transfer_rules: string[]; provenance: { sample_count?: number; coverage?: Record<string, number> };
  blind_test: Record<string, unknown>;
};
type Workspace = {
  manifest: Manifest | null; samples: Sample[]; profiles: Profile[];
  approved_counts?: Record<string, number>;
  sample_quotas?: Record<string, number>; style_dimensions?: string[];
};

const categoryLabels: Record<string, string> = {
  daily_dialogue: "日常对白", conflict_dialogue: "冲突对白", action: "动作 / 战斗",
  psychology_information: "心理与信息释放", opening: "场景开头", ending: "章末",
  full_chapter: "完整章节",
};
const dimensionLabels: Record<string, string> = {
  rhythm: "节奏", dialogue: "对白", humor: "幽默", narrator_stance: "叙述姿态",
  information_release: "信息释放", ending: "章末",
};
const defaultDimensions: Record<string, string[]> = {
  daily_dialogue: ["dialogue", "humor"], conflict_dialogue: ["dialogue", "rhythm"],
  action: ["rhythm"], psychology_information: ["information_release", "narrator_stance"],
  opening: ["information_release", "narrator_stance"], ending: ["ending", "rhythm"],
  full_chapter: ["rhythm", "narrator_stance", "information_release"],
};
const blindTestLabels: Record<string, string> = {
  more_natural: "匿名对比中候选稿读起来更自然",
  closer_rhythm: "长短句与段落节奏更接近目标",
  dialogue_interaction: "对白像人物交锋，而非轮流传递信息",
  no_content_transfer: "没有口癖堆叠、原句、人物或情节迁移",
  keeps_project_voice: "结果仍然属于当前项目的人物与声音",
};

export function ReferenceStylePanel({ projectId }: { projectId: string }) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [sourcePath, setSourcePath] = useState("");
  const [allowLocal, setAllowLocal] = useState(false);
  const [allowProvider, setAllowProvider] = useState(false);
  const [category, setCategory] = useState("daily_dialogue");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load(signal?: AbortSignal) {
    setWorkspace(await api<Workspace>(`/api/projects/${projectId}/reference-style?category=${category}`, { signal }));
  }
  useEffect(() => {
    const controller = new AbortController();
    setWorkspace(null); setError(null);
    void load(controller.signal).catch((value: unknown) => {
      if (!isAbortError(value)) setError(errorText(value));
    });
    return () => controller.abort();
  }, [projectId, category]); // eslint-disable-line react-hooks/exhaustive-deps
  const samples = useMemo(() => (workspace?.samples ?? []).filter((item) => item.category === category), [workspace, category]);
  const approvedCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    Object.assign(counts, workspace?.approved_counts ?? {});
    if (!workspace?.approved_counts) for (const item of workspace?.samples ?? []) if (item.decision === "approved") counts[item.category] = (counts[item.category] ?? 0) + 1;
    return counts;
  }, [workspace]);
  const active = workspace?.profiles.find((item) => item.status === "active") ?? null;

  async function command(action: () => Promise<void>, success: string) {
    setBusy(true); setError(null); setMessage(null);
    try { await action(); setMessage(success); await load(); }
    catch (value) { setError(errorText(value)); }
    finally { setBusy(false); }
  }
  async function register() {
    await command(async () => {
      await api(`/api/projects/${projectId}/reference-style/corpora`, {
        method: "POST", headers: commandHeaders(), body: jsonBody({
          source_path: sourcePath, local_analysis_allowed: allowLocal,
          provider_excerpt_allowed: allowProvider, confirmed: true,
        }),
      });
    }, "本地登记、清洗和分层候选已生成；原文没有复制进数据库或发送到 Provider。");
  }
  async function review(sample: Sample, decision: Decision, dimensions = sample.approved_dimensions) {
    await command(async () => {
      await api(`/api/projects/${projectId}/reference-style/samples/${sample.sample_id}`, {
        method: "PUT", headers: commandHeaders(), body: jsonBody({
          decision, approved_dimensions: decision === "approved" ? dimensions : [],
          prohibited_transfer: sample.prohibited_transfer, author_note: sample.author_note, confirmed: true,
        }),
      });
    }, decision === "approved" ? "该样本及所选维度已明确认可。" : "样本判断已保存。");
  }
  function editSample(sampleId: string, update: Partial<Sample>) {
    setWorkspace((current) => current ? { ...current, samples: current.samples.map((item) => item.sample_id === sampleId ? { ...item, ...update } : item) } : current);
  }

  if (!workspace) return <section className="reference-style-panel"><h3>参考文风提取</h3>{error ? <div className="error-banner">{error}</div> : <p>正在载入本地语料状态…</p>}</section>;
  return <section className="reference-style-panel">
    <div className="section-title"><div><span className="eyebrow">S0–S6 本地流程</span><h3>参考文风档案</h3><p>只提取抽象节奏、对白组织和叙述姿态。源正文按偏移即时读取，不进入正式 StoryState、Prompt 或普通日志。</p></div>{active && <span className="reference-active">已激活 v{active.version}</span>}</div>
    {error && <div className="error-banner">{error}</div>}{message && <div className="success-banner">{message}</div>}
    {!workspace.manifest ? <div className="reference-register">
      <label>本地源文件<input placeholder="选择当前项目要使用的参考正文路径" value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} /></label>
      <label className="authorization-check"><input type="checkbox" checked={allowLocal} onChange={(event) => setAllowLocal(event.target.checked)} />我允许系统只在本机读取、清洗和分析该文件</label>
      <label className="authorization-check"><input type="checkbox" checked={allowProvider} onChange={(event) => setAllowProvider(event.target.checked)} />另行允许未来把我选中的短样本发给 Provider（当前不会发送）</label>
      <button className="primary-button" disabled={busy || !allowLocal || !sourcePath.trim()} onClick={() => void register()}>{busy ? "正在本地解析" : "登记并生成候选"}</button>
    </div> : <>
      <ManifestSummary manifest={workspace.manifest} />
      <div className="reference-category-tabs">{Object.keys(categoryLabels).map((key) => <button className={category === key ? "active" : ""} key={key} onClick={() => setCategory(key)}>{categoryLabels[key]} <small>{approvedCounts[key] ?? 0}/{workspace.sample_quotas?.[key] ?? 0}</small></button>)}</div>
      <div className="reference-samples">{samples.map((sample) => <article className={`reference-sample ${sample.decision}`} key={sample.sample_id}>
        <header><div><strong>{sample.chapter_title}</strong><span>字符偏移 {sample.start_offset}–{sample.end_offset}</span></div><span>{decisionLabel(sample.decision)}</span></header>
        {sample.text ? <pre>{sample.text}</pre> : <div className="error-banner">源文件已变化或缺失，拒绝显示旧偏移。</div>}
        <div className="dimension-grid">{Object.entries(dimensionLabels).map(([key, label]) => <label key={key}><input type="checkbox" checked={sample.approved_dimensions.includes(key)} onChange={(event) => editSample(sample.sample_id, { approved_dimensions: event.target.checked ? [...sample.approved_dimensions, key] : sample.approved_dimensions.filter((item) => item !== key) })} />{label}</label>)}</div>
        <label>禁止继承项（每行一条）<textarea value={sample.prohibited_transfer.join("\n")} onChange={(event) => editSample(sample.sample_id, { prohibited_transfer: lines(event.target.value) })} /></label>
        <label>作者备注<input value={sample.author_note} onChange={(event) => editSample(sample.sample_id, { author_note: event.target.value })} /></label>
        <div className="sample-actions"><button disabled={busy || !sample.text} onClick={() => void review(sample, "approved", sample.approved_dimensions.length ? sample.approved_dimensions : defaultDimensions[sample.category])}>认可所选维度</button><button disabled={busy} onClick={() => void review(sample, "false_positive")}>易误杀</button><button disabled={busy} onClick={() => void review(sample, "excluded")}>排除</button></div>
      </article>)}</div>
      <ProfileStage projectId={projectId} manifest={workspace.manifest} profiles={workspace.profiles} coverage={approvedCounts} busy={busy} command={command} />
    </>}
  </section>;
}

function ManifestSummary({ manifest }: { manifest: Manifest }) {
  const report = manifest.parse_report;
  return <div className="manifest-summary"><div><span>源文件</span><strong>{manifest.source_name}</strong><small>{manifest.encoding} · {(manifest.byte_size / 1024 / 1024).toFixed(2)} MB</small></div><div><span>指纹</span><code>{manifest.source_sha256.slice(0, 16)}…</code><small>{manifest.source_status === "verified" ? "当前文件已核验" : "文件已变化或缺失"}</small></div><div><span>清洗结果</span><strong>{report.retained_chapters} / {report.total_chapters} 章保留</strong><small>{report.excluded_chapters} 章排除 · Provider 调用 {report.provider_calls}</small></div><div><span>外发边界</span><strong>{manifest.provider_excerpt_allowed ? "精选短样本可另行确认" : "完全禁止"}</strong><small>登记动作本身永不外发</small></div></div>;
}

function ProfileStage({ projectId, manifest, profiles, coverage, busy, command }: { projectId: string; manifest: Manifest; profiles: Profile[]; coverage: Record<string, number>; busy: boolean; command: (action: () => Promise<void>, success: string) => Promise<void> }) {
  const draft = profiles.find((item) => item.status === "draft") ?? null;
  const [contract, setContract] = useState<ContractItem[]>(draft?.positive_contract ?? []);
  const [negative, setNegative] = useState<string[]>(draft?.negative_transfer_rules ?? []);
  const [blind, setBlind] = useState<Record<string, boolean>>({});
  useEffect(() => { setContract(draft?.positive_contract ?? []); setNegative(draft?.negative_transfer_rules ?? []); }, [draft?.profile_id]);
  const complete = Object.keys(categoryLabels).every((key) => (coverage[key] ?? 0) > 0);
  async function createDraft() { await command(async () => { await api(`/api/projects/${projectId}/reference-style/profiles`, { method: "POST", headers: commandHeaders(), body: jsonBody({ manifest_id: manifest.manifest_id, confirmed: true }) }); }, "已从作者认可样本生成可编辑档案草稿。"); }
  async function saveDraft() { if (!draft) return; await command(async () => { await api(`/api/projects/${projectId}/reference-style/profiles/${draft.profile_id}`, { method: "PUT", headers: commandHeaders(), body: jsonBody({ positive_contract: contract, negative_transfer_rules: negative, confirmed: true }) }); }, "文风档案草稿已另存于当前版本；正式激活状态未改变。"); }
  async function activate() { if (!draft) return; await command(async () => { await api(`/api/projects/${projectId}/reference-style/profiles/${draft.profile_id}/activate`, { method: "POST", headers: commandHeaders(), body: jsonBody({ blind_test: blind, confirmed: true }) }); }, "已保存为当前参考文风档案，供作者查阅；小说生成方式等待重构。"); }
  return <div className="reference-profile-stage"><div className="section-title"><div><h3>档案草稿与盲测激活</h3><p>七类样本各至少认可一条后才能生成。激活前必须独立完成匿名对比；系统不会自动调用模型生成盲测稿。</p></div>{!draft && <button className="primary-button" disabled={busy || !complete} onClick={() => void createDraft()}>生成版本化草稿</button>}</div>
    {!complete && <div className="handoff-note">尚缺：{Object.keys(categoryLabels).filter((key) => !coverage[key]).map((key) => categoryLabels[key]).join("、")}</div>}
    {draft && <><div className="profile-metrics"><strong>ReferenceStyleProfile v{draft.version} · 草稿</strong><span>{draft.provenance.sample_count} 条认可样本</span><code>结构指纹不含原文</code></div>
      <div className="contract-editor"><h4>正向文风合同</h4>{contract.map((item, index) => <div key={index}><select value={item.dimension} onChange={(event) => setContract((items) => items.map((entry, current) => current === index ? { ...entry, dimension: event.target.value } : entry))}>{Object.entries(dimensionLabels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select><textarea value={item.rule} onChange={(event) => setContract((items) => items.map((entry, current) => current === index ? { ...entry, rule: event.target.value } : entry))} /></div>)}</div>
      <label>禁止迁移规则（每行一条）<textarea value={negative.join("\n")} onChange={(event) => setNegative(lines(event.target.value))} /></label>
      <button disabled={busy} onClick={() => void saveDraft()}>保存草稿修改</button>
      <div className="blind-test"><h4>作者匿名对比确认</h4>{Object.entries(blindTestLabels).map(([key, label]) => <label key={key}><input type="checkbox" checked={blind[key] ?? false} onChange={(event) => setBlind((value) => ({ ...value, [key]: event.target.checked }))} />{label}</label>)}<button className="primary-button" disabled={busy || !Object.keys(blindTestLabels).every((key) => blind[key])} onClick={() => void activate()}>明确激活 v{draft.version}</button></div>
    </>}
  </div>;
}

function lines(value: string) { return value.split("\n").map((item) => item.trim()).filter(Boolean); }
function isAbortError(value: unknown) { return value instanceof DOMException && value.name === "AbortError"; }
function decisionLabel(value: Decision) { return value === "approved" ? "已认可" : value === "excluded" ? "已排除" : value === "false_positive" ? "易误杀" : "待选择"; }
function errorText(value: unknown) { return value instanceof Error ? value.message : "操作失败"; }
