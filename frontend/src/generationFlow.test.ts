import { describe, expect, it } from "vitest";
import type { GenerationDetail } from "./api";
import { generationJourney } from "./generationFlow";

const base = { status: "draft", next_action: "plan", state: {}, spec: { stage_mode: "longform-v1" }, snapshot: { blockers: [] }, artifacts: [], calls: [] } as unknown as GenerationDetail;
describe("stage navigation follows actual work", () => {
  it("explains the saved Chief failure and the available local recovery without claiming prose exists", () => {
    const failed = { ...base, status: "needs_attention", state: { message: "已保存响应，Chief 单元设计数量与冻结上限不符" }, calls: [{ id: "chief", action: "plan", status: "local_failure", can_revalidate: true }] };
    const result = generationJourney(failed);
    expect(result.next).toContain("数量与冻结上限不符");
    expect(result.next).toContain("纯本地重验");
    expect(result.next).toContain("不增加费用");
    expect(result.next).toContain("成功后仍暂停");
    expect(result.next).toContain("本批次尚未生成正文");
    expect(result.next).not.toContain("已写正文仍保留");
    expect(generationJourney({ ...failed, calls: [{ ...failed.calls[0], can_revalidate: false }] }).next).not.toContain("点击「纯本地重验」");
  });
  it.each(["advisory-v1", "logic-v1"] as const)("offers manuscript adoption under %s when Reader is off or optional feedback failed", (policy) => {
    const draft = { ...base, status: "needs_attention", next_action: null, spec: { ...base.spec, feedback_policy: policy, enable_reader: false }, state: { candidate_id: "c" }, artifacts: [{ id: "c" }], calls: [] };
    expect(generationJourney(draft).next).toContain("Reader 不是采用条件");
    expect(generationJourney({ ...draft, calls: [{ action: "reader", status: "local_failure" }] })).toMatchObject({ phase: 4, pane: "body", tone: "review" });
    expect(generationJourney({ ...draft, status: "outcome_uncertain", calls: [{ action: "reader", status: "outcome_uncertain" }] }).pane).toBe("calls");
  });
  it("directs recoverable batches to current input confirmation instead of repeating an old error", () => {
    const result = generationJourney({ ...base, status: "needs_attention", input_recovery_available: true, next_action: null, state: { plan_id: "p", message: "最终输入计数/上界 63120 超过允许值 58000" }, artifacts: [{ id: "p" }], calls: [{ action: "plan", status: "completed" }] });
    expect(result.next).toContain("核对下方最新输入、全部角色输出额度与费用");
    expect(result.next).not.toContain("58000");
    expect(result).toMatchObject({ phase: 2, pane: "plan", editPlan: true });
  });
  it("shows pre-dispatch capacity failure and directs the author to the saved Chief", () => {
    const result = generationJourney({ ...base, status: "needs_attention", next_action: null, state: { plan_id: "p", message: "最终输入计数/上界 63120 超过允许值 58000" }, artifacts: [{ id: "p" }], calls: [{ action: "plan", status: "completed" }] });
    expect(result.next).toContain("63120"); expect(result.next).toContain("Chief 方案已保存");
    expect(result).toMatchObject({ phase: 2, pane: "plan", editPlan: true });
  });
  it.each([
    ["plan", 1], ["write:2", 2], ["memory:2", 2], ["chief:1", 2], ["reader_early", 2],
    ["checker", 3], ["reader", 3], ["memory_amend", 3], ["title", 3], ["rewrite", 2],
  ])("maps %s without treating an early read as the final review", (action, phase) => {
    const result = generationJourney({ ...base, status: "running", next_action: "reader", calls: [{ action, status: "executing" }] });
    expect(result).toMatchObject({ phase, tone: "working" });
    expect(result.next).toContain("无需点击继续");
  });
  it("keeps failures at the actual step even when prose and feedback exist", () => {
    const result = generationJourney({ ...base, status: "needs_attention", next_action: null, state: { candidate_id: "body", review_id: "read" }, artifacts: [{ id: "body" }, { id: "read" }], calls: [{ action: "reader_amend", status: "local_failure" }] });
    expect(result).toMatchObject({ phase: 3, tone: "attention", pane: "calls" });
  });
  it("points a checkpoint pause at plan editing and does not offer normal continuation", () => {
    expect(generationJourney({ ...base, status: "awaiting_plan", state: { checkpoint_author_required: true } })).toMatchObject({ phase: 2, pane: "plan", editPlan: true });
  });
  it("counts only pending questions that block the current unit", () => {
    const result = generationJourney({ ...base, status: "awaiting_plan", state: { plan_id: "p", questions_id: "q" }, artifacts: [{ id: "p" }, { id: "q", payload: { items: [{ status: "pending", scope: "current_unit" }, { status: "answered", scope: "current_unit" }, { status: "pending", scope: "later" }] } }] });
    expect(result.next).toContain("1 项当前问题");
  });
  it("distinguishes uncertain results and missing final feedback from completion", () => {
    expect(generationJourney({ ...base, status: "outcome_uncertain" }).next).toContain("供应商处核对");
    expect(generationJourney({ ...base, status: "needs_attention", state: { candidate_id: "c" }, artifacts: [{ id: "c" }] }).next).toContain("终稿反馈尚未完成");
  });
});
