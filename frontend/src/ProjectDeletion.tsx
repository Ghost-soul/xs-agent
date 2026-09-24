import { useEffect, useRef, useState } from "react";

import { api, commandHeaders, jsonBody } from "./api";
import { requestConfirmation } from "./confirmation";
import type { components } from "./generated/api";

export type DeletionReceipt = components["schemas"]["ProjectDeletionResponse"] & {
  browser_cleanup_error?: string;
};
type DeletionPreview = components["schemas"]["ProjectDeletionPreview"];
type ResultHandler = (receipt: DeletionReceipt) => Promise<void>;
type DeletionPhase = "preview" | "confirmation" | "deleting" | "browser_cleanup";

export function ProjectDeleteButton({ projectId, title, disabled, onResult }: {
  projectId: string;
  title: string;
  disabled: boolean;
  onResult: ResultHandler;
}) {
  const [phase, setPhase] = useState<DeletionPhase | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const previewRequest = useRef<AbortController | null>(null);

  useEffect(() => () => {
    previewRequest.current?.abort();
    previewRequest.current = null;
  }, []);

  useEffect(() => {
    setElapsed(0);
    if (!phase || phase === "confirmation") return;
    const timer = window.setInterval(() => setElapsed((seconds) => seconds + 1), 1000);
    return () => window.clearInterval(timer);
  }, [phase]);

  function cancelPreview() {
    previewRequest.current?.abort();
    previewRequest.current = null;
    setPhase(null);
  }

  async function removeProject() {
    if (previewRequest.current) return;
    const controller = new AbortController();
    previewRequest.current = controller;
    setPhase("preview");
    setSubmitted(false);
    setError(null);
    const timeout = window.setTimeout(() => {
      if (previewRequest.current !== controller) return;
      cancelPreview();
      setError("删除前核对超过 60 秒，已停止等待；未提交删除。请稍后重新核对。");
    }, 60_000);
    controller.signal.addEventListener("abort", () => window.clearTimeout(timeout), { once: true });
    try {
      const preview = await api<DeletionPreview>(`/api/projects/${projectId}/delete-preview`, {
        signal: controller.signal,
      });
      window.clearTimeout(timeout);
      if (controller.signal.aborted || previewRequest.current !== controller) return;
      if (preview.project_id !== projectId || preview.title !== title) {
        throw new Error("作品信息已变化，请刷新书架后重新预览。");
      }
      if (preview.blockers.length) throw new Error(preview.blockers.join("；"));
      const counts = preview.counts;
      setPhase("confirmation");
      const accepted = await requestConfirmation({
        title: `永久删除小说“${title}”`,
        message: `将删除整本小说及其正文、设定、草稿、运行记录、提示词和原始响应，不可恢复。\n\n${counts.chapters ?? 0} 章 · ${counts.state_versions ?? 0} 个版本 · ${counts.writing_sessions ?? 0} 个写作会话\n${preview.file_count} 个专属文件，约 ${(preview.estimated_file_bytes / 1024 / 1024).toFixed(2)} MiB；${preview.shared_files_preserved} 个共享文件保留。\n\n${preview.exclusions.join("\n")}\n\n清理时需要短暂独占数据库，正在执行的其他操作会使本次清理暂停。`,
        requiredText: title,
        confirmLabel: "永久删除整本小说",
        danger: true,
      });
      if (!accepted || controller.signal.aborted || previewRequest.current !== controller) return;
      setPhase("deleting");
      setSubmitted(true);
      const result = await api<DeletionReceipt>(`/api/projects/${projectId}/delete`, {
        method: "POST", headers: commandHeaders(),
        body: jsonBody({ confirmed: true, confirmed_title: title, binding_sha256: preview.binding_sha256 }),
      });
      setPhase("browser_cleanup");
      await onResult(result);
    } catch (caught) {
      if (controller.signal.aborted || previewRequest.current !== controller) return;
      setError(caught instanceof Error ? caught.message : "删除失败；请在删除记录中核对状态。");
    } finally {
      window.clearTimeout(timeout);
      if (previewRequest.current === controller) {
        previewRequest.current = null;
        setPhase(null);
      }
    }
  }

  return <div className="project-delete-action">
    <button className="danger-command" type="button" disabled={disabled || phase !== null}
      aria-label={`永久删除小说 ${title}`} onClick={() => void removeProject()}>
      {phase === "preview" ? "核对中…" : phase === "confirmation" ? "等待确认…"
        : phase === "deleting" ? "删除中…" : phase === "browser_cleanup" ? "清理浏览器…" : "删除"}
    </button>
    {phase === "preview" && <>
      <small role="status">正在盘点数据和共享文件，已等待 {elapsed} 秒；尚未删除任何数据。</small>
      <button type="button" onClick={cancelPreview}>取消核对</button>
    </>}
    {(phase === "deleting" || phase === "browser_cleanup") && <small role="status">
      {phase === "deleting" ? "已提交永久删除，正在清理数据与文件" : "服务端已返回，正在同步本浏览器草稿"}
      ，已等待 {elapsed} 秒。请勿重复操作；可在删除记录中核对结果。
    </small>}
    {error && <small role="alert">{error} {submitted
      ? "可打开删除记录刷新核对；不会自动重发。" : "尚未提交删除；不会自动重发。"}</small>}
  </div>;
}

