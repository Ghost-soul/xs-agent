import { useEffect, useState, type ReactNode } from "react";
import { CreativeCardOptions, CreativeCardPool, isGenreCard, type CreativeCardOption } from "./CreativeCards";

import {
  api,
  commandHeaders,
  jsonBody,
  type ImportPreviewResponse,
  type ProjectSetupDraftResponse,
  type ProjectSetupPayload,
} from "./api";

function Dialog({ title, close, children }: { title: string; close: () => void; children: ReactNode }) {
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={close}>
      <section className="dialog" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => event.stopPropagation()}>
        <div className="dialog-header"><h2>{title}</h2><button type="button" aria-label="关闭" onClick={close}>×</button></div>
        {children}
      </section>
    </div>
  );
}

type ImportEncoding = "auto" | "utf-8" | "gb18030" | "utf-16le" | "utf-16be";
type SetupImportBinding = NonNullable<ProjectSetupPayload["import_preview"]>;
type SetupPayload = ProjectSetupPayload;
type SetupDraft = ProjectSetupDraftResponse;
type GenreCardOption = CreativeCardOption & { tagline: string; source_file: string };

export function ProjectDialog({ busy, close, submit }: {
  busy: boolean;
  close: () => void;
  submit: (title: string, setup: SetupPayload, draftId: string) => Promise<void>;
}) {
  const [mode, setMode] = useState<"blank" | "import">("blank");
  const [title, setTitle] = useState("");
  const [step, setStep] = useState(1);
  const [genre, setGenre] = useState("");
  const [genreSelectionMode, setGenreSelectionMode] = useState<"unselected" | "specified">("unselected");
  const [genreCardId, setGenreCardId] = useState<string | null>(null);
  const [secondaryGenreCardIds, setSecondaryGenreCardIds] = useState<string[]>([]);
  const [genreCards, setGenreCards] = useState<GenreCardOption[]>([]);
  const [targetChapters, setTargetChapters] = useState<number | "">("");
  const [worldSummary, setWorldSummary] = useState("");
  const [characterNames, setCharacterNames] = useState("");
  const [chapterMin, setChapterMin] = useState(4000);
  const [chapterTarget, setChapterTarget] = useState(5000);
  const [chapterMax, setChapterMax] = useState(6500);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<ImportPreviewResponse | null>(null);
  const [encoding, setEncoding] = useState<ImportEncoding>("auto");
  const [providerSummary, setProviderSummary] = useState("正在读取本地配置…");
  const [draft, setDraft] = useState<SetupDraft | null>(null);
  const [resumeDraft, setResumeDraft] = useState<SetupDraft | null>(null);
  const [localBusy, setLocalBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const primaryCard = genreCards.find((card) => card.id === genreCardId);
  const sideCount = secondaryGenreCardIds.filter((id) => genreCards.some((card) => card.id === id && isGenreCard(card))).length;
  const narrativeCount = secondaryGenreCardIds.length - sideCount;
  const validCards = genreSelectionMode !== "specified" || (primaryCard && isGenreCard(primaryCard) && sideCount <= 1);

  function choosePrimary(next: string | null) {
    setSecondaryGenreCardIds((items) => [...new Set([
      ...items.filter((id) => id !== next),
      ...(primaryCard && !isGenreCard(primaryCard) ? [primaryCard.id] : []),
    ])]);
    setGenreCardId(next);
  }

  useEffect(() => {
    void api<SetupDraft[]>("/api/project-setup-drafts").then((items) => setResumeDraft(items[0] ?? null)).catch(() => undefined);
    void api<GenreCardOption[]>("/api/genre-quality-cards").then(setGenreCards).catch(() => undefined);
    void api<Array<{ enabled: boolean; display_name: string }>>("/api/provider-profiles")
      .then((profiles) => {
        const enabled = profiles.filter((item) => item.enabled);
        setProviderSummary(enabled.length ? `已启用 ${enabled.length} 个 Provider Profile：${enabled.map((item) => item.display_name).join("、")}` : "尚未启用 Provider Profile；可以先创建作品，之后在高级设置中配置。" );
      })
      .catch(() => setProviderSummary("Provider 配置暂不可读；不会自动发送验证请求。"));
  }, []);

  function payload(nextStep = step): SetupPayload {
    const importBinding = mode === "import" && importPreview !== null && previewIsUsable(importPreview, title)
      ? importPreviewBinding(importPreview)
      : null;
    return {
      mode, title: title.trim(), genre: genre.trim(),
      genre_selection_mode: genreSelectionMode,
      genre_card_id: genreSelectionMode === "specified" ? genreCardId : null,
      secondary_genre_card_ids: genreSelectionMode === "specified" ? secondaryGenreCardIds : [],
      target_chapters: targetChapters === "" ? null : targetChapters,
      chapter_min_characters: chapterMin,
      chapter_target_characters: chapterTarget,
      chapter_max_characters: chapterMax,
      world_summary: worldSummary.trim(),
      character_names: characterNames.split(/[，,\n]/u).map((item) => item.trim()).filter(Boolean),
      import_filename: importFile?.name ?? importPreview?.original_filename ?? draft?.payload.import_filename ?? "",
      import_encoding: encoding,
      import_preview: importBinding,
      step: nextStep,
    };
  }

  async function persist(nextStep: number): Promise<SetupDraft> {
    const nextPayload = payload(nextStep);
    const saved = await api<SetupDraft>(draft ? `/api/project-setup-drafts/${draft.draft_id}` : "/api/project-setup-drafts", {
      method: draft ? "PUT" : "POST",
      headers: commandHeaders(),
      body: jsonBody({ payload: nextPayload, expected_updated_at: draft?.updated_at ?? null }),
    });
    setDraft(saved);
    window.localStorage.setItem("novel-writer:latest-setup-draft", saved.draft_id);
    return saved;
  }

  async function advance() {
    if (step === 2 && !(chapterMin <= chapterTarget && chapterTarget <= chapterMax)) { setError("分章字数必须满足最小值 ≤ 目标值 ≤ 最大值"); return; }
    if (step === 2 && !validCards) { setError("请选择一张主题材，副题材最多一张；叙事卡可以多选"); return; }
    if (step === 5 && mode === "import" && !previewIsUsable(importPreview, title)) {
      setError(importPreview?.requires_encoding_confirmation ? "请明确选择 GB18030 编码并重新预览" : "请先完成有效的服务端导入预览");
      return;
    }
    setLocalBusy(true); setError(null);
    try { const next = step + 1; await persist(next); setStep(next); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "保存建书草稿失败"); }
    finally { setLocalBusy(false); }
  }

  async function restore(saved: SetupDraft) {
    const value = saved.payload;
    setDraft(saved); setMode(value.mode); setTitle(value.title); setGenre(value.genre);
    setGenreSelectionMode(value.genre_selection_mode === "specified" ? "specified" : "unselected");
    setGenreCardId(value.genre_card_id ?? null);
    setSecondaryGenreCardIds(value.secondary_genre_card_ids ?? []);
    setTargetChapters(value.target_chapters ?? ""); setChapterMin(value.chapter_min_characters);
    setChapterTarget(value.chapter_target_characters); setChapterMax(value.chapter_max_characters);
    setWorldSummary(value.world_summary); setCharacterNames(value.character_names.join("，"));
    setEncoding(asImportEncoding(value.import_encoding)); setStep(value.step); setResumeDraft(null);
    setImportFile(null); setImportPreview(null); setError(null);
    const binding = setupImportBinding(value);
    if (!binding) return;
    setLocalBusy(true);
    try {
      const restored = await api<ImportPreviewResponse>(`/api/imports/${binding.preview_id}`);
      if (!previewMatchesBinding(restored, binding)) throw new Error("导入预览绑定已变化，请重新选择文件预览");
      if (!previewIsUsable(restored, value.title)) throw new Error("导入预览已过期或不可用，请重新选择文件预览");
      setImportPreview(restored);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "恢复导入预览失败");
    } finally {
      setLocalBusy(false);
    }
  }

  async function chooseImportFile(file: File | null, selectedEncoding = encoding) {
    setImportFile(file); setImportPreview(null); setError(null);
    if (!file) return;
    setLocalBusy(true);
    try {
      const preview = await api<ImportPreviewResponse>(
        `/api/imports/preview?title=${encodeURIComponent(title.trim())}&encoding=${selectedEncoding}`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/octet-stream",
            "X-Upload-Filename": encodeURIComponent(file.name),
          },
          body: file,
        },
      );
      setImportPreview(preview);
      if (preview.requires_encoding_confirmation) {
        setError(`自动检测结果为 ${preview.selected_encoding}；请明确选择该编码并重新预览`);
      }
    } catch (caught) { setError(caught instanceof Error ? caught.message : "读取导入文件失败"); }
    finally { setLocalBusy(false); }
  }

  async function finalize() {
    if (!validCards) { setError("请返回第二步，选择一张主题材和最多一张副题材"); return; }
    setLocalBusy(true); setError(null);
    try {
      const saved = await persist(6);
      await submit(saved.payload.title, saved.payload, saved.draft_id);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "创建作品失败"); }
    finally { setLocalBusy(false); }
  }

  return (
    <Dialog title="新建项目" close={close}>
      <form onSubmit={(event) => { event.preventDefault(); if (step < 6) void advance(); else void finalize(); }}>
        <p className="muted-copy">第 {step} 步 / 6。每步保存为非正式建书草稿，不会产生 AgentCall 或 Provider 费用。</p>
        {resumeDraft && <div className="success-banner"><span>发现未完成建书“{resumeDraft.payload.title || "未命名作品"}”</span><button type="button" onClick={() => void restore(resumeDraft)}>继续</button></div>}
        {error && <div className="error-banner" role="alert">{error}</div>}
        {step === 1 && <fieldset><legend>开始方式</legend><label><input type="radio" checked={mode === "blank"} onChange={() => setMode("blank")} />空白创作</label><label><input type="radio" checked={mode === "import"} onChange={() => setMode("import")} />导入已有正文</label></fieldset>}
        {step === 2 && <><label>项目名称<input autoFocus value={title} onChange={(event) => { setTitle(event.target.value); setImportPreview(null); }} /></label><div className="field-grid"><label>题材说明<input value={genre} onChange={(event) => setGenre(event.target.value)} placeholder="可选，例如：东方玄幻" /></label><label>题材卡模式<select aria-label="题材卡模式" value={genreSelectionMode} onChange={(event) => { const next = event.target.value as "unselected" | "specified"; setGenreSelectionMode(next); if (next === "unselected") { setGenreCardId(null); setSecondaryGenreCardIds([]); } }}><option value="unselected">暂不选择</option><option value="specified">作者指定</option></select></label>{genreSelectionMode === "specified" && <><label>主题材卡<select aria-label="主题材卡" value={genreCardId ?? ""} onChange={(event) => choosePrimary(event.target.value || null)} disabled={!genreCards.length}><option value="">请选择主题材卡</option><CreativeCardOptions cards={genreCards} layer="genre" /></select></label>{primaryCard && !isGenreCard(primaryCard) && <p role="alert">原主题卡现归叙事卡，请另选主题材；原卡会保留为默认叙事卡。</p>}<CreativeCardPool cards={genreCards} primaryId={genreCardId} selected={secondaryGenreCardIds} update={setSecondaryGenreCardIds} /></>}<label>目标章节<input type="number" min={1} value={targetChapters} onChange={(event) => setTargetChapters(event.target.value ? Number(event.target.value) : "")} /></label><label>分章最小字数<input type="number" min={500} value={chapterMin} onChange={(event) => setChapterMin(Number(event.target.value))} /></label><label>分章目标字数<input type="number" min={500} value={chapterTarget} onChange={(event) => setChapterTarget(Number(event.target.value))} /></label><label>分章最大字数<input type="number" min={500} value={chapterMax} onChange={(event) => setChapterMax(Number(event.target.value))} /></label></div><p className="muted-copy">题材卡提供世界背景与基础设定，叙事卡引入人物关系、职业事件、故事机制与经典桥段。均由你手选，未选不启用。</p></>}
        {step === 3 && <section><h3>模型与能力只读检查</h3><p>{providerSummary}</p><p className="muted-copy">此步骤只读取本地配置，不发送 capability 验证请求，也不会自动选择模型。</p></section>}
        {step === 4 && <><label>世界设定摘要<textarea rows={4} autoFocus value={worldSummary} onChange={(event) => setWorldSummary(event.target.value)} placeholder="只填写已确定的基础事实，可跳过" /></label><label>重要人物（逗号或换行分隔）<input value={characterNames} onChange={(event) => setCharacterNames(event.target.value)} placeholder="可跳过" /></label></>}
        {step === 5 && (mode === "import" ? <><label className="file-field">选择正文文件<input type="file" accept=".txt,.md,.markdown,text/plain,text/markdown" onChange={(event) => void chooseImportFile(event.target.files?.[0] ?? null)} /></label><label>编码<select value={encoding} onChange={(event) => { const nextEncoding = asImportEncoding(event.target.value); setEncoding(nextEncoding); setImportPreview(null); if (importFile) void chooseImportFile(importFile, nextEncoding); }}><option value="auto">自动检测</option><option value="utf-8">UTF-8</option><option value="gb18030">GB18030 / GBK</option><option value="utf-16le">UTF-16 LE</option><option value="utf-16be">UTF-16 BE</option></select></label>{importPreview && <div className="backup-ok"><strong>预览识别 {importPreview.chapter_count} 章，{importPreview.character_count.toLocaleString()} 字</strong><span>{importPreview.chapters.slice(0, 8).map((item) => `${item.title}（${item.characters}字）`).join("、")}</span>{importPreview.warnings.length > 0 && <span>发现 {importPreview.warnings.length} 项结构提示，请在提交前核对。</span>}</div>}</> : <section><h3>第一阶段目标</h3><p>创建后进入工作台，由后端 <code>next_action</code> 决定下一步。初始资料不会启动模型。</p></section>)}
        {step === 6 && <section><h3>确认创建</h3><p>《{title || "未命名作品"}》 · {genre || "未指定题材"} · {genreSelectionMode === "specified" ? `主题材卡：${genreCards.find((card) => card.id === genreCardId)?.name ?? genreCardId}；副题材 ${sideCount} 张；叙事卡 ${narrativeCount} 张` : "未选择题材卡（不自动匹配）"} · {mode === "import" ? `导入 ${importPreview?.chapter_count ?? 0} 章` : "空白作品"}</p><p>将创建初始正式 StoryState；题材卡选择只影响未来创作批次，Provider 调用为零。</p></section>}
        <div className="dialog-actions">
          <button className="secondary-button" type="button" onClick={step === 1 ? close : () => setStep(step - 1)}>{step === 1 ? "取消" : "上一步"}</button>
          <button className="primary-button" type="submit" disabled={busy || localBusy || (step >= 2 && !title.trim()) || ((step === 2 || step === 6) && !validCards) || (step === 5 && mode === "import" && !previewIsUsable(importPreview, title))}>{step < 6 ? (localBusy ? "正在保存…" : "保存并继续") : "创建作品"}</button>
        </div>
      </form>
    </Dialog>
  );
}

