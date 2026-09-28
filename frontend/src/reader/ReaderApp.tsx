import { useEffect, useRef, useState, type CSSProperties } from "react";

type Book = { id: string; title: string; formal_version: number; chapter_count: number };
type Chapter = { chapter_id: string; revision_id: string; ordinal: number; title: string; char_count: number };
type Manifest = { project_id: string; title: string; version_id: string; formal_version: number; chapters: Chapter[] };
type Body = Chapter & { body: string };
type Hit = Chapter & { snippet: string };

async function get<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`/backend${path}`, { signal, headers: { Accept: "application/json" }, cache: "no-store" });
  if (!response.ok) {
    const result = await response.json().catch(() => null);
    throw new Error(typeof result?.detail === "string" ? result.detail : `读取失败（${response.status}），请稍后刷新`);
  }
  return response.json() as Promise<T>;
}

function message(error: unknown) {
  return error instanceof Error ? error.message : "无法连接阅读服务，请稍后刷新";
}

export function ReaderApp() {
  const [books, setBooks] = useState<Book[] | null>(null);
  const [selected, setSelected] = useState(new URLSearchParams(location.search).get("book") ?? "");
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    void get<Book[]>("/api/projects", controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setBooks(value);
      setSelected((current) => value.some((book) => book.id === current) ? current : "");
    }).catch((caught) => { if (!controller.signal.aborted) setError(message(caught)); });
    return () => controller.abort();
  }, [refresh]);

  const book = books?.find((item) => item.id === selected);
  function openBook(id: string) {
    setSelected(id);
    const url = new URL(location.href);
    if (id) url.searchParams.set("book", id); else url.searchParams.delete("book");
    url.searchParams.delete("chapter");
    history.replaceState(null, "", url);
  }

  return <main className={`cloud-reader ${dark ? "night" : "day"}`}>
    <header className="reader-top">
      <button className="brand" onClick={() => openBook("")} aria-label="返回书架">书间<span>小说阅读</span></button>
      <div className="top-actions">
        {book && <button onClick={() => openBook("")}>书架</button>}
        <button onClick={() => setRefresh((value) => value + 1)}>刷新内容</button>
        <button aria-pressed={dark} onClick={() => setDark(!dark)}>{dark ? "浅色" : "夜读"}</button>
      </div>
    </header>
    {error && <p role="alert" className="notice">{error}</p>}
    {!books && !error && <p className="empty" role="status">正在打开书架…</p>}
    {book ? <BookReader key={`${book.id}:${refresh}`} book={book} /> : books && <section className="library">
      <div className="library-heading"><div><p className="eyebrow">我的书架</p><h1>继续一段故事</h1><p className="subtle">这里是作品当前已采用的正式正文。</p></div>
        <label>查找作品<input type="search" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="输入书名" /></label>
      </div>
      <div className="book-grid">{books.filter((item) => item.title.includes(filter)).map((item, index) => <button className="book-card" key={item.id} onClick={() => openBook(item.id)}>
        <span className={`book-cover shade-${index % 4}`} aria-hidden="true">{item.title.slice(0, 1)}</span>
        <span className="book-info"><strong>{item.title}</strong><span>{item.chapter_count} 章</span><span className="read-link">打开阅读 →</span></span>
      </button>)}</div>
      {books.length === 0 && <p className="empty">数据库中暂无作品。在创作系统导入或采用正文后，点击“刷新内容”。</p>}
      {books.length > 0 && !books.some((item) => item.title.includes(filter)) && <p className="empty">没有找到这个书名。</p>}
    </section>}
  </main>;
}