export function ProjectDeletionHistory({ latest, onResult, onRefresh }: {
  latest: DeletionReceipt | null;
  onResult: ResultHandler;
  onRefresh: (receipts: DeletionReceipt[]) => Promise<DeletionReceipt[]>;
}) {
  const [open, setOpen] = useState(false);
  const [receipts, setReceipts] = useState<DeletionReceipt[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!latest) return;
    setReceipts((current) => [latest, ...current.filter((item) => item.deletion_id !== latest.deletion_id)]);
    setOpen(true);
  }, [latest]);

  async function refresh() {
    setBusy(true);
    setError(null);
    try {
      const items = await api<DeletionReceipt[]>("/api/project-deletions");
      setReceipts(await onRefresh(items));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除记录读取失败");
    } finally { setBusy(false); }
  }

  async function retry(receipt: DeletionReceipt) {
    if (!await requestConfirmation({
      title: "继续清理残留文件", confirmLabel: "继续本地清理", danger: true,
      message: "仅继续此删除凭证已确认的文件和索引清理。不调用模型、不重新删除其他小说；共享引用会重新核对。",
    })) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api<DeletionReceipt>(`/api/project-deletions/${receipt.deletion_id}/cleanup`, {
        method: "POST", headers: commandHeaders(), body: jsonBody({ confirmed: true }),
      });
      setReceipts((current) => current.map((item) => item.deletion_id === result.deletion_id ? result : item));
      await onResult(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "本地清理未完成");
    } finally { setBusy(false); }
  }

  return <section className="project-deletion-history" aria-label="小说删除记录">
    <button className="archived-project-toggle" type="button" aria-expanded={open}
      onClick={() => { setOpen(!open); if (!open) void refresh(); }}>删除记录与残留清理</button>
    {open && <div>
      <button type="button" disabled={busy} onClick={() => void refresh()}>刷新删除记录</button>
      {error && <p role="alert">{error}</p>}
      {!busy && receipts.length === 0 && <small>暂无删除记录</small>}
      {receipts.map((receipt) => <article key={receipt.deletion_id}>
        <strong>{receipt.status === "completed"
          ? (receipt.browser_cleanup_error ? "服务端已清理，本浏览器清理未完成" : "清理完成")
          : "数据库已删除，文件清理未完成"}</strong>
        <small>作品 {receipt.project_id.slice(0, 8)} · {new Date(receipt.created_at).toLocaleString()}</small>
        <small>凭证 {receipt.deletion_id}</small>
        {receipt.cleanup_error && <p role="alert">{receipt.cleanup_error === "file_or_index_cleanup_failed"
          ? "文件或索引清理失败，请检查文件完整性及索引依赖。" : receipt.cleanup_error}</p>}
        {receipt.status === "cleanup_pending" && <button type="button" disabled={busy}
          onClick={() => void retry(receipt)}>继续清理残留</button>}
        {receipt.browser_cleanup_error && <><p role="alert">{receipt.browser_cleanup_error}</p>
          <button type="button" disabled={busy} onClick={() => void onResult(receipt)}>清理本浏览器草稿</button></>}
      </article>)}
    </div>}
  </section>;
}