function asImportEncoding(value: string): ImportEncoding {
  return ["auto", "utf-8", "gb18030", "utf-16le", "utf-16be"].includes(value)
    ? value as ImportEncoding
    : "auto";
}

function importPreviewBinding(preview: ImportPreviewResponse): SetupImportBinding {
  const selectedEncoding = asImportEncoding(preview.selected_encoding);
  if (selectedEncoding === "auto") throw new Error("导入预览缺少明确编码");
  return {
    preview_id: preview.preview_id,
    upload_sha256: preview.upload_sha256,
    chapter_manifest_sha256: preview.chapter_manifest_sha256,
    selected_encoding: selectedEncoding,
    parser_version: preview.parser_version,
  };
}

function setupImportBinding(payload: ProjectSetupPayload): SetupImportBinding | null {
  return payload.import_preview ?? null;
}

function previewMatchesBinding(preview: ImportPreviewResponse, binding: SetupImportBinding): boolean {
  return preview.preview_id === binding.preview_id
    && preview.upload_sha256 === binding.upload_sha256
    && preview.chapter_manifest_sha256 === binding.chapter_manifest_sha256
    && preview.selected_encoding === binding.selected_encoding
    && preview.parser_version === binding.parser_version;
}

function previewIsUsable(preview: ImportPreviewResponse | null, expectedTitle: string): boolean {
  return preview !== null
    && preview.status === "previewed"
    && preview.title === expectedTitle.trim()
    && !preview.requires_encoding_confirmation
    && Date.parse(preview.expires_at) > Date.now();
}
