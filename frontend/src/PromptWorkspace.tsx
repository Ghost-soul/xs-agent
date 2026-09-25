import { useCallback, useEffect, useRef, useState } from "react";
import { diffLines } from "diff";

import { api, commandHeaders, errorMessage, jsonBody } from "./api";
import { requestConfirmation } from "./confirmation";
import { clearDirtySurface, setDirtySurface } from "./unsavedChanges";
import { templateIssues, templateTokens } from "./promptTemplateValidation";

type TemplateText = { system_text: string; task_template: string };
type Entry = {
  variant: string; label: string; text: TemplateText; default_text: TemplateText;
  customized: boolean; fields: { key: string; label: string; required: boolean }[];
  output_requirement: string;
};
type Catalog = {
  revision: string; engine_system: string; entries: Entry[];
  history: { revision: string; created_at: string; note: string }[];
};
type Batch = { id: string; direction: string; created_at: string };
type Preview = {
  system_prompt: string; task_prompt: string; input_count: number; counting_method: string;
  blockers: string[]; omitted_optional: string[]; engine_contract: Record<string, unknown>;
  source_action: string; source_description: string;
};
type Version = { revision: string; text: TemplateText; customized: boolean };

export function PromptWorkspace({ projectId }: { projectId: string }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [variant, setVariant] = useState("chief");
  const [draft, setDraft] = useState<TemplateText | null>(null);
  const [useDefault, setUseDefault] = useState(true);
  const [note, setNote] = useState("");
  const [batches, setBatches] = useState<Batch[]>([]);
  const [batchId, setBatchId] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [version, setVersion] = useState<Version | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const taskRef = useRef<HTMLTextAreaElement>(null);
  const entry = catalog?.entries.find((item) => item.variant === variant);
  const dirty = !!entry && !!draft && (
    JSON.stringify(draft) !== JSON.stringify(entry.text) || useDefault === entry.customized
  );
  const issues = entry && draft && !useDefault ? templateIssues(draft, entry.fields) : [];

  useEffect(() => {
    const controller = new AbortController();
    void api<Catalog>("/api/prompt-templates", { signal: controller.signal }).then((value) => {
      setCatalog(value);
      setDraft(value.entries[0].text);
      setUseDefault(!value.entries[0].customized);
    }).catch((caught) => { if (!controller.signal.aborted) setError(errorMessage(caught, "模板读取失败")); });
    void api<Batch[]>(`/api/projects/${projectId}/generation-batches`, { signal: controller.signal }).then((value) => {
      setBatches(value); setBatchId(value[0]?.id ?? "");
    }).catch((caught) => { if (!controller.signal.aborted) setError(errorMessage(caught, "批次读取失败")); });
    return () => controller.abort();
  }, [projectId]);

  useEffect(() => { setPreview(null); }, [draft, useDefault, batchId, variant]);

  const save = useCallback(async () => {
    if (!catalog || !draft || busyRef.current) throw new Error("请等待当前操作完成");
    busyRef.current = true; setBusy(true); setError(null); setNotice(null);
    try {
      const invalid = !useDefault && entry ? templateIssues(draft, entry.fields) : [];
      if (invalid.length) throw new Error("尚未保存，请先修正下方列出的模板问题。当前编辑内容已保留。");
      const value = await api<Catalog>(`/api/prompt-templates/${variant}`, {
        method: "PUT", headers: commandHeaders(), body: jsonBody({
          expected_revision: catalog.revision, template: useDefault ? null : draft,
          note: note.trim() || `${entry?.label ?? variant}：${useDefault ? "恢复项目默认" : "手动修改"}`,
        }),
      });
      setCatalog(value);
      const selected = value.entries.find((item) => item.variant === variant)!;
      setDraft(selected.text); setUseDefault(!selected.customized); setNote("");
      setNotice("已保存。新建预览和新独立修订使用此默认值；现有批次保持原模板，无需重启。");
    } catch (caught) {
      setError(errorMessage(caught, "保存模板失败")); throw caught;
    } finally { busyRef.current = false; setBusy(false); }
  }, [catalog, draft, entry, note, useDefault, variant]);

  useEffect(() => {
    setDirtySurface("prompt-templates", "Prompt 模板", dirty, {
      save,
      discard: () => { if (entry) { setDraft(entry.text); setUseDefault(!entry.customized); } },
      focus: () => taskRef.current?.focus(),
    });
    return () => clearDirtySurface("prompt-templates");
  }, [dirty, entry, save]);

  async function operation(action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(null); setNotice(null);
    try { await action(); }
    catch (caught) { setError(errorMessage(caught, "操作失败")); }
    finally { busyRef.current = false; setBusy(false); }
  }

  async function selectVariant(next: string) {
    if (dirty && !await requestConfirmation({
      title: "放弃当前模板修改？", message: "当前内容尚未保存，切换角色将放弃这些修改。", confirmLabel: "放弃并切换",
    })) return;
    const selected = catalog!.entries.find((item) => item.variant === next)!;
    setVariant(next); setDraft(selected.text); setUseDefault(!selected.customized);
    setVersion(null); setError(null); setNotice(null); setNote("");
  }

  function change(key: keyof TemplateText, text: string) {
    setDraft((current) => ({ ...current!, [key]: text })); setUseDefault(false);
    setError(null); setNotice(null);
  }

  function locate(start: number, end: number) {
    const textarea = taskRef.current;
    if (!textarea) return;
    textarea.focus(); textarea.setSelectionRange(start, end);
    textarea.scrollIntoView?.({ block: "center", behavior: "smooth" });
  }

  function insert(key: string) {
    const textarea = taskRef.current;
    if (!textarea || !draft) return;
    const existing = templateTokens(draft.task_template).find((token) => token.key === key);
    if (existing) {
      locate(existing.start, existing.end);
      setNotice(`已定位现有 {{${key}}}。如需调整位置，请剪切后移动；没有重复插入。`);
      return;
    }
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const token = `{{${key}}}`;
    change("task_template", draft.task_template.slice(0, start) + token + draft.task_template.slice(end));
    requestAnimationFrame(() => { textarea.focus(); textarea.setSelectionRange(start + token.length, start + token.length); });
  }

  if (!catalog || !draft || !entry) return <section className="workspace-section"><h2>Prompt 模板</h2>
    {error ? <p role="alert">{error}</p> : <p>正在读取模板…</p>}</section>;

  return <section className="workspace-section prompt-workspace">
    <h2>Prompt 模板</h2>
    <p>默认值适用于所有作品。编辑系统指导文本与任务结构，再用本作品的已保存资料预览；不会调用模型。</p>
    <label>角色与任务<select aria-label="角色与任务" value={variant} disabled={busy} onChange={(event) => { void selectVariant(event.target.value); }}>
      {catalog.entries.map((item) => <option key={item.variant} value={item.variant}>{item.label}</option>)}
    </select></label>
    <p>{useDefault ? "项目内置默认" : "手动模板"} · {dirty ? "有未保存修改" : "已同步"}</p>
    <label>系统 Prompt 默认文本<textarea rows={16} value={draft.system_text} disabled={busy}
      onChange={(event) => change("system_text", event.target.value)} /></label>
    <label>任务 Prompt 模板<textarea ref={taskRef} rows={20} value={draft.task_template} disabled={busy}
      onChange={(event) => change("task_template", event.target.value)} /></label>
    <p>可以改标题、顺序和说明文字。每项占位符最多出现一次；“必要”项不能遗漏。点击下面的字段可定位已有占位符，或插入缺少的占位符。</p>
    <div className="prompt-placeholders" aria-label="可用占位符">{entry.fields.map((field) => <button
      type="button" key={field.key} disabled={busy} title={`定位或插入 {{${field.key}}}`} onClick={() => insert(field.key)}>
      {field.label}{field.required ? "（必要）" : "（可选）"}
    </button>)}</div>
    <details><summary>程序自动保留的输出与来源要求</summary>
      <p>{catalog.engine_system}</p><p>{entry.output_requirement}</p>
      <p>输出字段、来源边界、单元上限及授权范围按实际任务注入，最终预览可查看全文。Reader 未重新启用。</p>
    </details>
    <label>版本备注<input value={note} maxLength={300} disabled={busy} placeholder="例如：调整事件设计顺序" onChange={(event) => setNote(event.target.value)} /></label>
    <div className="button-row">
      <button type="button" disabled={!dirty || busy} onClick={() => { void save().catch(() => undefined); }}>{busy ? "处理中…" : "保存为默认"}</button>
      <button type="button" disabled={busy} onClick={() => {
        setDraft(entry.default_text); setUseDefault(true); setNotice("已载入项目默认文本，保存后生效。");
      }}>恢复项目默认到草稿</button>
      <button type="button" disabled={busy} onClick={() => { void operation(async () => {
        setCatalog(await api<Catalog>("/api/prompt-templates"));
        setNotice("已读取最新默认版本，当前草稿保留；可在下方比较版本后再保存。");
      }); }}>刷新默认版本（保留草稿）</button>
    </div>
    <div className="prompt-save-feedback" aria-label="模板保存反馈" aria-live="polite">
      {busy ? <p role="status">正在处理，请稍候…</p> : <>
        {(error || issues.length > 0) && <div role="alert">
          <p>{error ?? "任务模板暂不能保存，请修正以下问题。"}</p>
          {issues.length > 0 && <ul>{issues.map((issue, index) => <li key={index}>
            {issue.message}{issue.start !== undefined && issue.end !== undefined && <button
              type="button" onClick={() => locate(issue.start!, issue.end!)}
              aria-label={`定位问题字段 ${issue.key ?? ""}`}>定位这一处</button>}
          </li>)}</ul>}
        </div>}
        {notice && <p role="status">{notice}</p>}
        {!error && !notice && issues.length === 0 && <p>{dirty ? "有未保存修改，请点击保存为默认。" : "当前模板已与默认版本同步。"}</p>}
      </>}
    </div>
    <h3>本地预览</h3>
    <label>使用本作品的批次资料<select aria-label="使用本作品的批次资料" value={batchId} disabled={busy} onChange={(event) => setBatchId(event.target.value)}>
      {!batches.length && <option value="">请先在创作页建立预览</option>}
      {batches.map((batch) => <option key={batch.id} value={batch.id}>{new Date(batch.created_at).toLocaleString()} · {batch.direction.slice(0, 50)}</option>)}
    </select></label>
    <p>Chief 可直接使用未运行的预览资料；其他角色使用所选批次最近一次该角色的调用资料，不重新检索。</p>
    <button type="button" disabled={!batchId || busy} onClick={() => { void operation(async () => {
      const result = await api<Preview>("/api/prompt-templates/preview", {
        method: "POST", headers: commandHeaders(), body: jsonBody({
          project_id: projectId, batch_id: batchId, variant, template: useDefault ? null : draft,
        }),
      });
      setPreview(result);
    }); }}>预览当前草稿（不调用模型）</button>
    {preview && <div className="prompt-preview" aria-label="渲染结果">
      <p>{preview.source_description} 动作：{preview.source_action}</p>
      <p>完整输入计数 / 上界：{preview.input_count.toLocaleString()} · {preview.counting_method === "utf8-byte-upper-bound" ? "UTF-8 字节保守上界，并非实际 tokens" : `本地分词计数（${preview.counting_method}）`}</p>
      {preview.blockers.map((blocker, i) => <p key={i} role="alert">{blocker}</p>)}
      {!!preview.omitted_optional.length && <p>未包含的可选资料：{preview.omitted_optional.join("、")}</p>}
      <details open><summary>最终系统 Prompt</summary><pre>{preview.system_prompt}</pre></details>
      <details open><summary>最终任务 Prompt</summary><pre>{preview.task_prompt}</pre></details>
    </div>}
    <h3>版本与比较</h3>
    <p>展示最近 50 个版本；比较和恢复只作用于当前角色。</p>
    <label>已保存版本<select aria-label="已保存版本" value={version?.revision ?? ""} disabled={busy}
      onChange={(event) => { const revision = event.target.value; if (!revision) { setVersion(null); return; }
        void operation(async () => setVersion(await api<Version>(`/api/prompt-templates/versions/${revision}/${variant}`)));
      }}><option value="">选择版本，与当前草稿比较</option>
      {catalog.history.map((item) => <option key={item.revision} value={item.revision}>{new Date(item.created_at).toLocaleString()} · {item.note}</option>)}
    </select></label>
    {version && <>
      <p>比较所选版本 → 当前草稿：红色为删除，绿色为增加。</p>
      <details open><summary>系统文本差异</summary><TextDiff before={version.text.system_text} after={draft.system_text} /></details>
      <details open><summary>任务结构差异</summary><TextDiff before={version.text.task_template} after={draft.task_template} /></details>
      <button type="button" disabled={busy} onClick={() => {
        setDraft(version.text); setUseDefault(!version.customized); setNote(`恢复版本 ${version.revision}`);
        setNotice("历史版本已载入草稿，保存后作为新默认值生效。");
      }}>将此版本载入草稿</button>
    </>}
  </section>;
}

function TextDiff({ before, after }: { before: string; after: string }) {
  if (before === after) return <p>内容相同。</p>;
  return <pre className="prompt-diff">{diffLines(before, after).map((part, index) =>
    part.added ? <ins key={index}>{part.value}</ins> : part.removed ? <del key={index}>{part.value}</del> : <span key={index}>{part.value}</span>,
  )}</pre>;
}
