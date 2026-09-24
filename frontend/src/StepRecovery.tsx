import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, jsonBody, StableWriteOperationKeys, type GenerationDetail } from "./api";
import { actionName } from "./GenerationProgress";

type Preview = {
  preview_sha256: string; failed_call_id: string; action: string; model: string;
  input_limit: number; output_limit: number; known_cost_cny: string;
  unknown_cost_reserve_cny: string; retry_cost_upper_cny: string;
  remaining_cost_upper_cny: string; total_cost_upper_cny: string; max_cost_cny: string;
  requires_uncertain_confirmation: boolean; partial_response_characters: number;
  blockers: string[];
};

export function StepRecovery({ batch, base, disabled, onContinued }: {
  batch: GenerationDetail; base: string; disabled: boolean;
  onContinued: (value: GenerationDetail) => void;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [budget, setBudget] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const writes = useRef(new StableWriteOperationKeys());
  const serial = useRef(0);
  const binding = JSON.stringify([base, batch.id, batch.status, batch.state, batch.calls, disabled]);
  const currentBinding = useRef(binding); currentBinding.current = binding;
  const available = batch.step_recovery_available && !batch.memory_recovery_available;
  const receipt = batch.artifacts.find((a) => a.id === batch.state.step_recovery_authorization_id)?.payload as { action?: string; failed_call_id?: string } | undefined;
  const pending = receipt && batch.status === "paused" && batch.next_action === receipt.action
    && !batch.calls.some((c) => c.retry_of_call_id === receipt.failed_call_id);
  const resumeAmendment = receipt && !!batch.state.amendment_authorized_sha256
    && batch.status === "paused" && !!batch.next_action && !available;
  const check = useCallback(async (limit: string | null, signal?: AbortSignal) => {
    const ticket = ++serial.current;
    const current = () => ticket === serial.current && currentBinding.current === binding;
    setBusy(true); setError(""); setPreview(null); setConfirmed(false); setUncertain(false);
    try {
      const query = limit === null ? "" : `?${new URLSearchParams({ max_cost_cny: limit })}`;
      const value = await api<Preview>(`${base}/${batch.id}/step-recovery-preview${query}`, { signal });
      if (current()) { setPreview(value); setBudget(value.max_cost_cny); }
    } catch (e) { if (current()) setError(errorMessage(e)); }
    finally { if (current()) setBusy(false); }
  }, [base, batch.id, binding]);
  useEffect(() => {
    setPreview(null); setBudget(null); setConfirmed(false); setUncertain(false); setError(""); setBusy(false);
    if (!available || disabled || pending) return;
    const controller = new AbortController();
    void check(null, controller.signal);
    return () => { ++serial.current; controller.abort(); };
  }, [check, available, disabled, pending]);

  if (pending || resumeAmendment) return <section className="generation-panel" aria-label="继续恢复流程">
    <h3>恢复流程已授权，当前已暂停</h3>
    <p>继续已确认的剩余步骤，原失败响应和费用记录保留。</p>
    <button disabled={disabled || busy} onClick={async () => {
      setBusy(true); setError("");
      try {
        await writes.current.request(`${base}/${batch.id}/authorize`, { method: "POST", body: jsonBody({ confirmed: true, preview_sha256: batch.preview_sha256 }) });
        const value = await api<GenerationDetail>(`${base}/${batch.id}`);
        if (currentBinding.current === binding) onContinued(value);
      } catch (e) { if (currentBinding.current === binding) setError(errorMessage(e)); }
      finally { if (currentBinding.current === binding) setBusy(false); }
    }}>继续已授权的恢复流程</button>
    {error && <p role="alert">{error}</p>}
  </section>;
  if (!available) return null;
  return <section className="generation-panel" aria-label="从失败步骤恢复">
    <h3>从失败步骤恢复</h3>
    <p>保留已完成的方案、正文和事实接力。确认后重做失败步骤，再接续本次授权范围内的剩余流程；再次失败仍暂停。</p>
    {budget !== null && <label>恢复后的总费用上限（元）<input type="number" min={0} max={10000} step="0.01" value={budget} disabled={disabled || busy}
      onChange={(e) => { setBudget(e.target.value); setPreview(null); setConfirmed(false); setUncertain(false); }} /></label>}
    <button disabled={disabled || busy} onClick={() => void check(budget)}>{busy ? "正在核算…" : "预览恢复位置与费用（不调用模型）"}</button>
    {preview && <>
      <p>恢复位置：{actionName(preview.action)}；模型 {preview.model}。</p>
      {preview.partial_response_characters > 0 && <p>原响应保留在调用详情。若失败步骤为 Writer，将重新生成该未完成单元，旧片段留在历史记录中，已完成正文保持。</p>}
      <p>已记录 ¥{preview.known_cost_cny}；原调用未知费用预留 ¥{preview.unknown_cost_reserve_cny}；本次额外调用最多 ¥{preview.retry_cost_upper_cny}。</p>
      <p>重做与剩余步骤最多 ¥{preview.remaining_cost_upper_cny}；包含已记录费用和未知费用预留的总上界 ¥{preview.total_cost_upper_cny}；待确认总预算 ¥{preview.max_cost_cny}。</p>
      <p>沿用输入上限 {preview.input_limit.toLocaleString()}、本步骤输出上限 {preview.output_limit.toLocaleString()} tokens。</p>
      {preview.blockers.map((reason) => <p role="alert" key={reason}>{reason}</p>)}
      {!preview.blockers.length && <>
        {preview.requires_uncertain_confirmation && <label className="check"><input type="checkbox" checked={uncertain} disabled={disabled || busy} onChange={(e) => setUncertain(e.target.checked)} />我已核查原调用不再在途，理解原调用可能已计费，重发可能重复计费；未知费用仍按上界预留</label>}
        <label className="check"><input type="checkbox" checked={confirmed} disabled={disabled || busy} onChange={(e) => setConfirmed(e.target.checked)} />确认一次额外调用、资料外发范围及上述总预算，恢复后继续至作者审核</label>
        <button className="primary-button" disabled={disabled || busy || !confirmed || (preview.requires_uncertain_confirmation && !uncertain)} onClick={async () => {
          setBusy(true); setError("");
          try {
            const value = await writes.current.request<GenerationDetail>(`${base}/${batch.id}/step-recovery-authorize`, { method: "POST", body: jsonBody({ confirmed: true, uncertain_confirmed: uncertain, max_cost_cny: preview.max_cost_cny, preview_sha256: preview.preview_sha256 }) });
            if (currentBinding.current === binding) onContinued(value);
          } catch (e) { if (currentBinding.current === binding) setError(errorMessage(e)); }
          finally { if (currentBinding.current === binding) setBusy(false); }
        }}>确认从失败步骤恢复</button>
      </>}
    </>}
    {disabled && <p>请先保存或撤销未保存的修改。</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
