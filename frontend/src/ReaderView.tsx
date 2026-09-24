import { useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent } from "react";

import {
  api,
  type BookmarkResponse,
  type ReaderChapterResponse,
  type ReaderManifestChapterResponse,
  type ReaderManifestResponse,
} from "./api";
import {
  copyPlainText,
  formatNovelBodyForCopy,
  formatNovelChapterForCopy,
  formatNovelCollectionForCopy,
} from "./novelText";

type ReaderChapter = ReaderManifestChapterResponse & { body?: string };
type ReaderViewPayload = Omit<ReaderManifestResponse, "chapters"> & { chapters: ReaderChapter[] };

type SavedPosition = {
  chapterId: string;
  revisionId: string;
  bodySha256: string;
  scrollTop: number;
};

type SearchTarget = {
  project_id: string;
  source_kind: string;
  source_id: string;
  source_sha256: string;
  chapter_id: string | null;
  offset: number;
  match_field: "title" | "text";
  query: string;
};

type ReaderPreferences = {
  font: "serif" | "sans";
  fontSize: number;
  lineHeight: number;
  width: number;
  theme: "light" | "dark";
  paragraphGap: number;
};

const defaultPreferences: ReaderPreferences = {
  font: "serif", fontSize: 18, lineHeight: 2.05, width: 760, theme: "light", paragraphGap: 18,
};

