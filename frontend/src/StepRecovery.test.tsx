import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { GenerationDetail } from "./api";
import { StepRecovery } from "./StepRecovery";

const mocks = vi.hoisted(() => ({ api: vi.fn(), write: vi.fn() }));
vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), api: mocks.api, StableWriteOperationKeys: class { request = mocks.write; } }));
const batch = { id: "batch", status: "needs_attention", step_recovery_available: true, state: { plan_id: "plan" }, spec: { stage_mode: "longform-v1" }, artifacts: [], calls: [{ id: "failed", action: "write:1", status: "local_failure" }] } as unknown as GenerationDetail;
const preview = { preview_sha256: "sha", failed_call_id: "failed", action: "write:1", model: "grok-4.6", input_limit: 200000, output_limit: 100000, known_cost_cny: "0.031401", unknown_cost_reserve_cny: "0.3", retry_cost_upper_cny: "0.3", remaining_cost_upper_cny: "3.3", total_cost_upper_cny: "3.631401", max_cost_cny: "100", requires_uncertain_confirmation: true, partial_response_characters: 0, blockers: [] };
const props = { batch, base: "/batches", disabled: false, onContinued: vi.fn() };
beforeEach(() => { vi.clearAllMocks(); mocks.api.mockResolvedValue(preview); mocks.write.mockResolvedValue({ ...batch, status: "queued" }); });
afterEach(cleanup);

it("only previews until both budget and uncertain outcome are confirmed", async () => {
  render(<StepRecovery {...props} />);
  const button = await screen.findByText("确认从失败步骤恢复");
  expect(button).toBeDisabled(); expect(mocks.write).not.toHaveBeenCalled();
  fireEvent.click(screen.getByLabelText(/确认一次额外调用/));
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/我已核查原调用/));
  fireEvent.click(button);
  await waitFor(() => expect(props.onContinued).toHaveBeenCalledOnce());
  expect(mocks.write).toHaveBeenCalledOnce();
  expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toEqual({ confirmed: true, uncertain_confirmed: true, max_cost_cny: "100", preview_sha256: "sha" });
});

it("requires only budget confirmation for a known costed failure", async () => {
  mocks.api.mockResolvedValue({ ...preview, requires_uncertain_confirmation: false, partial_response_characters: 100 });
  render(<StepRecovery {...props} />);
  await screen.findByText("确认从失败步骤恢复");
  expect(screen.getAllByRole("checkbox")).toHaveLength(1);
  expect(screen.getByText(/旧片段留在历史记录中/)).toBeVisible();
  fireEvent.click(screen.getByRole("checkbox"));
  expect(screen.getByText("确认从失败步骤恢复")).toBeEnabled();
});

it("invalidates preview and confirmation after budget changes and unsaved edits", async () => {
  const view = render(<StepRecovery {...props} />);
  await screen.findByText("确认从失败步骤恢复");
  fireEvent.click(screen.getByLabelText(/确认一次额外调用/));
  fireEvent.change(screen.getByLabelText("恢复后的总费用上限（元）"), { target: { value: "120" } });
  expect(screen.queryByText("确认从失败步骤恢复")).toBeNull();
  view.rerender(<StepRecovery {...props} disabled />);
  expect(screen.getByText("预览恢复位置与费用（不调用模型）")).toBeDisabled();
  expect(mocks.write).not.toHaveBeenCalled();
});

it("ignores a late response from an old checkpoint", async () => {
  let resolve!: (v: unknown) => void;
  mocks.api.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
  const view = render(<StepRecovery {...props} />);
  mocks.api.mockResolvedValueOnce({ ...preview, blockers: ["恢复预算不足"] });
  view.rerender(<StepRecovery {...props} batch={{ ...batch, state: { plan_id: "new" } }} />);
  await screen.findByText("恢复预算不足");
  await act(async () => resolve(preview));
  expect(screen.getByText("恢复预算不足")).toBeVisible();
  expect(screen.queryByRole("checkbox")).toBeNull();
});

it("resumes a previously authorized recovery paused before dispatch", async () => {
  const paused = { ...batch, status: "paused", next_action: "write:1", state: { step_recovery_authorization_id: "auth" }, artifacts: [{ id: "auth", payload: { action: "write:1", failed_call_id: "failed" } }] } as unknown as GenerationDetail;
  mocks.api.mockResolvedValue({ ...paused, status: "queued" });
  render(<StepRecovery {...props} batch={paused} />);
  expect(mocks.api).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("继续已授权的恢复流程"));
  await waitFor(() => expect(props.onContinued).toHaveBeenCalledOnce());
  expect(mocks.write.mock.calls[0][0]).toBe("/batches/batch/authorize");
});
