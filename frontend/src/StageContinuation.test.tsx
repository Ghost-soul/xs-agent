import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GenerationDetail } from "./api";
import { StageContinuation } from "./StageContinuation";

const write = vi.hoisted(() => vi.fn());
vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), StableWriteOperationKeys: class { request = write; } }));
const question = "谁先表达在意？";
const batch = { id: "b", preview_sha256: "preview", status: "awaiting_plan", spec: { pause_after_plan: true }, state: { plan_id: "p", questions_id: "q" }, calls: [{ status: "completed", action: "plan" }], artifacts: [{ id: "p", sha256: "plan-sha", payload: {} }, { id: "q", sha256: "q-sha", payload: { items: [{ question, status: "pending", scope: "current_unit" }] } }] } as unknown as GenerationDetail;
afterEach(cleanup);
beforeEach(() => { write.mockReset(); write.mockResolvedValue({ ...batch, status: "queued", next_action: "write:1" }); });

describe("stage continuation", () => {
  it("requires explicit delegation then sends one bound continuation request", async () => {
    const onContinued = vi.fn();
    render(<StageContinuation batch={batch} base="/batches" disabled={false} onContinued={onContinued} />);
    const button = screen.getByText("确认并自动继续至审核");
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByLabelText("此项是普通剧情选择，交给 Chief／Writer 自主决定"));
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByLabelText("确认上述答复／委托，自动执行原费用上限内的剩余步骤并停在作者审核"));
    fireEvent.click(button);
    await waitFor(() => expect(onContinued).toHaveBeenCalledTimes(1));
    expect(write).toHaveBeenCalledTimes(1);
    expect(write.mock.calls[0][0]).toBe("/batches/b/continue-stage");
    expect(JSON.parse(write.mock.calls[0][1].body)).toMatchObject({ preview_sha256: "preview", expected_plan_sha256: "plan-sha", expected_questions_sha256: "q-sha", delegated_questions: [question], question_answers: {} });
  });

  it("requires a real answer for a marked blocker and clears confirmation when it changes", () => {
    const actual = { ...batch, artifacts: [batch.artifacts[0], { id: "q", sha256: "q-sha", payload: { items: [{ question, status: "pending", scope: "current_unit", reason: { source: "作者边界", why_blocked: "明确限制有冲突" } }] } }] };
    render(<StageContinuation batch={actual} base="/batches" disabled={false} onContinued={vi.fn()} />);
    expect(screen.queryByLabelText("此项是普通剧情选择，交给 Chief／Writer 自主决定")).toBeNull();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "按正式档案决定" } });
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button")).toBeEnabled();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "新的具体答复" } });
    expect(screen.getByRole("button")).toBeDisabled();
    expect(write).not.toHaveBeenCalled();
  });

  it("does not offer to restart a failed or uncertain call", () => {
    render(<StageContinuation batch={{ ...batch, calls: [{ status: "outcome_uncertain" }] }} base="/batches" disabled={false} onContinued={vi.fn()} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(write).not.toHaveBeenCalled();
  });

  it("continues after a historical Memory failure has a completed replacement", () => {
    render(<StageContinuation batch={{ ...batch, status: "paused", calls: [{ status: "local_failure", replaced_by_recovery: true }, { status: "completed" }] }} base="/batches" disabled={false} onContinued={vi.fn()} />);
    expect(screen.getByText("确认并自动继续至审核")).toBeVisible();
    expect(write).not.toHaveBeenCalled();
  });
});