export function ReaderView({ projectId, initialChapterId }: { projectId: string; initialChapterId?: string | null }) {
  const [data, setData] = useState<ReaderViewPayload | null>(null);
  const [chapterId, setChapterId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<{ message: string; tone: "success" | "error" } | null>(null);
  const [bookmarkStatus, setBookmarkStatus] = useState<string | null>(null);
  const [bookmarks, setBookmarks] = useState<BookmarkResponse[]>([]);
  const [searchTarget, setSearchTarget] = useState<SearchTarget | null>(null);
  const [preferences, setPreferences] = useState<ReaderPreferences>(readPreferences);
  const scrollRef = useRef<HTMLDivElement>(null);
  const restoreScrollRef = useRef(0);
  const restoreCharacterOffsetRef = useRef<number | null>(null);
  const storageKey = `novel-writer:reader-position:${projectId}`;

  useEffect(() => {
    let active = true;
    setData(null);
    setError(null);
    setSearchTarget(null);
    api<ReaderViewPayload>(`/api/projects/${projectId}/reader/manifest`)
      .then((payload) => {
        if (!active) return;
        const search = readSearchTarget(projectId);
        const searchChapter = search?.chapter_id
          ? payload.chapters.find(
            (item) => item.chapter_id === search.chapter_id && item.body_sha256 === search.source_sha256,
          )
          : null;
        const saved = readSavedPosition(storageKey);
        const restored = payload.chapters.find(
          (item) =>
            item.chapter_id === saved?.chapterId
            && item.revision_id === saved.revisionId
            && item.body_sha256 === saved.bodySha256,
        );
        if (searchChapter && search) {
          restoreCharacterOffsetRef.current = search.offset;
          setSearchTarget(search);
          window.sessionStorage.removeItem("novel-writer:search-target-v1");
        } else if (search?.chapter_id) {
          setBookmarkStatus("搜索结果来源已变化，已打开当前正式正文但未使用陈旧定位。");
          window.sessionStorage.removeItem("novel-writer:search-target-v1");
        }
        restoreScrollRef.current = searchChapter ? 0 : restored ? saved?.scrollTop ?? 0 : 0;
        setData(payload);
        setChapterId(
          searchChapter?.chapter_id
          ?? (initialChapterId && payload.chapters.some((item) => item.chapter_id === initialChapterId) ? initialChapterId : null)
          ?? restored?.chapter_id
          ?? payload.chapters[0]?.chapter_id
          ?? null,
        );
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setError(caught instanceof Error ? caught.message : "连续阅读视图载入失败");
      });
    return () => {
      active = false;
    };
  }, [initialChapterId, projectId, storageKey]);

  useEffect(() => {
    void api<BookmarkResponse[]>(`/api/projects/${projectId}/bookmarks`).then(setBookmarks).catch(() => setBookmarks([]));
  }, [projectId]);

  useEffect(() => {
    window.localStorage.setItem("novel-writer:reader-preferences-v1", JSON.stringify(preferences));
  }, [preferences]);

  const currentIndex = useMemo(
    () => data?.chapters.findIndex((item) => item.chapter_id === chapterId) ?? -1,
    [chapterId, data],
  );
  const currentChapter = currentIndex >= 0 ? data?.chapters[currentIndex] ?? null : null;

  useEffect(() => {
    const navigateWithKeyboard = (event: KeyboardEvent) => {
      if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select, button, [contenteditable=true]")) return;
      const nextIndex = event.key === "ArrowLeft" ? currentIndex - 1 : event.key === "ArrowRight" ? currentIndex + 1 : -1;
      const chapter = data?.chapters[nextIndex];
      if (!chapter) return;
      event.preventDefault();
      selectChapter(chapter);
    };
    window.addEventListener("keydown", navigateWithKeyboard);
    return () => window.removeEventListener("keydown", navigateWithKeyboard);
  }, [currentIndex, data]);

  useEffect(() => {
    if (!currentChapter || currentChapter.body !== undefined) return;
    let active = true;
    api<ReaderChapterResponse>(`/api/projects/${projectId}/reader/chapters/${currentChapter.chapter_id}`)
      .then((chapter) => {
        if (!active) return;
        setData((current) => current ? {
          ...current,
          chapters: current.chapters.map((item) => item.chapter_id === chapter.chapter_id ? { ...item, ...chapter } : item),
        } : current);
      })
      .catch((caught: unknown) => { if (active) setError(caught instanceof Error ? caught.message : "章节正文载入失败"); });
    return () => { active = false; };
  }, [currentChapter?.body, currentChapter?.chapter_id, projectId]);

  useEffect(() => {
    if (!data || currentIndex < 0 || currentChapter?.body === undefined) return;
    const adjacent = [data.chapters[currentIndex - 1], data.chapters[currentIndex + 1]]
      .filter((item): item is ReaderChapter => Boolean(item && item.body === undefined));
    if (adjacent.length === 0) return;
    let active = true;
    void Promise.all(adjacent.map((item) =>
      api<ReaderChapterResponse>(`/api/projects/${projectId}/reader/chapters/${item.chapter_id}`)
    )).then((chapters) => {
      if (!active) return;
      const byId = new Map(chapters.map((item) => [item.chapter_id, item]));
      setData((current) => current ? {
        ...current,
        chapters: current.chapters.map((item) => ({ ...item, ...byId.get(item.chapter_id) })),
      } : current);
    }).catch(() => undefined);
    return () => { active = false; };
  }, [currentChapter?.body, currentIndex, data?.formal_version, projectId]);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container || !currentChapter) return;
    const target = restoreScrollRef.current;
    restoreScrollRef.current = 0;
    const frame = window.requestAnimationFrame(() => {
      const characterOffset = restoreCharacterOffsetRef.current;
      restoreCharacterOffsetRef.current = null;
      if (characterOffset !== null && currentChapter.body) {
        const maximum = Math.max(0, container.scrollHeight - container.clientHeight);
        container.scrollTop = maximum * Math.min(1, characterOffset / currentChapter.body.length);
      } else container.scrollTop = target;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [currentChapter?.chapter_id, currentChapter?.revision_id]);

  function selectChapter(nextChapter: ReaderChapter, restoreScroll = 0) {
    restoreScrollRef.current = restoreScroll;
    setCopyStatus(null);
    setSearchTarget(null);
    setChapterId(nextChapter.chapter_id);
  }

  async function copyText(value: string, successMessage: string) {
    try {
      await copyPlainText(value);
      setCopyStatus({ message: successMessage, tone: "success" });
    } catch (caught) {
      const reason = caught instanceof Error && caught.message.trim() ? `：${caught.message}` : "";
      setCopyStatus({ message: `复制失败${reason}`, tone: "error" });
    }
  }

  function savePosition(scrollTop: number) {
    if (!currentChapter) return;
    const position: SavedPosition = {
      chapterId: currentChapter.chapter_id,
      revisionId: currentChapter.revision_id,
      bodySha256: currentChapter.body_sha256,
      scrollTop,
    };
    window.localStorage.setItem(storageKey, JSON.stringify(position));
  }

  async function bookmarkCurrentChapter(
    chapter: ReaderChapter & { body: string },
    formalVersion: number,
  ) {
    try {
      const container = scrollRef.current;
      const maximum = Math.max(1, (container?.scrollHeight ?? 1) - (container?.clientHeight ?? 0));
      const offset = Math.round(chapter.body.length * ((container?.scrollTop ?? 0) / maximum));
      await api(`/api/projects/${projectId}/bookmarks`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          chapter_id: chapter.chapter_id,
          revision_id: chapter.revision_id,
          version_number: formalVersion,
          offset,
          anchor: chapter.body.slice(Math.max(0, offset - 120), offset + 120) || chapter.body.slice(0, 240),
          note: "阅读位置",
        }),
      });
      setBookmarks(await api<BookmarkResponse[]>(`/api/projects/${projectId}/bookmarks`));
      setBookmarkStatus("已保存书签");
    } catch (caught) {
      setBookmarkStatus(caught instanceof Error ? caught.message : "保存书签失败");
    }
  }

  if (error) {
    return <div className="reader-empty" role="alert">{error}</div>;
  }
  if (!data) {
    return <div className="reader-empty">正在载入正式正文…</div>;
  }
  if (!currentChapter) {
    return <div className="reader-empty">当前正式版本还没有可阅读章节。</div>;
  }
  if (currentChapter.body === undefined) {
    return <div className="reader-empty">正在载入当前章节正文…</div>;
  }
  const chapterWithBody = currentChapter as ReaderChapter & { body: string };

  const previous = currentIndex > 0 ? data.chapters[currentIndex - 1] : null;
  const next = currentIndex < data.chapters.length - 1 ? data.chapters[currentIndex + 1] : null;
  const readableChapters = data.chapters.filter(
    (item): item is ReaderChapter & { body: string } => item.body !== undefined,
  );
  const readerStyle = {
    "--reader-font-size": `${preferences.fontSize}px`,
    "--reader-line-height": String(preferences.lineHeight),
    "--reader-width": `${preferences.width}px`,
    "--reader-paragraph-gap": `${preferences.paragraphGap}px`,
  } as CSSProperties;

  return (
    <section className={`reader-view reader-theme-${preferences.theme} reader-font-${preferences.font}`} style={readerStyle}>
      <aside className="reader-directory" aria-label="正式章节目录">
        <header>
          <span>正式状态 v{data.formal_version}</span>
          <strong>{data.title}</strong>
        </header>
        <details className="reader-settings">
          <summary>阅读设置</summary>
          <label>字体<select value={preferences.font} onChange={(event) => setPreferences({ ...preferences, font: event.target.value as ReaderPreferences["font"] })}><option value="serif">宋体</option><option value="sans">黑体</option></select></label>
          <label>字号<input type="range" min="15" max="26" value={preferences.fontSize} onChange={(event) => setPreferences({ ...preferences, fontSize: Number(event.target.value) })} /></label>
          <label>行距<input type="range" min="1.5" max="2.8" step="0.05" value={preferences.lineHeight} onChange={(event) => setPreferences({ ...preferences, lineHeight: Number(event.target.value) })} /></label>
          <label>正文宽度<input type="range" min="560" max="980" step="20" value={preferences.width} onChange={(event) => setPreferences({ ...preferences, width: Number(event.target.value) })} /></label>
          <label>段落间距<input type="range" min="8" max="32" value={preferences.paragraphGap} onChange={(event) => setPreferences({ ...preferences, paragraphGap: Number(event.target.value) })} /></label>
          <label>主题<select value={preferences.theme} onChange={(event) => setPreferences({ ...preferences, theme: event.target.value as ReaderPreferences["theme"] })}><option value="light">浅色</option><option value="dark">深色</option></select></label>
        </details>
        {bookmarks.length > 0 && <details className="reader-bookmarks"><summary>书签（{bookmarks.length}）</summary>{bookmarks.map((bookmark) => { const chapter = data.chapters.find((item) => item.chapter_id === bookmark.chapter_id); const exact = chapter?.revision_id === bookmark.revision_id; return <button type="button" key={bookmark.bookmark_id} disabled={!chapter || !exact} onClick={() => { if (!chapter || !exact) return; restoreCharacterOffsetRef.current = bookmark.offset; selectChapter(chapter); }}><strong>{chapter?.title ?? "已移除章节"}</strong><small>{exact ? bookmark.note : `旧版本 v${bookmark.version_number}`}</small></button>; })}</details>}
        <VirtualChapterDirectory
          chapters={data.chapters}
          currentChapterId={currentChapter.chapter_id}
          selectChapter={selectChapter}
        />
      </aside>

      <div
        className="reader-scroll"
        ref={scrollRef}
        onScroll={(event) => savePosition(event.currentTarget.scrollTop)}
      >
        <article
          className="reader-chapter"
          data-revision-id={chapterWithBody.revision_id}
          data-body-sha256={chapterWithBody.body_sha256}
        >
          <header>
            <span>第 {chapterWithBody.ordinal} 章</span>
            <h2>{searchTarget?.match_field === "title" ? <HighlightedText value={chapterWithBody.title} query={searchTarget.query} /> : chapterWithBody.title}</h2>
            <div className="reader-copy-actions" aria-label="正文复制操作">
              <button
                type="button"
                onClick={() => void copyText(
                  formatNovelChapterForCopy(chapterWithBody, chapterWithBody.ordinal),
                  `第 ${chapterWithBody.ordinal} 章已复制`,
                )}
              >复制本章</button>
              <button
                type="button"
                onClick={() => void copyText(
                  formatNovelCollectionForCopy(readableChapters),
                  `全部 ${readableChapters.length} 章已复制`,
                )}
              >复制全部章节</button>
              <button
                type="button"
                onClick={() => void bookmarkCurrentChapter(chapterWithBody, data.formal_version)}
              >
                保存书签
              </button>
            </div>
            {copyStatus && <p className={`reader-copy-notice ${copyStatus.tone}`} role="status" aria-live="polite">{copyStatus.message}</p>}
            {bookmarkStatus && <p className="reader-copy-notice success" role="status">{bookmarkStatus}</p>}
          </header>
          <div className="reader-body">{searchTarget?.match_field === "text" ? <HighlightedText value={formatNovelBodyForCopy(chapterWithBody.body)} query={searchTarget.query} /> : formatNovelBodyForCopy(chapterWithBody.body)}</div>
          <footer>
            <button
              type="button"
              disabled={!previous}
              onClick={() => previous && selectChapter(previous)}
            >
              {previous ? `← 第${previous.ordinal}章` : "已是第一章"}
            </button>
            <span>{currentIndex + 1} / {data.chapters.length}</span>
            <button
              type="button"
              disabled={!next}
              onClick={() => next && selectChapter(next)}
            >
              {next ? `第${next.ordinal}章 →` : "已读到末章"}
            </button>
          </footer>
        </article>
      </div>
    </section>
  );
}

