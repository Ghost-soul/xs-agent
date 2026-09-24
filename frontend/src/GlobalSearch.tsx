import { useEffect, useState } from "react";

import {
  api,
  commandHeaders,
  errorMessage,
  jsonBody,
  type LocalSearchResponse,
  type SearchResultResponse,
} from "./api";

type SearchIndexStatus = LocalSearchResponse["index_status"];
type SearchStatus = { status: SearchIndexStatus };

export function GlobalSearch({ projectId, onNavigate }: {
  projectId: string | null;
  onNavigate: (href: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [includeHistory, setIncludeHistory] = useState(false);
  const [results, setResults] = useState<SearchResultResponse[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [indexStatus, setIndexStatus] = useState<SearchIndexStatus>("not_built");
  const [rebuilding, setRebuilding] = useState(false);
  useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();
    void api<SearchStatus>(`/api/search/status?project_id=${encodeURIComponent(projectId)}`, { signal: controller.signal })
      .then((value) => {
        if (controller.signal.aborted) return;
        setIndexStatus(value.status);
        if (value.status === "not_built" || value.status === "stale") {
          void rebuildIndex(projectId);
        }
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(caught, "读取搜索索引状态失败"));
      });
    return () => controller.abort();
  }, [projectId]);
  useEffect(() => {
    if (!projectId || query.trim().length === 0) { setResults([]); return; }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void api<LocalSearchResponse>(`/api/search?project_id=${encodeURIComponent(projectId)}&q=${encodeURIComponent(query.trim())}&include_history=${includeHistory}`, { signal: controller.signal })
        .then((value) => { setResults(value.results); setIndexStatus(value.index_status); setError(null); })
        .catch((caught: unknown) => {
          if (controller.signal.aborted) return;
          setError(errorMessage(caught, "搜索失败"));
        });
    }, 180);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [includeHistory, projectId, query]);

  function openResult(result: SearchResultResponse) {
    window.sessionStorage.setItem("novel-writer:search-target-v1", JSON.stringify({
      project_id: projectId,
      source_kind: result.source_kind,
      source_id: result.source_id,
      source_sha256: result.source_sha256,
      chapter_id: result.chapter_id,
      offset: result.offset,
      match_field: result.match_field,
      query: query.trim(),
    }));
    onNavigate(result.route);
    setOpen(false);
  }

  async function rebuildIndex(targetProjectId = projectId) {
    if (!targetProjectId || rebuilding) return;
    setRebuilding(true); setError(null);
    try {
      await api("/api/search/rebuild", {
        method: "POST",
        headers: commandHeaders(),
        body: jsonBody({ project_id: targetProjectId, confirmed: true }),
      });
      setIndexStatus("rebuilding");
    } catch (caught) {
      setError(errorMessage(caught, "重建搜索索引失败"));
    } finally {
      setRebuilding(false);
    }
  }

  return <div className="global-search">
    <label className="sr-only" htmlFor="global-search-input">搜索正文和故事资料</label>
    <input id="global-search-input" value={query} onFocus={() => setOpen(true)} onChange={(event) => { setQuery(event.target.value); setOpen(true); }} placeholder="搜索正文、人物、规则、伏笔" />
    {open && query.trim() && <div className="global-search-results" role="listbox">
      <label className="search-history-toggle"><input type="checkbox" checked={includeHistory} onChange={(event) => setIncludeHistory(event.target.checked)} />包含历史版本</label>
      {(indexStatus === "not_built" || indexStatus === "failed" || indexStatus === "stale") && <div className="search-index-action"><span>{indexStatus === "failed" ? "索引重建失败" : indexStatus === "stale" ? "正式版本已变化，搜索索引需要重建" : "尚未建立本地索引"}</span><button type="button" disabled={rebuilding} onClick={() => void rebuildIndex()}>{rebuilding ? "正在排队…" : "重建索引"}</button></div>}
      {indexStatus === "rebuilding" && <p className="muted-copy" role="status">本地索引正在重建，完成后会出现在任务中心。</p>}
      {error && <p className="error-copy">{error}</p>}
      {!error && results.length === 0 && <p className="muted-copy">没有匹配内容</p>}
      {results.map((result) => <button type="button" role="option" key={`${result.source_kind}-${result.source_id}-${result.version}`} onClick={() => openResult(result)}>
        <strong>{result.title}</strong><small>{result.historical ? `历史 v${result.version}` : "当前正式内容"}</small><span>{result.snippet}</span>
      </button>)}
    </div>}
  </div>;
}
