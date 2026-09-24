import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, jsonBody, StableWriteOperationKeys, type GenerationDetail } from "./api";
import { actionName, pendingMemoryRecovery } from "./GenerationProgress";

type Preview = { preview_sha256: string; action: string; input_tokens: number; input_limit: number; previous_output_limit: number; output_limit: number; spent_cost_cny: string; remaining_cost_upper_cny: string; total_cost_upper_cny: string; max_cost_cny: string; previous_max_cost_cny: string; all_roles: boolean; blockers: string[] };

export function MemoryRecovery({ batch, base, disabled, onContinued }: { batch: GenerationDetail; base: string; disabled: boolean; onContinued: (value: GenerationDetail) => void }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [limit, setLimit] = useState<number | null>(null);
  const [budget, setBudget] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const writes = useRef(new StableWriteOperationKeys());
  const amending = !!batch.state.amendment_authorized_sha256;
  const scopeName = amending ? "本次修订" : "本阶段";
  const receipt = batch.artifacts.find((a) => a.id === batch.state.memory_output_authorization_id)?.payload as { action?: string; scope?: string } | undefined;
  const resumeAmendment = batch.status === "paused" && !!batch.next_action && receipt?.scope === "amendment" && batch.calls.some((c) => c.action === receipt.action && c.status === "completed");
  const binding = JSON.stringify([base, batch.id, batch.state.plan_id, batch.state.candidate_id, batch.state.units_id, batch.state.questions_id, batch.state.plan_author_note_id, batch.state.input_authorization_id, batch.state.memory_output_authorization_id, batch.state.amendment_authorized_sha256, batch.memory_recovery_available, disabled]);
  const currentBinding = useRef(binding); currentBinding.current = binding;
  const serial = useRef(0);
  const check = useCallback(async (outputLimit: number | null, costLimit: string | null, signal?: AbortSignal) => {
    const ticket = ++serial.current;
    const current = () => ticket === serial.current && currentBinding.current === binding;
    setBusy(true); setError(""); setPreview(null); setConfirmed(false);
    try {
      const query = new URLSearchParams({ all_roles: "true" });
      if (outputLimit !== null) query.set("output_limit", String(outputLimit));
      if (costLimit !== null) query.set("max_cost_cny", costLimit);
      const value = await api<Preview>(`${base}/${batch.id}/memory-recovery-preview?${query}`, { signal });
      if (current()) { setPreview(value); setLimit(value.output_limit); setBudget(value.max_cost_cny); }
    } catch (e) { if (current()) setError(errorMessage(e)); }
    finally { if (current()) setBusy(false); }
  }, [base, batch.id, binding]);
  useEffect(() => {
    setPreview(null); setLimit(null); setBudget(null); setConfirmed(false); setError(""); setBusy(false);
    if (!batch.memory_recovery_available || disabled) return;
    const controller = new AbortController();
    void check(null, null, controller.signal);
    return () => { ++serial.current; controller.abort(); };
  }, [check, disabled, batch.memory_recovery_available]);
  if (pendingMemoryRecovery(batch) || resumeAmendment) return <section className="generation-panel"><h3>{resumeAmendment ? "Memory 已补全，修订核验已暂停" : "Memory 补全已授权，尚未发送"}</h3>
    <p>继续{scopeName}已确认的剩余步骤。</p>
    <button disabled={disabled || busy} onClick={async () => {
      setBusy(true); setError("");
      try {
        await writes.current.request(`${base}/${batch.id}/authorize`, { method: "POST", body: jsonBody({ confirmed: true, preview_sha256: batch.preview_sha256 }) });
        const value = await api<GenerationDetail>(`${base}/${batch.id}`);
        if (currentBinding.current === binding) onContinued(value);
      } catch (e) { if (currentBinding.current === binding) setError(errorMessage(e)); }
      finally { if (currentBinding.current === binding) setBusy(false); }
    }}>{resumeAmendment ? "继续已授权的修订核验" : "继续已授权的 Memory 补全"}</button>{error && <p role="alert">{error}</p>}
  </section>;
  if (!batch.memory_recovery_available) return null;
  return <section className="generation-panel" aria-label="Memory 补全与费用">
    <h3>{amending ? "补全 Memory 并继续修订核验" : "补全 Memory 并继续阶段"}</h3>
    <p>{amending ? "重新提取当前整稿的事实，额外只调用一次 Memory，再完成本次修订的 已选的检查或阅读步骤。补全后停在作者审核，不继续原阶段未写单元。" : "重新生成当前单元的完整事实报告，额外只调用一次 Memory，继续原阶段剩余步骤至作者审核。"}保留 Chief、已写正文和原始响应；{scopeName}所有剩余角色使用新额度，再次失败或结果未知仍暂停。</p>
    {limit !== null && <label>{scopeName}所有剩余角色输出上限<input type="number" min={4000} max={100000} value={limit} disabled={disabled || busy} onChange={(e) => { setLimit(Number(e.target.value)); setPreview(null); setConfirmed(false); }} /></label>}
    {budget !== null && <label>{scopeName}总费用上限（含已用费用，元）<input type="number" min={0} max={10000} step="0.01" value={budget} disabled={disabled || busy} onChange={(e) => { setBudget(e.target.value); setPreview(null); setConfirmed(false); }} /></label>}
    <button disabled={disabled || busy} onClick={() => void check(limit, budget)}>{busy ? "正在核算…" : "核算 Memory 补全与剩余费用（不调用模型）"}</button>
    {preview && <>
      <p>{actionName(preview.action)}：输出额度 {preview.previous_output_limit.toLocaleString()} → {preview.output_limit.toLocaleString()} tokens；输入计数／上界 {preview.input_tokens.toLocaleString()} / {preview.input_limit.toLocaleString()} tokens。</p>
      <p>{scopeName}已用 ¥{preview.spent_cost_cny}；补全与剩余步骤最多 ¥{preview.remaining_cost_upper_cny}；合计上界 ¥{preview.total_cost_upper_cny}；原授权预算 ¥{preview.previous_max_cost_cny}，本次待确认总预算 ¥{preview.max_cost_cny}。</p>
      <p>输入上限 {preview.input_limit.toLocaleString()}，Chief、Writer、Memory、Checker、Reader、Editor 的剩余输出上限均为 {preview.output_limit.toLocaleString()} tokens。</p>
      {preview.blockers.map((reason) => <p role="alert" key={reason}>{reason}</p>)}
      {!preview.blockers.length && <>
        <label className="check"><input type="checkbox" checked={confirmed} disabled={disabled || busy} onChange={(e) => setConfirmed(e.target.checked)} />确认一次额外 Memory 调用、所有剩余角色额度、资料外发范围与{scopeName}总预算</label>
        <button className="primary-button" disabled={disabled || busy || !confirmed} onClick={async () => {
          setBusy(true); setError("");
          try {
            const value = await writes.current.request<GenerationDetail>(`${base}/${batch.id}/memory-recovery-authorize`, { method: "POST", body: jsonBody({ confirmed: true, output_limit: preview.output_limit, all_roles: true, max_cost_cny: preview.max_cost_cny, preview_sha256: preview.preview_sha256 }) });
            if (currentBinding.current === binding) onContinued(value);
          } catch (e) { if (currentBinding.current === binding) setError(errorMessage(e)); }
          finally { if (currentBinding.current === binding) setBusy(false); }
        }}>确认补全 Memory 并继续</button>
      </>}
    </>}
    {disabled && <p>请先保存或撤销未保存的方案与正文修改。</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