function BookReader({ book }: { book: Book }) {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [chapterId, setChapterId] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [searchError, setSearchError] = useState("");
  const [searching, setSearching] = useState(false);
  const [more, setMore] = useState(false);
  const [highlight, setHighlight] = useState("");
  const [fontSize, setFontSize] = useState(20);
  const [lineHeight, setLineHeight] = useState(2);
  const [font, setFont] = useState("serif");
  useEffect(() => {
    const controller = new AbortController();
    void get<Manifest>(`/api/projects/${book.id}/reader/manifest`, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setManifest(value);
      const requested = new URLSearchParams(location.search).get("chapter");
      setChapterId(value.chapters.find((item) => item.chapter_id === requested)?.chapter_id ?? value.chapters[0]?.chapter_id ?? "");
    }).catch((caught) => { if (!controller.signal.aborted) setError(message(caught)); });
    return () => controller.abort();
  }, [book.id]);

  useEffect(() => {
    setHits([]); setSearchError(""); setMore(false); setSearching(false);
    if (!manifest || !query.trim()) return;
    const controller = new AbortController();
    setSearching(true);
    const timer = setTimeout(() => {
      const parameters = new URLSearchParams({ q: query.trim(), version_id: manifest.version_id });
      void get<{ results: Hit[]; has_more: boolean }>(`/api/projects/${book.id}/reader/search?${parameters}`, controller.signal).then((result) => {
        if (controller.signal.aborted) return;
        setHits(result.results); setMore(result.has_more);
      }).catch((caught) => { if (!controller.signal.aborted) setSearchError(message(caught)); })
        .finally(() => { if (!controller.signal.aborted) setSearching(false); });
    }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [book.id, manifest, query]);

  function selectChapter(id: string, needle = "") {
    setChapterId(id); setHighlight(needle);
    const url = new URL(location.href);
    url.searchParams.set("chapter", id);
    history.replaceState(null, "", url);
  }
  const currentIndex = manifest?.chapters.findIndex((item) => item.chapter_id === chapterId) ?? -1;
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
      if ((event.target as HTMLElement)?.closest("input,select,textarea,button,[contenteditable]")) return;
      const offset = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      const chapter = offset && manifest?.chapters[currentIndex + offset];
      if (chapter) { event.preventDefault(); selectChapter(chapter.chapter_id); }
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [manifest, currentIndex]);

  if (error) return <p role="alert" className="notice">{error}</p>;
  if (!manifest) return <p role="status" className="empty">正在读取目录…</p>;
  if (!manifest.chapters.length) return <p className="empty">这部作品尚无正式章节。草稿需要在创作系统采用后才会出现在这里。</p>;
  const chapter = manifest.chapters[currentIndex];
  const styles = { "--reading-size": `${fontSize}px`, "--reading-leading": lineHeight, "--reading-font": font === "serif" ? '"Noto Serif SC", "Songti SC", SimSun, serif' : 'system-ui, sans-serif' } as CSSProperties;
  return <section className="reading-layout" style={styles}>
    <aside className="reading-sidebar">
      <h1>{manifest.title}</h1><p className="subtle">共 {manifest.chapters.length} 章</p>
      <details className="reading-controls"><summary>阅读设置</summary>
        <label>字号<input type="range" min="16" max="30" value={fontSize} onChange={(event) => setFontSize(Number(event.target.value))} /></label>
        <label>行距<input type="range" min="1.5" max="2.6" step="0.1" value={lineHeight} onChange={(event) => setLineHeight(Number(event.target.value))} /></label>
        <label>字体<select value={font} onChange={(event) => setFont(event.target.value)}><option value="serif">宋体</option><option value="sans">黑体</option></select></label>
      </details>
      <details className="reading-toc" open><summary>章节目录</summary><nav aria-label="章节目录">{manifest.chapters.map((item) => <button key={item.chapter_id} aria-current={chapterId === item.chapter_id ? "page" : undefined} onClick={() => selectChapter(item.chapter_id)}><span>{item.ordinal}</span>{item.title}</button>)}</nav></details>
      <label className="book-search">搜索本书<input type="search" maxLength={100} placeholder="正文或章节标题" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      {query.trim() && <div className="search-results" aria-live="polite">
        {searching && <p>正在搜索…</p>}{searchError && <p role="alert">{searchError}</p>}
        {!searching && !searchError && !hits.length && <p>没有匹配的正式正文。</p>}
        {hits.map((hit) => <button key={hit.chapter_id} onClick={() => selectChapter(hit.chapter_id, query.trim())}><strong>{hit.title}</strong><span>{hit.snippet}</span></button>)}
        {more && <p>已显示前 50 个匹配章节，请缩小搜索范围。</p>}
      </div>}
    </aside>
    <div className="reading-main">
      {chapter && <ChapterBody key={`${chapterId}:${highlight}`} projectId={book.id} versionId={manifest.version_id} chapter={chapter} highlight={highlight} />}
      <nav className="chapter-navigation" aria-label="翻页">
        <button disabled={currentIndex <= 0} onClick={() => selectChapter(manifest.chapters[currentIndex - 1].chapter_id)}>← 上一章</button>
        <span>{currentIndex + 1} / {manifest.chapters.length}</span>
        <button disabled={currentIndex >= manifest.chapters.length - 1} onClick={() => selectChapter(manifest.chapters[currentIndex + 1].chapter_id)}>下一章 →</button>
      </nav>
    </div>
  </section>;
}

function ChapterBody({ projectId, versionId, chapter, highlight }: { projectId: string; versionId: string; chapter: Chapter; highlight: string }) {
  const [body, setBody] = useState<Body | null>(null);
  const [error, setError] = useState("");
  const article = useRef<HTMLElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    void get<Body>(`/api/projects/${projectId}/reader/chapters/${chapter.chapter_id}?version_id=${versionId}`, controller.signal).then((value) => { if (!controller.signal.aborted) setBody(value); })
      .catch((caught) => { if (!controller.signal.aborted) setError(message(caught)); });
    return () => controller.abort();
  }, [projectId, versionId, chapter.chapter_id]);
  useEffect(() => {
    if (!body) return;
    (article.current?.querySelector("mark") ?? article.current)?.scrollIntoView?.({ block: "start" });
  }, [body]);
  if (error) return <p role="alert" className="notice">{error}</p>;
  if (!body) return <p role="status" className="empty">正在读取正文…</p>;
  return <article ref={article} className="reading-article"><header><p className="eyebrow">第 {body.ordinal} 章</p><h2>{body.title}</h2></header>
    {body.body.split(/\r?\n/u).filter((line) => line.trim()).map((line, index) => <p key={index}>{markText(line, highlight)}</p>)}
  </article>;
}

function markText(text: string, query: string) {
  if (!query) return text;
  const position = text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());
  return position < 0 ? text : <>{text.slice(0, position)}<mark>{text.slice(position, position + query.length)}</mark>{text.slice(position + query.length)}</>;
}
