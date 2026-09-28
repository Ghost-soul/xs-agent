import { useEffect, useRef, useState } from "react";
import { api, errorMessage, jsonBody, StableWriteOperationKeys, type GenerationDetail } from "./api";

type Preview = { preview_sha256: string; chapters_requiring_position: number[]; notice: string; manifest: { segments: unknown[] } };

export function ChapterArrangement({ batch, base, disabled, refresh }: { batch: GenerationDetail; base: string; disabled: boolean; refresh: () => Promise<void> }) {
  const candidate = batch.artifacts.find((a) => a.id === batch.state.candidate_id);
  const manifest = batch.artifacts.find((a) => a.id === batch.state.segments_id);
  const body = [...String((candidate?.payload as { body?: string })?.body ?? "")];
  const passages = (batch.passages ?? []) as { id: string; start: number; end: number }[];
  const segments = (manifest?.payload as { segments?: { end: number }[] })?.segments ?? [];
  const [ends, setEnds] = useState<string[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const writes = useRef(new StableWriteOperationKeys());
  const binding = `${candidate?.sha256}:${manifest?.sha256}`;
  const current = useRef(binding); current.current = binding;
  useEffect(() => {
    setEnds(passages.filter((p) => segments.slice(0, -1).some((s) => s.end === p.end)).map((p) => p.id));
    setPreview(null); setError("");
  }, [binding]);
  if (!candidate || !manifest || !batch.state.units_finished) return null;
  const payload = { candidate_sha256: candidate.sha256, manifest_sha256: manifest.sha256, paragraph_ends: passages.filter((p) => ends.includes(p.id)).map((p) => p.id) };
  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(""); try { await action(); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  return <details><summary>调整章节：合并相邻章或在自然段末拆分</summary>
    <p>勾选需要保留的章界，取消勾选即可合并。原文、空行和 Writer 单元保持；本次最多 6 章，不调用模型。</p>
    <fieldset disabled={disabled || busy}>
      {passages.filter((p) => p.end < [...body.join("").trimEnd()].length).map((p, i) => <label className="check" key={p.id}><input type="checkbox" checked={ends.includes(p.id)} disabled={!ends.includes(p.id) && ends.length >= 5} onChange={(e) => { setEnds((old) => e.target.checked ? [...old, p.id] : old.filter((id) => id !== p.id)); setPreview(null); }} />在第 {i + 1} 段后分章：{body.slice(Math.max(p.start, p.end - 55), p.end).join("")}</label>)}
      <button onClick={() => void perform(async () => { const at = current.current; const result = await api<Preview>(`${base}/${batch.id}/chapter-arrangement-preview`, { method: "POST", body: jsonBody(payload) }); if (at === current.current) setPreview(result); })}>预览新章节</button>
      {preview && <><p>将形成 {preview.manifest.segments.length} 章。{preview.notice}</p><p>需要作者填写章末现场：{preview.chapters_requiring_position.join("、") || "无"}。本次模型费用 ¥0。</p><button onClick={() => void perform(async () => { await writes.current.request(`${base}/${batch.id}/chapter-arrangement`, { method: "POST", body: jsonBody({ ...payload, preview_sha256: preview.preview_sha256 }) }); setPreview(null); await refresh(); })}>确认新章界并重新核对逐章事实</button></>}
    </fieldset>
    {error && <p role="alert">{error}</p>}
  </details>;
}