const directoryRowHeight = 48;
const directoryOverscan = 6;

export function VirtualChapterDirectory({ chapters, currentChapterId, selectChapter }: {
  chapters: ReaderChapter[];
  currentChapterId: string;
  selectChapter: (chapter: ReaderChapter) => void;
}) {
  const listRef = useRef<HTMLElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(480);
  const currentIndex = chapters.findIndex((item) => item.chapter_id === currentChapterId);
  const start = Math.max(0, Math.floor(scrollTop / directoryRowHeight) - directoryOverscan);
  const visibleCount = Math.ceil(viewportHeight / directoryRowHeight) + directoryOverscan * 2;
  const end = Math.min(chapters.length, start + visibleCount);

  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const updateHeight = () => setViewportHeight(list.clientHeight || 480);
    updateHeight();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updateHeight);
    observer?.observe(list);
    return () => observer?.disconnect();
  }, []);

  useEffect(() => {
    const list = listRef.current;
    if (!list || currentIndex < 0) return;
    const rowTop = currentIndex * directoryRowHeight;
    const rowBottom = rowTop + directoryRowHeight;
    if (rowTop < list.scrollTop) list.scrollTop = rowTop;
    else if (rowBottom > list.scrollTop + list.clientHeight) {
      list.scrollTop = rowBottom - list.clientHeight;
    }
  }, [currentIndex]);

  function focusChapter(index: number) {
    const bounded = Math.max(0, Math.min(chapters.length - 1, index));
    const chapter = chapters[bounded];
    if (!chapter) return;
    selectChapter(chapter);
    const list = listRef.current;
    if (list) list.scrollTop = bounded * directoryRowHeight;
    window.requestAnimationFrame(() => {
      listRef.current?.querySelector<HTMLButtonElement>(`button[data-directory-index="${bounded}"]`)?.focus();
    });
  }

  function navigateDirectory(event: ReactKeyboardEvent<HTMLElement>, index: number) {
    if (!(["ArrowUp", "ArrowDown", "Home", "End"] as string[]).includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Home") focusChapter(0);
    else if (event.key === "End") focusChapter(chapters.length - 1);
    else focusChapter(index + (event.key === "ArrowUp" ? -1 : 1));
  }

  return <nav
    ref={listRef}
    className="reader-directory-list"
    aria-label={`章节目录，共 ${chapters.length} 章`}
    onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
  >
    <div className="reader-directory-spacer" style={{ height: chapters.length * directoryRowHeight }}>
      <div className="reader-directory-window" style={{ transform: `translateY(${start * directoryRowHeight}px)` }}>
        {chapters.slice(start, end).map((chapter, offset) => {
          const index = start + offset;
          const active = chapter.chapter_id === currentChapterId;
          return <button
            className={active ? "active" : ""}
            type="button"
            key={chapter.chapter_id}
            data-directory-index={index}
            aria-current={active ? "page" : undefined}
            aria-posinset={index + 1}
            aria-setsize={chapters.length}
            onKeyDown={(event) => navigateDirectory(event, index)}
            onClick={() => selectChapter(chapter)}
          >
            <span>{String(chapter.ordinal).padStart(2, "0")}</span>
            <strong>{chapter.title}</strong>
          </button>;
        })}
      </div>
    </div>
  </nav>;
}

