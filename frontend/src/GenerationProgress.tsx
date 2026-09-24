import { useEffect, useState } from "react";
import type { GenerationDetail } from "./api";

export function plannedUnitCount(batch: GenerationDetail): number {
  const plan = batch.artifacts.find((a) => a.id === batch.state.plan_id)?.payload as { scenes?: unknown[] } | undefined;
  return batch.spec.stage_mode === "longform-v1" && plan?.scenes?.length ? plan.scenes.length : batch.spec.unit_limit ?? 1;
}

export function actionName(action: unknown): string {
  if (typeof action !== "string") return "等待下一步";
  const [kind, ordinal] = action.split(":");
  if (ordinal) return ({ write: `Writer 写第 ${ordinal} 单元`, memory: `Memory 提取第 ${ordinal} 单元事实`, chief: `Chief 第 ${ordinal} 次题材对照` } as Record<string, string>)[kind] ?? action;
  return ({ plan: "Chief 设计剧情", write: "Writer 写正文", memory: "Memory 提取事实", checker: "Checker 检查连续性", reader: "Reader 终稿冷读", reader_early: "Reader 首章早读", review: "Chief 题材复核", editor: "Editor 局部编辑", title: "Editor 拟定章名", rewrite: "Writer 改写阶段", amend: "Editor 作者修订", memory_edit: "Memory 编辑后提取", checker_edit: "Checker 编辑后检查", memory_amend: "Memory 修订后提取", checker_amend: "Checker 修订后检查", reader_amend: "Reader 修订后冷读" } as Record<string, string>)[action] ?? action;
}

export function callDiagnostic(call: Record<string, unknown> | undefined) {
  return call?.diagnostic as { code: string; message: string; visible_characters: number } | null | undefined;
}

export function revalidationBlocker(batch: GenerationDetail, call: Record<string, unknown>): string | null {
  if (call.replaced_by_recovery) return "此历史失败已由另一次调用恢复，原响应保留。";
  if (typeof call.revalidation_blocker === "string") return call.revalidation_blocker;
  if (call.can_revalidate === true) return null;
  if (callDiagnostic(call)?.code === "output_limit_exceeded") return "输出已被截断，本地重验无法补齐缺失内容。";
  if ((batch.state.compiled as string[] | undefined)?.some((mark) => mark.startsWith(`${call.id}:`))) return "此响应已处理，不能循环重验。";
  if (call.can_revalidate === false || !["response_saved", "local_failure"].includes(String(call.status)) || ["running", "queued", "adopted", "archived", "outcome_uncertain"].includes(batch.status)) return "此响应当前不可本地重验。";
  return null;
}

export function pendingMemoryRecovery(batch: GenerationDetail): boolean {
  const receipt = batch.artifacts.find((a) => a.id === batch.state.memory_output_authorization_id)?.payload as { action?: string; failed_call_id?: string } | undefined;
  return batch.status === "paused" && !!receipt?.action && receipt.action === batch.next_action
    && batch.calls.some((c) => c.id === receipt.failed_call_id && c.status === "local_failure")
    && batch.calls.every((c) => c.action !== receipt.action || c.id === receipt.failed_call_id);
}

const callLabels: Record<string, string> = { executing: "进行中", completed: "已完成", response_saved: "响应已保存，正在处理", local_failure: "未完成，需处理", outcome_uncertain: "结果待核对", uncertain_closed: "已记录核对结论", failed: "调用失败" };
export function callStatusName(status: unknown): string {
  return callLabels[String(status)] ?? String(status);
}
export function chronologicalCalls(batch: GenerationDetail) {
  return [...batch.calls].sort((a, b) => String(a.started_at ?? "").localeCompare(String(b.started_at ?? "")));
}

