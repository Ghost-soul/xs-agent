import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, jsonBody, StableWriteOperationKeys, type GenerationDetail } from "./api";
import { actionName } from "./GenerationProgress";
import { INPUT_TOKEN_LIMIT, TOKEN_LIMIT } from "./generationTokenLimits";

type Preview = { preview_sha256: string; action?: string; all_roles?: boolean; output_limit?: number; input_limit: number; previous_input_limit: number; writer_input_tokens: number | null; spent_cost_cny: string | null; remaining_cost_upper_cny: string; total_cost_upper_cny: string; max_cost_cny: string; previous_max_cost_cny?: string; blockers: string[] };
export function InputRecovery({ batch, base, disabled, onContinued }: { batch: GenerationDetail; base: string; disabled: boolean; onContinued: (value: GenerationDetail) => void }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [limit, setLimit] = useState<number | null>(null);
  const [output, setOutput] = useState(TOKEN_LIMIT);
  const [budget, setBudget] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const writes = useRef(new StableWriteOperationKeys());
  const binding = `${base}:${batch.id}:${batch.state.plan_id}:${batch.state.questions_id}:${batch.state.plan_author_note_id}:${batch.state.input_authorization_id}:${batch.state.candidate_id}:${batch.state.units_id}:${batch.next_action}:${batch.calls?.length}:${disabled}:${batch.input_recovery_available}`;
  const currentBinding = useRef(binding); currentBinding.current = binding;
  const requestSerial = useRef(0);
  const check = useCallback(async (inputLimit: number | null, outputLimit: number, costLimit: string | null, signal?: AbortSignal) => {
    const ticket = ++requestSerial.current;
    const current = () => requestSerial.current === ticket && currentBinding.current === binding;
    setBusy(true); setError(""); setConfirmed(false); setPreview(null);
    try {
      const query = new URLSearchParams({ all_roles: "true", output_limit: String(outputLimit) });
      if (inputLimit !== null) query.set("input_limit", String(inputLimit));
      if (costLimit !== null) query.set("max_cost_cny", costLimit);
      const result = await api<Preview>(`${base}/${batch.id}/input-preview?${query}`, { signal });
      if (current()) { setPreview(result); setLimit(result.input_limit); setBudget(result.max_cost_cny); }
    } catch (e) { if (current()) setError(errorMessage(e)); } finally { if (current()) setBusy(false); }
  }, [base, batch.id, binding]);
  useEffect(() => {
    setPreview(null); setLimit(null); setOutput(TOKEN_LIMIT); setBudget(null); setConfirmed(false); setError(""); setBusy(false);
    if (!batch.input_recovery_available || disabled) return;
    const controller = new AbortController();
    void check(null, TOKEN_LIMIT, null, controller.signal);
    return () => { ++requestSerial.current; controller.abort(); };
  }, [check, disabled, batch.input_recovery_available]);
  if (!batch.input_recovery_available) return null;
  const completeLimits = preview?.all_roles === true && preview.output_limit === output;
  return <section className="generation-panel input-recovery" aria-label="剩余输入与费用"><h3>确认输入与全部角色输出额度并继续创作</h3>
    <p>按二十万输入及全部角色十万输出核算剩余费用。确认后应用于尚未执行的步骤，已完成的方案、正文和调用保留。</p>
    {limit !== null && <label>剩余步骤输入上限<input type="number" min={8000} max={INPUT_TOKEN_LIMIT} value={limit} disabled={busy || disabled} onChange={(e) => { setLimit(Number(e.target.value)); setPreview(null); setConfirmed(false); }} /></label>}
    <label>剩余全部角色输出上限<input type="number" min={4000} max={TOKEN_LIMIT} value={output} disabled={busy || disabled} onChange={(e) => { setOutput(Number(e.target.value)); setPreview(null); setConfirmed(false); }} /></label>
    {budget !== null && <label>阶段总费用上限（含已用费用，元）<input type="number" min={0} max={10000} step="0.01" value={budget} disabled={busy || disabled} onChange={(e) => { setBudget(e.target.value); setPreview(null); setConfirmed(false); }} /></label>}
    <button disabled={busy || disabled} onClick={() => void check(limit, output, budget)}>{busy ? "正在处理…" : "核算剩余输入与费用（不调用模型）"}</button>
    {preview && <><p>当前批次原输入上限：{preview.previous_input_limit.toLocaleString()}；待确认的新输入上限：{preview.input_limit.toLocaleString()} tokens。</p>
      {completeLimits && !preview.blockers.length && <p role="status">新额度预检通过：{preview.action ? actionName(preview.action) : "Writer"} 输入 {preview.writer_input_tokens?.toLocaleString() ?? "待核算"} / {preview.input_limit.toLocaleString()} tokens；所有剩余角色输出上限 {preview.output_limit?.toLocaleString()}。确认后应用新额度并继续。</p>}
      <p>已用 ¥{preview.spent_cost_cny ?? "待确定"}；剩余步骤最多 ¥{preview.remaining_cost_upper_cny}；合计上界 ¥{preview.total_cost_upper_cny}，原阶段预算 ¥{preview.previous_max_cost_cny ?? preview.max_cost_cny}，待确认总预算 ¥{preview.max_cost_cny}。</p>
      {!completeLimits && <p role="alert">后端尚未返回完整的输入与输出额度预览，请加载更新后重新核算。</p>}
      {preview.blockers.map((b) => <p role="alert" key={b}>{b}</p>)}
      {completeLimits && !preview.blockers.length && <><label className="check"><input type="checkbox" disabled={disabled || busy} checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />确认新的输入、全部角色输出额度、阶段总预算与资料外发范围，继续并停在作者审核</label>
        <button className="primary-button" disabled={disabled || busy || !confirmed} onClick={async () => {
          setBusy(true); setError("");
          try { const value = await writes.current.request<GenerationDetail>(`${base}/${batch.id}/input-authorize`, { method: "POST", body: jsonBody({ confirmed: true, input_limit: preview.input_limit, output_limit: preview.output_limit, all_roles: true, max_cost_cny: preview.max_cost_cny, preview_sha256: preview.preview_sha256 }) }); if (currentBinding.current === binding) onContinued(value); }
          catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
        }}>{batch.state.candidate_id ? "确认额度并继续剩余步骤" : "确认额度并从 Writer 继续"}</button></>}
    </>}
    {disabled && <p>请先保存或撤销未保存的方案与正文修改。</p>}{error && <p role="alert">{error}</p>}
  </section>;
}