function readSavedPosition(storageKey: string): SavedPosition | null {
  try {
    const value = window.localStorage.getItem(storageKey);
    return value ? JSON.parse(value) as SavedPosition : null;
  } catch {
    return null;
  }
}

function readSearchTarget(projectId: string): SearchTarget | null {
  try {
    const raw = window.sessionStorage.getItem("novel-writer:search-target-v1");
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<SearchTarget>;
    if (
      value.project_id !== projectId
      || typeof value.source_sha256 !== "string"
      || typeof value.offset !== "number"
      || typeof value.query !== "string"
      || !["title", "text"].includes(value.match_field ?? "")
    ) return null;
    return value as SearchTarget;
  } catch {
    return null;
  }
}

function HighlightedText({ value, query }: { value: string; query: string }) {
  const start = value.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());
  if (start < 0 || !query) return value;
  const end = start + query.length;
  return <>{value.slice(0, start)}<mark>{value.slice(start, end)}</mark>{value.slice(end)}</>;
}

function readPreferences(): ReaderPreferences {
  try {
    const stored = window.localStorage.getItem("novel-writer:reader-preferences-v1");
    return stored ? { ...defaultPreferences, ...JSON.parse(stored) as Partial<ReaderPreferences> } : defaultPreferences;
  } catch {
    return defaultPreferences;
  }
}
