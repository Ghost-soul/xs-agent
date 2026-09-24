import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { GenerationDetail } from "./api";
import { GenerationProgress } from "./GenerationProgress";

afterEach(cleanup);
const batch = { status: "needs_attention", next_action: null, state: {}, spec: { stage_mode: "longform-v1", unit_limit: 6 }, snapshot: { blockers: [] }, artifacts: [], calls: [] } as unknown as GenerationDetail;

describe("generation progress", () => {
  it("shows the actual plan length while keeping the authorization ceiling visible", () => {
    render(<GenerationProgress batch={{ ...batch, status: "paused", spec: { ...batch.spec, unit_limit: 5 }, state: { plan_id: "p" }, artifacts: [{ id: "p", payload: { scenes: [{}, {}, {}] } }], calls: [{ id: "c", action: "plan", status: "completed", actual_cost_cny: "0.0314" }] }} />);
    expect(screen.getByText(/已写 0 \/ 3/)).toHaveTextContent("授权上限 5");
    expect(screen.getByText(/已记录费用/)).toHaveTextContent("¥0.0314");
  });
  it("treats a logic review failure as optional feedback and Reader as disabled", () => {
    render(<GenerationProgress batch={{ ...batch, spec: { ...batch.spec, feedback_policy: "logic-v1", enable_reader: false }, state: { candidate_id: "body" }, artifacts: [{ id: "body", payload: { complete: true } }], calls: [{ id: "c", action: "checker", status: "local_failure" }] }} />);
    expect(screen.getByRole("heading")).toHaveTextContent("正文已保存：可选反馈未完成");
    expect(screen.getByText(/Reader 反馈/)).toHaveTextContent("未开启，不影响读稿采用");
  });
  it("labels the old limit error as history while waiting for new input confirmation", () => {
    render(<GenerationProgress batch={{ ...batch, input_recovery_available: true, state: { message: "63120 超过允许值 58000" } }} />);
    expect(screen.getByRole("heading")).toHaveTextContent("等待确认新的输入额度与费用");
    const prior = screen.getByText("63120 超过允许值 58000");
    expect(prior.closest("details")).not.toHaveAttribute("open");
    expect(prior.closest("details")).toHaveTextContent("上次暂停原因（原额度）");
  });
  it("identifies a failed Chief and does not claim that prose exists", () => {
    render(<GenerationProgress batch={{ ...batch, calls: [{ id: "c", action: "plan", status: "local_failure", actual_cost_cny: "0.199803", diagnostic: { code: "output_limit_exceeded", visible_characters: 0, message: "6000 tokens 用于推理，可见结果为空" } }] }} />);
    expect(screen.getByRole("heading")).toHaveTextContent("已暂停：Chief 设计剧情");
    expect(screen.getByText(/已写 0 \/ 6/)).toHaveTextContent("剧情方案：未完成");
    expect(screen.getByRole("alert")).toHaveTextContent("可见结果为空");
    expect(screen.getByText(/已记录费用/)).toHaveTextContent("¥0.1998");
  });

  it("uses the actual in-flight action and distinguishes an incomplete unit", () => {
    render(<GenerationProgress batch={{ ...batch, status: "running", next_action: "memory:2", state: { plan_id: "p", units_id: "u", transport: { call_id: "c", received_bytes: 200, last_received_at: new Date().toISOString() } }, artifacts: [{ id: "p", payload: {} }, { id: "u", payload: { items: [{ complete: true, memory_id: "m" }, { complete: false }] } }], calls: [{ id: "c", action: "memory:2", status: "executing", started_at: new Date(Date.now() - 10000).toISOString() }] }} />);
    expect(screen.getByRole("heading")).toHaveTextContent("正在进行：Memory 提取第 2 单元事实");
    expect(screen.getByText(/已写 1 \/ 6/)).toHaveTextContent("事实接力 1 个");
    expect(screen.getByText(/本次已等待/)).toHaveTextContent("累计 200 字节");
    expect(screen.getByText(/另有未完成单元/)).toBeTruthy();
    expect(screen.getByText(/另有调用费用待确认/)).toBeTruthy();
  });

  it("orders calls by execution time instead of action slot order", () => {
    render(<GenerationProgress batch={{ ...batch, calls: [{ id: "late", action: "write:2", status: "local_failure", started_at: "2026-09-21T12:02:00Z" }, { id: "early", action: "chief:1", status: "completed", started_at: "2026-09-21T12:01:00Z" }] }} />);
    expect(screen.getByRole("heading")).toHaveTextContent("已暂停：Writer 写第 2 单元");
    expect(screen.getAllByRole("listitem", { hidden: true })[0]).toHaveTextContent("Chief 第 1 次题材对照");
  });
});
