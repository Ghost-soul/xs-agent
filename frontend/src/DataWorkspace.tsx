import { useEffect, useState, type ChangeEvent } from "react";

import {
  api,
  commandHeaders,
  jsonBody,
  type ImportCommitResponse,
  type ImportPreviewResponse,
} from "./api";
import { requestConfirmation } from "./confirmation";
import { deleteLocalDraft, listLocalDrafts, type LocalDraft } from "./localDrafts";

type Inspection = {
  valid: boolean;
  title: string;
  formal_version: number;
  chapter_count: number;
  characters: number;
  created_at: string;
  message: string;
};

type WorkspaceStatus = {
  path: string;
  exists: boolean;
  formal_version: number;
  session_count: number;
  chunk_count: number;
  last_synced_at: string | null;
  synced_version: number | null;
  up_to_date: boolean;
  policy: string;
};

export function DataWorkspace({ projectId, projectTitle, onProjectCreated }: {
  projectId: string;
  projectTitle: string;
  onProjectCreated: (projectId: string) => Promise<void>;
}) {
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importTitle, setImportTitle] = useState("");
  const [archive, setArchive] = useState<Record<string, unknown> | null>(null);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceStatus | null>(null);
  const [encoding, setEncoding] = useState("auto");
  const [importPreview, setImportPreview] = useState<ImportPreviewResponse | null>(null);
  const [localDrafts, setLocalDrafts] = useState<LocalDraft[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    setWorkspace(null);
    api<WorkspaceStatus>(`/api/projects/${projectId}/workspace`, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setWorkspace(value); })
      .catch((caught: unknown) => { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "无法读取项目文件夹"); });
    return () => controller.abort();
  }, [projectId]);
  useEffect(() => {
    let active = true;
    setLocalDrafts([]);
    void listLocalDrafts(projectId)
      .then((value) => { if (active) setLocalDrafts(value); })
      .catch((caught: unknown) => { if (active) setError(caught instanceof Error ? caught.message : "无法读取本地草稿"); });
    return () => { active = false; };
  }, [projectId]);

  async function run(work: () => Promise<void>) {
    setBusy(true); setError(null); setMessage(null);
    try { await work(); } catch (caught) { setError(caught instanceof Error ? caught.message : "操作失败"); }
    finally { setBusy(false); }
  }

  async function previewManuscript() {
    if (!importFile) return;
    await run(async () => {
      const params = new URLSearchParams({
        title: importTitle.trim() || fileTitle(importFile.name),
        encoding,
      });
      const preview = await api<ImportPreviewResponse>(`/api/imports/preview?${params}`, {
        method: "POST",
        headers: { "X-Upload-Filename": encodeURIComponent(importFile.name) },
        body: importFile,
      });
      setImportPreview(preview);
      if (preview.requires_encoding_confirmation) {
        setEncoding(preview.selected_encoding);
        setMessage(`检测到 ${preview.selected_encoding}，请检查样本后再次生成预览以明确确认编码。`);
      } else setMessage(`已生成 ${preview.chapter_count} 章的绑定预览；尚未创建正式项目。`);
    });
  }

  async function importManuscript() {
    if (!importPreview || importPreview.requires_encoding_confirmation) return;
    await run(async () => {
      const created = await api<ImportCommitResponse>(`/api/imports/${importPreview.preview_id}/commit`, {
        method: "POST", headers: commandHeaders(), body: jsonBody({
          upload_sha256: importPreview.upload_sha256,
          chapter_manifest_sha256: importPreview.chapter_manifest_sha256,
          selected_encoding: importPreview.selected_encoding,
          parser_version: importPreview.parser_version,
          confirmed: true,
        }),
      });
      setMessage(`已导入 ${created.chapter_count} 章，并创建为独立项目。`);
      await onProjectCreated(created.project_id);
    });
  }

  async function exportBook(format: "markdown" | "txt" | "docx" | "epub" = "markdown") {
    await run(async () => {
      await api(`/api/projects/${projectId}/local-tasks`, {
        method: "POST",
        headers: commandHeaders(),
        body: jsonBody({ kind: `export_${format}`, confirmed: true }),
      });
      setMessage(`已把 ${format.toUpperCase()} 导出加入本地任务中心；完成后可下载工件。`);
    });
  }

  async function backupProject() {
    await run(async () => {
      await api(`/api/projects/${projectId}/local-tasks`, {
        method: "POST",
        headers: commandHeaders(),
        body: jsonBody({ kind: "project_backup", confirmed: true }),
      });
      setMessage("作品备份已加入本地任务中心；完成后可下载带完整性校验码的工件。");
    });
  }

  async function syncWorkspace() {
    await run(async () => {
      const status = await api<WorkspaceStatus>(`/api/projects/${projectId}/workspace/sync`, {
        method: "POST", headers: commandHeaders(), body: jsonBody({ confirmed: true }),
      });
      setWorkspace(status);
      setMessage(`已同步项目文件夹：${status.session_count}个写作会话、${status.chunk_count}个分块。`);
    });
  }

  async function selectBackup(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    setArchive(null); setInspection(null); setError(null); setMessage(null);
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as Record<string, unknown>;
      const result = await api<Inspection>("/api/data/backup/inspect", { method: "POST", body: jsonBody({ archive: parsed }) });
      setArchive(parsed); setInspection(result);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "无法读取备份"); }
  }

  async function restoreBackup() {
    if (!archive || !inspection?.valid) return;
    await run(async () => {
      const created = await api<{ project_id: string }>("/api/data/backup/restore", {
        method: "POST", headers: commandHeaders(), body: jsonBody({ archive, confirmed: true }),
      });
      setMessage("恢复完成。原项目未被覆盖，已创建恢复副本。");
      await onProjectCreated(created.project_id);
    });
  }

  async function removeLocalDraft(item: LocalDraft) {
    const accepted = await requestConfirmation({
      title: "丢弃本地草稿",
      message: `确认丢弃“${draftSurfaceLabel(item.surface)}”草稿？此操作只删除浏览器本地恢复副本，不修改后端候选或正式正文。`,
      confirmLabel: "丢弃草稿",
      danger: true,
    });
    if (!accepted) return;
    await deleteLocalDraft(item.draft_key);
    setLocalDrafts((current) => current.filter((draft) => draft.draft_key !== item.draft_key));
    setMessage("本地恢复草稿已丢弃；后端内容未变化。");
  }

  return <div className="data-workspace">
    <header><span className="eyebrow">作品与安全</span><h2>项目文件夹、导入导出与备份</h2><p>每本小说拥有独立的本机文件夹。数据库仍是正式来源，文件夹保存可读镜像和逐块资料。</p></header>
    {error && <div className="error-banner">{error}</div>}
    {message && <div className="success-banner">{message}</div>}
    <section className="data-card-grid">
      <article><span className="data-icon">夹</span><h3>项目文件夹</h3><p>同步正式正文、世界状态、作品备份，以及每个写作会话的逐块 Markdown。不同小说互不混放。</p>{workspace ? <><strong>{workspace.exists ? (workspace.up_to_date ? "已同步到当前正式版本" : "需要重新同步") : "尚未创建"}</strong><small>{workspace.session_count}个写作会话 · {workspace.chunk_count}个分块</small><small title={workspace.path}>{workspace.path}</small><button className="primary-button" disabled={busy} onClick={() => void syncWorkspace()}>{workspace.exists ? "同步到项目文件夹" : "创建并同步"}</button></> : <small>正在读取文件夹状态…</small>}</article>
      <article><span className="data-icon">入</span><h3>导入已有小说</h3><p>原始文件先上传到受控暂存区，按字节 SHA、编码和拆章清单生成预览；确认提交时服务端会全部重算。</p><label className="file-field">选择小说文件<input type="file" accept=".md,.markdown,.txt,text/plain,text/markdown" onChange={(event) => { setImportFile(event.target.files?.[0] ?? null); setImportPreview(null); }} /></label>{importFile && <><input className="data-title-input" value={importTitle} onChange={(event) => { setImportTitle(event.target.value); setImportPreview(null); }} placeholder={fileTitle(importFile.name)} /><label>编码<select value={encoding} onChange={(event) => { setEncoding(event.target.value); setImportPreview(null); }}><option value="auto">自动检测</option><option value="utf-8">UTF-8</option><option value="gb18030">GB18030 / GBK</option><option value="utf-16le">UTF-16 LE</option><option value="utf-16be">UTF-16 BE</option></select></label><small>{importFile.name} · {importFile.size.toLocaleString()} 字节</small><button className="primary-button" disabled={busy} onClick={() => void previewManuscript()}>生成拆章预览</button>{importPreview && <div className="import-preview"><strong>{importPreview.chapter_count} 章 · {importPreview.character_count.toLocaleString()} 字 · {importPreview.selected_encoding}</strong><small>文件 SHA {importPreview.upload_sha256.slice(0, 12)}… · 清单 SHA {importPreview.chapter_manifest_sha256.slice(0, 12)}…</small>{importPreview.warnings.length > 0 && <ul>{importPreview.warnings.map((warning, index) => <li key={`${warning.code}-${index}`}>{importWarningLabel(warning)}</li>)}</ul>}<div className="import-preview-chapters">{importPreview.chapters.map((chapter) => <div key={`${chapter.ordinal}-${chapter.title}`}><b>第{chapter.ordinal}章 {chapter.title}</b><span>{chapter.characters.toLocaleString()} 字</span><small>{chapter.first_sample}</small><small>{chapter.last_sample}</small></div>)}</div><button className="primary-button" disabled={busy || importPreview.requires_encoding_confirmation} onClick={() => void importManuscript()}>{importPreview.requires_encoding_confirmation ? "请按检测编码重新预览" : "确认清单并创建项目"}</button></div>}</>}</article>
      <article><span className="data-icon">出</span><h3>导出正式作品</h3><p>按正式版本顺序导出章节正文。待审稿、失败响应和内部记录不会出现在成稿中。</p><strong>{projectTitle}</strong><div className="button-row"><button className="primary-button" disabled={busy} onClick={() => void exportBook("markdown")}>Markdown</button><button disabled={busy} onClick={() => void exportBook("txt")}>TXT</button><button disabled={busy} onClick={() => void exportBook("docx")}>DOCX</button><button disabled={busy} onClick={() => void exportBook("epub")}>EPUB</button></div></article>
      <article><span className="data-icon">备</span><h3>创建作品备份</h3><p>保存正式正文、章节顺序和当前故事状态，并附带 SHA-256 完整性校验。</p><button className="primary-button" disabled={busy} onClick={() => void backupProject()}>下载备份文件</button></article>
      <article><span className="data-icon">复</span><h3>检查并恢复</h3><p>系统先检查格式和完整性。恢复始终生成副本，现有项目不会被覆盖。</p><label className="file-field">选择备份文件<input type="file" accept=".json,application/json" onChange={(event) => void selectBackup(event)} /></label>{inspection && <div className={inspection.valid ? "backup-ok" : "backup-bad"}><strong>{inspection.message}</strong><span>{inspection.title} · v{inspection.formal_version} · {inspection.chapter_count}章 · {inspection.characters}字</span></div>}{inspection?.valid && <button className="primary-button" disabled={busy} onClick={() => void restoreBackup()}>恢复为新项目</button>}</article>
      <article><span className="data-icon">稿</span><h3>本地草稿</h3><p>只显示当前作品和全局设置的浏览器恢复副本。恢复需回到对应编辑页确认，不会自动覆盖后端内容。</p>{localDrafts.length === 0 ? <small>没有可管理的本地草稿</small> : <div className="local-draft-list">{localDrafts.map((item) => <div key={item.draft_key}><span><strong>{draftSurfaceLabel(item.surface)}</strong><small>{new Date(item.updated_at).toLocaleString()} · {item.project_id === "global" ? "全局设置" : "当前作品"}</small></span><button type="button" className="danger-command" onClick={() => void removeLocalDraft(item)}>丢弃</button></div>)}</div>}</article>
    </section>
    <aside className="backup-scope"><strong>备份包含什么</strong><p>当前正式版本的全部章节正文、章节顺序、StoryState，以及题材质量卡和风格资产。供应商密钥、费用账单、失败响应与尚未采用的候选稿不会进入作品备份。</p></aside>
  </div>;
}

function fileTitle(name: string) { return name.replace(/\.(md|markdown|txt)$/i, "") || "导入作品"; }
function draftSurfaceLabel(surface: string): string {
  return ({ blueprint: "故事资料", candidate_body: "候选正文", review_package: "审核包", model_config: "模型配置", title_edit: "章节标题" } as Record<string, string>)[surface] ?? surface;
}

function importWarningLabel(warning: ImportPreviewResponse["warnings"][number]): string {
  if (warning.code === "short_chapter") return `第 ${warning.ordinal} 章偏短`;
  if (warning.code === "long_chapter") return `第 ${warning.ordinal} 章偏长`;
  if (warning.code === "duplicate_title") return `标题“${warning.title}”重复 ${warning.count} 次`;
  if (warning.code === "ordinal_gap") return `章节序号从 ${warning.previous} 跳到 ${warning.current}`;
  if (warning.code === "control_characters") return `包含 ${warning.count} 个不可打印字符`;
  return warning.code;
}
