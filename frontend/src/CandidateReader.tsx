import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "./styles/candidate-reader.css";

export type CandidateReading = {
  candidateId: string;
  body: string;
  units: unknown;
  complete: boolean;
  unsaved: boolean;
  adopted: boolean;
};

type ReadingSection = { label: string; body: string };

export function candidateSections(body: string, units: unknown): ReadingSection[] {
  if (!Array.isArray(units) || !units.length) return [];
  // Backend offsets count Unicode code points, unlike JavaScript string.slice.
  const characters = Array.from(body);
  const sections: ReadingSection[] = [];
  let end = 0;
  for (const [index, value] of units.entries()) {
    if (!value || typeof value !== "object") return [];
    const { start, end: nextEnd, ordinal, complete } = value;
    if (!Number.isInteger(start) || !Number.isInteger(nextEnd) || ordinal !== index + 1
      || start < end || nextEnd <= start || nextEnd > characters.length
      || (index === 0 && start !== 0)
      || characters.slice(end, start).join("").trim()) return [];
    sections.push({
      label: `叙事单元 ${ordinal}${complete === false ? "（未完成）" : ""}`,
      body: characters.slice(start, nextEnd).join(""),
    });
    end = nextEnd;
  }
  return end === characters.length ? sections : [];
}

export function CandidateReader({ reading, hasNewerVersion, onClose }: {
  reading: CandidateReading;
  hasNewerVersion: boolean;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState(-1);
  const [fontSize, setFontSize] = useState(20);
  const [dark, setDark] = useState(false);
  const sections = useMemo(() => reading.unsaved ? [] : candidateSections(reading.body, reading.units), [reading]);
  const current = sections[selected] ?? { label: "全文", body: reading.body };

  useEffect(() => {
    const opener = document.activeElement;
    const element = dialog.current!;
    const overflow = document.body.style.overflow;
    element.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      element.close();
      document.body.style.overflow = overflow;
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);

  function selectSection(index: number) {
    setSelected(index);
    if (scroll.current) scroll.current.scrollTop = 0;
  }

  function download() {
    const url = URL.createObjectURL(new Blob([reading.body], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = reading.unsaved ? "候选正文-未保存修改.txt" : "候选正文.txt";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return createPortal(<dialog ref={dialog} className={`candidate-reader${dark ? " candidate-reader-dark" : ""}`}
    aria-labelledby="candidate-reader-title" onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header className="candidate-reader-header">
      <div><span>{reading.unsaved ? "未保存的编辑稿" : reading.adopted ? "已采用稿" : "已保存的候选稿"}</span><h2 id="candidate-reader-title">正文阅读</h2></div>
      <button type="button" onClick={onClose} autoFocus>返回创作</button>
    </header>
    <div className="candidate-reader-toolbar">
      {sections.length > 0 && <label>阅读范围<select value={selected} onChange={(event) => selectSection(Number(event.target.value))}>
        <option value={-1}>全文 · {sections.length} 个单元</option>
        {sections.map((section, index) => <option value={index} key={index}>{section.label}</option>)}
      </select></label>}
      <div className="candidate-reader-font" role="group" aria-label="阅读字号">
        <button type="button" aria-label="减小字号" disabled={fontSize <= 16} onClick={() => setFontSize(fontSize - 2)}>A−</button>
        <span aria-live="polite">{fontSize}px</span>
        <button type="button" aria-label="增大字号" disabled={fontSize >= 28} onClick={() => setFontSize(fontSize + 2)}>A＋</button>
      </div>
      <button type="button" aria-pressed={dark} onClick={() => setDark(!dark)}>夜间阅读</button>
      <button type="button" onClick={download}>下载全文</button>
    </div>
    {!reading.complete && <p className="candidate-reader-note">本次输出尚未完成，以下为已保存部分。</p>}
    {reading.unsaved && <p className="candidate-reader-note">正在预览编辑框中的修改，返回后可继续编辑或保存。</p>}
    {hasNewerVersion && <p className="candidate-reader-note" role="status">正文已有更新，返回后重新打开即可阅读新版。</p>}
    <div className="candidate-reader-scroll" ref={scroll} tabIndex={0} aria-label="正文阅读区">
      <article className="candidate-reader-article">
        <h3>{current.label}</h3>
        <div className="candidate-reader-prose" style={{ fontSize }}>{current.body}</div>
        <p className="candidate-reader-end">{selected < 0 ? "已读至本次正文末尾" : "本单元结束"}</p>
      </article>
    </div>
    {sections.length > 0 && <nav className="candidate-reader-navigation" aria-label="切换叙事单元">
      <button type="button" disabled={selected <= 0} onClick={() => selectSection(selected - 1)}>上一单元</button>
      <span>{selected < 0 ? "全文" : `${selected + 1} / ${sections.length}`}</span>
      <button type="button" disabled={selected >= sections.length - 1} onClick={() => selectSection(selected + 1)}>{selected < 0 ? "逐单元阅读" : "下一单元"}</button>
    </nav>}
  </dialog>, document.body);
}