export function GenerationProgress({ batch, compact = false }: { batch: GenerationDetail; compact?: boolean }) {
  const calls = chronologicalCalls(batch);
  const executing = calls.find((c) => c.status === "executing");
  const last = calls.at(-1);
  const failed = last && ["local_failure", "failed", "outcome_uncertain"].includes(String(last.status)) ? last : undefined;
  const diagnostic = callDiagnostic(failed);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!executing) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [executing?.id]);
  const find = (kind: string) => batch.artifacts.find((a) => a.id === batch.state[`${kind}_id`]) as { payload: Record<string, unknown> } | undefined;
  const units = (find("units")?.payload.items ?? []) as { memory_id?: string; complete?: boolean }[];
  const candidate = find("candidate");
  const written = batch.spec.stage_mode === "longform-v1" ? units.filter((u) => u.complete !== false).length : Number(!!candidate && candidate.payload.complete !== false);
  const handoffs = batch.spec.stage_mode === "longform-v1" ? units.filter((u) => u.memory_id).length : Number(!!find("memory"));
  const blockers = (batch.snapshot.blockers ?? []) as string[];
  let headline: string;
  if (executing) headline = `正在进行：${actionName(executing.action)}`;
  else if (batch.status === "draft") headline = blockers.length ? "尚未开始：本地预检未通过" : "尚未开始：等待费用确认与授权";
  else if (batch.status === "queued" || batch.status === "running") headline = `准备进行：${actionName(batch.next_action)}`;
  else if (batch.status === "outcome_uncertain") headline = `结果待核对：${actionName(failed?.action ?? batch.next_action)}`;
  else if (batch.status === "adopted") headline = "本阶段已正式采用";
  else if (failed && ["advisory-v1", "logic-v1"].includes(batch.spec.feedback_policy ?? "") && /^(checker|reader)/.test(String(failed.action))) headline = "正文已保存：可选反馈未完成";
  else if (failed) headline = `已暂停：${actionName(failed.action)}`;
  else if (batch.status === "needs_attention" && batch.input_recovery_available) headline = "等待确认新的输入额度与费用";
  else if (batch.status === "awaiting_plan") headline = "等待作者确认或调整故事方案";
  else if (batch.status === "paused") headline = `已暂停；下一步：${actionName(batch.next_action)}`;
  else if (batch.status === "archived") headline = "历史记录";
  else if (find("candidate") && ["advisory-v1", "logic-v1"].includes(batch.spec.feedback_policy ?? "")) headline = "等待作者读稿：正文已保存";
  else if (find("candidate") && find("review")) headline = "等待作者审核：正文与反馈已保存";
  else headline = "等待作者审阅或处理问题";
  const knownCost = calls.reduce((sum, c) => sum + (c.actual_cost_cny == null ? 0 : Number(c.actual_cost_cny)), 0);
  const unknownCost = calls.some((c) => c.actual_cost_cny == null);
  const seconds = executing ? Math.max(0, Math.floor((now - Date.parse(String(executing.started_at))) / 1000)) : 0;
  const transport = batch.state.transport as { call_id?: string; received_bytes?: number; last_received_at?: string } | undefined;
  return <section className="generation-progress" aria-label="当前创作进度">
    <h3 aria-live="polite">{headline}</h3>
    <p>剧情方案：{find("plan") ? "已保存" : "未完成"}；已写 {written} / {plannedUnitCount(batch)} 个完整单元{plannedUnitCount(batch) !== (batch.spec.unit_limit ?? 1) && `（授权上限 ${batch.spec.unit_limit}）`}；事实接力 {handoffs} 个。</p>
    {units.some((u) => u.complete === false) && <p>另有未完成单元，其可见正文已保留。</p>}
    {!compact && <p>Reader 反馈：{find("review") ? "已保存，供参考" : ["advisory-v1", "logic-v1"].includes(batch.spec.feedback_policy ?? "") && !batch.spec.enable_reader ? "未开启，不影响读稿采用" : "尚未完成"}；章节划分：{find("segments") ? "已有当前分段" : "尚未生成"}；正式采用：{batch.status === "adopted" ? "已完成" : "未发生"}。</p>}
    <p>已发起 {calls.length} 次调用，已记录费用 ¥{knownCost.toFixed(4)}{unknownCost ? "（另有调用费用待确认）" : ""}。</p>
    {executing && <p>本次已等待 {Number.isFinite(seconds) ? seconds : 0} 秒。{transport && transport.call_id === executing.id && transport.last_received_at ? `最近收到数据：${new Date(transport.last_received_at).toLocaleTimeString()}，累计 ${transport.received_bytes ?? 0} 字节。` : "尚无可用的接收进度。"}接收数据不代表该步骤已完成。</p>}
    {diagnostic ? <p role="alert">{diagnostic.message}</p> : typeof batch.state.message === "string" && (batch.status === "needs_attention" && batch.input_recovery_available ? <details><summary>上次暂停原因（原额度）</summary><p>{batch.state.message}</p></details> : <p>{batch.state.message}</p>)}
    {!compact && !!calls.length && <details><summary>查看已执行步骤</summary><ol>{calls.map((c) => <li key={String(c.id)}>{actionName(c.action)} · {callLabels[String(c.status)] ?? String(c.status)}</li>)}</ol></details>}
  </section>;
}
