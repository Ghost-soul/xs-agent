import type { GenerationDetail } from "./api";
import { callDiagnostic, chronologicalCalls, pendingMemoryRecovery } from "./GenerationProgress";

export type GenerationPane = "plan" | "body" | "review" | "calls";
export type GenerationJourney = { phase: number; tone: "working" | "attention" | "review" | "done"; next: string; pane: GenerationPane; link?: string; editPlan?: boolean };

export function generationJourney(batch: GenerationDetail): GenerationJourney {
  const has = (kind: string) => batch.artifacts.some((a) => a.id === batch.state[`${kind}_id`]);
  const calls = chronologicalCalls(batch);
  const current = calls.find((c) => c.status === "executing") ?? calls.at(-1);
  const action = String(current?.status === "executing" ? current.action : batch.next_action ?? current?.action ?? "plan");
  const phase = action === "plan" ? 1 : /^(write:|memory:|chief:|reader_early$)/.test(action) || ["write", "rewrite", "memory"].includes(action) ? 2 : 3;
  const failed = current && ["local_failure", "failed", "response_saved", "outcome_uncertain", "uncertain_closed"].includes(String(current.status));
  const advisory = ["advisory-v1", "logic-v1"].includes(batch.spec.feedback_policy ?? "");
  if (batch.status === "draft") return { phase: 0, tone: "attention", next: (batch.snapshot.blockers as unknown[])?.length ? "先处理下方预检问题，再重新建立预览；此时不能开始创作。" : "核对本次模型与费用，勾选确认后点击下方授权按钮。内部步骤会自动衔接。", pane: "plan" };
  if (["adopted", "archived"].includes(batch.status)) return { phase: 4, tone: "done", next: batch.status === "adopted" ? "本阶段已采用。可查看正文与审核记录，或点击右上方「新建阶段」。" : "这是历史记录，可查看保存的方案、正文与调用。", pane: has("candidate") ? "body" : "calls" };
  if (["running", "queued"].includes(batch.status)) return { phase, tone: "working", next: "正在自动衔接，无需点击继续。完成后会停在作者审核；需要中止时可点下方暂停。", pane: has("candidate") ? "body" : "plan" };
  if (batch.step_recovery_available && !batch.memory_recovery_available) return { phase, tone: "attention", next: "核对下方恢复位置与费用，确认后从失败步骤继续，已完成成果保留。若结果未知，须先核查原调用并确认可能重复计费；当前不会自动重发。", pane: "calls" };
  if (batch.status === "outcome_uncertain") return { phase, tone: "attention", next: "先在供应商处核对这次调用，再在下方记录核对结论。当前不会自动重发。", pane: "calls" };
  if (failed && callDiagnostic(current)?.code === "obsolete_genre_quota") return { phase: 1, tone: "attention", next: "旧题材配额拦住了已返回的方案。可用下方按钮按新背景策略建立预览，再核对费用；原响应和费用记录保留。", pane: "plan" };
  if (advisory && failed && /^(checker|reader)/.test(String(current?.action)) && has("candidate")) return { phase: 4, tone: "review", next: "正文已保存，可选反馈未完整结束。可以先读稿、核对事实并预览采用；原失败和费用保留，不自动重试。", pane: "body", link: "阅读已保存正文" };
  if (pendingMemoryRecovery(batch)) return { phase: 2, tone: "attention", next: "Memory 补全已授权但尚未发送。可在下方继续这次补全，已写正文保留。", pane: "body" };
  if (batch.memory_recovery_available) return { phase, tone: "attention", next: batch.state.amendment_authorized_sha256 ? "修订后的 Memory 输出被截断。核对下方补全额度与独立修订费用，确认后仅额外调用一次 Memory，再完成已选的剩余修订步骤，停在作者读稿。" : "Memory 输出被截断，已写正文保留。核对下方补全额度与费用，确认后仅额外调用一次 Memory，再继续本阶段。", pane: "body", link: "阅读已保存正文" };
  if (failed && String(current?.action).startsWith("memory") && callDiagnostic(current)?.code === "output_limit_exceeded") return { phase, tone: "attention", next: "Memory 输出被截断，本地重验无法补齐。已写正文保留；当前没有可用的一次补全入口。", pane: "calls", link: "查看截断响应" };
  if (batch.input_recovery_available && !failed) return has("candidate") ? { phase, tone: "attention", next: "方案和已写正文保留。核对下方输入、全部角色输出额度与阶段总预算，确认后继续尚未执行的步骤。", pane: "body" } : { phase: 2, tone: "attention", next: "Chief 方案已保存。核对下方最新输入、全部角色输出额度与费用，确认后从 Writer 继续；已完成的 Chief 不会重做。", pane: "plan", link: "编辑已保存的 Chief 方案", editPlan: true };
  if (batch.status === "needs_attention" && has("plan") && !has("candidate") && !failed) return { phase: 2, tone: "attention", next: `${typeof batch.state.message === "string" ? batch.state.message : "Writer 尚未开始。"} Chief 方案已保存，可先修改故事方向，再核算下方剩余输入与费用。`, pane: "plan", link: "编辑已保存的 Chief 方案", editPlan: true };
  if (failed || batch.status === "failed") {
    const reason = typeof batch.state.message === "string" ? batch.state.message : "本次调用未完成。";
    const retained = has("candidate") ? "已写正文仍保留。" : "本批次尚未生成正文，原有正式正文保留。";
    const handling = current?.can_revalidate === true ? "可点击「纯本地重验」处理已保存响应，不调用模型、不增加费用；成功后仍暂停，待你确认继续。" : has("plan") ? "可查看已保存响应并修改有效计划。" : "请查看已保存响应；若本地处理不可用，可新建阶段预览。";
    return { phase, tone: "attention", next: batch.plan_retry_preview ? "本次设计未完成。可用下方按钮提高 Chief 输出并重新预览，再核对新费用。" : `${reason} ${handling} ${retained}`, pane: "calls", link: batch.plan_retry_preview ? undefined : "查看失败调用与处理入口" };
  }
  if (batch.state.checkpoint_author_required) return { phase: 2, tone: "attention", next: "Chief 的中途对照需要作者调整未写方案。请先查看对照结果并保存修订，再继续阶段。", pane: "plan", link: "查看对照并调整方案", editPlan: true };
  if (["awaiting_plan", "paused"].includes(batch.status)) {
    const questions = batch.artifacts.find((a) => a.id === batch.state.questions_id)?.payload as { items?: { status: string; scope: string }[] } | undefined;
    const pending = questions?.items?.filter((q) => q.status === "pending" && q.scope === "current_unit").length ?? 0;
    return { phase: has("candidate") ? phase : has("plan") ? 1 : phase, tone: "attention", next: pending ? `有 ${pending} 项当前问题待处理。在下方答复或明确委托，再确认自动继续。` : "在下方确认继续，系统会从剩余步骤接上，已完成的调用不重做。", pane: has("candidate") ? "body" : "plan" };
  }
  if (has("candidate")) return { phase: 4, tone: "review", next: advisory ? "正文已保存。先读稿，按需参考反馈，核对事实后即可预览采用；Reader 不是采用条件。" : has("review") ? "本阶段已停止生成。先阅读候选正文，再查看反馈、核对逐章事实并预览采用。" : "正文已保存，终稿反馈尚未完成。先读稿，再到审核页核对缺失检查和可用处理。", pane: "body", link: "阅读候选正文" };
  return { phase, tone: "attention", next: typeof batch.state.message === "string" ? batch.state.message : "当前需要处理，请查看执行记录中的具体原因。", pane: "calls", link: "查看执行记录" };
}
