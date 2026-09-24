import { useState } from "react";

import type { ChapterWorkflow } from "../api";

export function FormalTitleEditor({ chapters, busy, cancel, save }: { chapters: ChapterWorkflow[]; busy: boolean; cancel: () => void; save: (titles: Array<{ chapter_id: string; title: string }>) => Promise<void> }) {
  const [drafts, setDrafts] = useState(() => chapters.map((chapter) => ({ chapter_id: chapter.chapter_id, title: chapter.title })));
  const changed = drafts.some((item, index) => item.title.trim() !== chapters[index]?.title);
  return <section className="formal-title-editor"><strong>批量修改正式章节标题</strong><small>保存会创建新的正式版本；正文、Revision 和 StoryState 保持不变。</small>{drafts.map((item, index) => <label key={item.chapter_id}><span>第 {chapters[index]?.ordinal} 章</span><input aria-label={`第${chapters[index]?.ordinal}章正式标题`} value={item.title} onChange={(event) => setDrafts((current) => current.map((entry, entryIndex) => entryIndex === index ? { ...entry, title: event.target.value } : entry))} /></label>)}<div className="button-row"><button type="button" disabled={busy} onClick={cancel}>取消</button><button className="primary-button" type="button" disabled={busy || !changed || drafts.some((item) => !item.title.trim())} onClick={() => void save(drafts.map((item) => ({ ...item, title: item.title.trim() })))}>保存为新版本</button></div></section>;
}
