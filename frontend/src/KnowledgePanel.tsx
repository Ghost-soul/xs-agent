import { useEffect, useRef, useState } from "react";
import { api, errorMessage, isAbortError, jsonBody } from "./api";

export type KnowledgeHit = {
  id: string; kind: string; text: string; ordinal?: number | null;
  start: number; end: number; source_id: string;
};
export type ContextSelection = {
  policy: string; material_count: number; counting_method: string; soft_target: number;
  source_count?: number; selected_count?: number; omitted_count?: number;
  required_above_target?: boolean;
  protected_continuity?: { recent_summaries: number; history_paragraphs: number };
};

export function SavedContextSelection({ selection }: { selection?: ContextSelection }) {
  if (!selection || !["role-key-v3", "chief-focus-v4"].includes(selection.policy)) return null;
  return <details><summary>本次背景资料选取</summary>
    {typeof selection.selected_count === "number" && <p>从 {selection.source_count} 条故事资料中选入 {selection.selected_count} 条，省略 {selection.omitted_count} 条。</p>}
    <p>辅助资料用量：{selection.material_count.toLocaleString()} {selection.counting_method === "local-tokenizer" ? "tokens（本地分词估算）" : "UTF-8 字节（保守上界，非实际 tokens）"}。完整待处理正文、卡文与输出合同另计。</p>
    {selection.required_above_target && <p>必要资料超过精简目标，已完整保留；最终请求仍受授权容量约束。</p>}
    {selection.protected_continuity && <p>优先保留 {selection.protected_continuity.recent_summaries} 份近期摘要、{selection.protected_continuity.history_paragraphs} 段相关历史原文，以及可用的上一章结尾。</p>}
    <p>省略仅表示本次未选入，原始资料继续保留。具体内容见任务 Prompt。</p>
  </details>;
}
type KnowledgeStatus = { status: string; mode: string; chunk_count: number };
type SearchResult = { mode: string; hits: KnowledgeHit[]; version_id: string };
const labels: Record<string, string> = {
  chapter: "正文", characters: "人物", world_lore: "世界资料", world_rules: "世界规则",
  relationships: "人物关系", beliefs: "人物认知", events: "事件", places: "地点",
  timeline_constraints: "时间约束", foreshadowings: "伏笔", reader_promises: "故事承诺",
};

export function KnowledgeHits({ hits }: { hits: KnowledgeHit[] }) {
  return <div className="knowledge-hits">{hits.map((hit) => <article key={hit.id}>
    <strong>{labels[hit.kind] ?? "故事资料"}{hit.ordinal ? ` · 原章节 ${hit.ordinal}` : ""}</strong>
    <small> · 原文位置 {hit.start + 1}–{hit.end}</small>
    <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{hit.text}</p>
  </article>)}</div>;
}

export function KnowledgePanel({ projectId, version }: { projectId: string; version: number }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<KnowledgeStatus | null>(null);
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const base = `/api/projects/${projectId}/knowledge`;
  const context = `${projectId}:${version}`;
  const currentContext = useRef(context);
  currentContext.current = context;
  useEffect(() => { setResult(null); setError(""); setBusy(false); }, [context]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setResult(null);
    void api<KnowledgeStatus>(base, { signal: controller.signal }).then(setStatus).catch((caught) => {
      if (!isAbortError(caught)) setError(errorMessage(caught));
    });
    return () => controller.abort();
  }, [base, version, open, refresh]);
  async function search() {
    if (!query.trim()) return;
    setBusy(true); setError("");
    try {
      const found = await api<SearchResult>(`${base}/search`, { method: "POST", body: jsonBody({ query: query.trim() }) });
      if (currentContext.current === context) setResult(found);
    }
    catch (caught) { if (currentContext.current === context) setError(errorMessage(caught)); }
    finally { if (currentContext.current === context) setBusy(false); }
  }
  async function rebuild() {
    setBusy(true); setError("");
    try { await api(`${base}/rebuild`, { method: "POST" }); setRefresh((n) => n + 1); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }
  return <details className="blueprint-band" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>本书知识库与检索</summary>
    {open && <>
      <p>只检索本书的正式资料与有效正文。阶段内未采用的接力另行保留。</p>
      {status && <p role="status">{status.status === "ready" ? "语义索引已就绪" : status.status === "migration_required" ? "索引尚未初始化，当前可用文本检索" : status.status === "lexical" ? "文本检索可用" : "索引更新中，当前可用文本检索"} · {status.chunk_count} 个片段</p>}
      <div className="button-row">
        <input aria-label="本书知识检索词" value={query} maxLength={900} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !busy) void search(); }} />
        <button type="button" disabled={busy || !query.trim()} onClick={() => void search()}>搜索本书资料</button>
        <button type="button" disabled={busy} onClick={() => setRefresh((n) => n + 1)}>刷新索引状态</button>
        <button type="button" disabled={busy || !status || status.status === "migration_required"} onClick={() => void rebuild()}>重建本书索引</button>
      </div>
      {busy && <p role="status">正在处理…</p>}
      {error && <p role="alert">{error}</p>}
      {result && <><p>{result.mode === "hybrid" ? "语义与文本综合检索" : "文本检索"} · {result.hits.length} 条相关资料</p><KnowledgeHits hits={result.hits} /></>}
    </>}
  </details>;
}

export function SavedKnowledge({ userPrompt }: { userPrompt?: string }) {
  if (!userPrompt) return null;
  let material: { hits?: KnowledgeHit[]; mode?: string } | undefined;
  try { material = JSON.parse(userPrompt).knowledge_context; } catch { return null; }
  if (!material || !Array.isArray(material.hits)) return null;
  return <details><summary>本次检索补充 · {material.hits.length} 条资料</summary>
    <p>以下是本次实际发送的历史与事实补充；完整世界规则和阶段接力可在任务 Prompt 中查看。</p>
    <KnowledgeHits hits={material.hits} />
  </details>;
}
