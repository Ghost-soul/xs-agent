import { useEffect, useRef, useState } from "react";
import { errorMessage, jsonBody, StableWriteOperationKeys, type GenerationDetail } from "./api";

type Question = { question: string; status: string; scope: string; reason?: { source: string; why_blocked: string } };

export function StageContinuation({ batch, base, disabled, onContinued }: { batch: GenerationDetail; base: string; disabled: boolean; onContinued: (value: GenerationDetail) => void }) {
  const plan = batch.artifacts.find((a) => a.id === batch.state.plan_id);
  const questions = batch.artifacts.find((a) => a.id === batch.state.questions_id);
  const items = ((questions?.payload as { items?: Question[] } | undefined)?.items ?? []).filter((q) => q.status === "pending");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [delegated, setDelegated] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const writes = useRef(new StableWriteOperationKeys());
  const binding = `${batch.id}:${plan?.sha256}:${questions?.sha256}`;
  const confirmation = `${binding}:${batch.preview_sha256}:${JSON.stringify(answers)}:${JSON.stringify(delegated)}`;
  useEffect(() => { setAnswers({}); setDelegated([]); setConfirmed(null); setError(""); }, [binding]);
  if (!plan || !["awaiting_plan", "paused"].includes(batch.status) || batch.state.checkpoint_author_required || batch.calls.some((c) => c.status !== "completed" && !c.replaced_by_recovery) || (batch.input_recovery_available && !items.length)) return null;
  const incomplete = items.some((q) => q.scope === "current_unit" && !answers[q.question]?.trim() && !delegated.includes(q.question));
  const questionField = (q: Question) => <div key={q.question}><label>{q.scope === "later" ? "后续问题（可稍后处理）" : "当前待决"}：{q.question}<textarea disabled={delegated.includes(q.question)} value={answers[q.question] ?? ""} onChange={(e) => { setAnswers({ ...answers, [q.question]: e.target.value }); setConfirmed(null); }} /></label>
      {q.reason ? <p>来源：{q.reason.source}；阻塞原因：{q.reason.why_blocked}</p> : <label className="check"><input type="checkbox" checked={delegated.includes(q.question)} onChange={(e) => { setDelegated(e.target.checked ? [...delegated, q.question] : delegated.filter((x) => x !== q.question)); const next = { ...answers }; delete next[q.question]; setAnswers(next); setConfirmed(null); }} />此项是普通剧情选择，交给 Chief／Writer 自主决定</label>}
    </div>;
  return <section className="generation-panel generation-continuation"><h3>确认后自动衔接至审核</h3>
    <p>{batch.spec.pause_after_plan ? "本批设置了设计后暂停。" : "当前已暂停。"}确认后继续原预算内的剩余步骤。</p>
    {items.length > 0 && <p>真实资料缺口请答复；普通剧情选择可明确委托。已有作者边界和人物事实仍须遵守。</p>}
    {items.filter((q) => q.scope !== "later").map(questionField)}
    {items.some((q) => q.scope === "later") && <details className="generation-later-questions"><summary>后续问题（不影响当前继续，可稍后处理）</summary>{items.filter((q) => q.scope === "later").map(questionField)}</details>}
    <label className="check"><input type="checkbox" checked={confirmed === confirmation} onChange={(e) => setConfirmed(e.target.checked ? confirmation : null)} />确认上述答复／委托，自动执行原费用上限内的剩余步骤并停在作者审核</label>
    {incomplete && <p className="generation-action-hint">请先答复或委托全部“当前待决”问题，后续问题可稍后处理。</p>}
    {!incomplete && confirmed !== confirmation && <p className="generation-action-hint">勾选上方确认后，即可继续。</p>}
    <button className="primary-button" disabled={disabled || busy || incomplete || confirmed !== confirmation} onClick={async () => {
      setBusy(true); setError("");
      try {
        const value = await writes.current.request<GenerationDetail>(`${base}/${batch.id}/continue-stage`, { method: "POST", body: jsonBody({ confirmed: true, preview_sha256: batch.preview_sha256, expected_plan_sha256: plan.sha256, expected_questions_sha256: questions?.sha256 ?? null, question_answers: Object.fromEntries(Object.entries(answers).filter(([, a]) => a.trim())), delegated_questions: delegated }) });
        onContinued(value);
      } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
    }}>确认并自动继续至审核</button>
    {error && <p role="alert">{error}</p>}
  </section>;
}
