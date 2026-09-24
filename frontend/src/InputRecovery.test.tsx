import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { GenerationDetail } from "./api";
import { InputRecovery } from "./InputRecovery";

const mocks = vi.hoisted(() => ({ api: vi.fn(), write: vi.fn() }));
vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), api: mocks.api, StableWriteOperationKeys: class { request = mocks.write; } }));
const batch = { id: "batch", input_recovery_available: true, state: { plan_id: "plan", questions_id: "q" } } as unknown as GenerationDetail;
const preview = { preview_sha256: "preview", all_roles: true, output_limit: 100000, previous_input_limit: 58000, input_limit: 100000, writer_input_tokens: 63120, spent_cost_cny: "0.48", remaining_cost_upper_cny: "4", total_cost_upper_cny: "4.48", max_cost_cny: "10", blockers: [] };
afterEach(cleanup);
beforeEach(() => { vi.clearAllMocks(); mocks.api.mockResolvedValue(preview); mocks.write.mockResolvedValue({ ...batch, status: "queued" }); });

it("automatically previews saved-plan input but requires explicit fee confirmation to continue", async () => {
  const continued = vi.fn();
  render(<InputRecovery batch={batch} base="/batches" disabled={false} onContinued={continued} />);
  const authorize = await screen.findByText("确认额度并从 Writer 继续");
  expect(authorize).toBeDisabled(); expect(mocks.write).not.toHaveBeenCalled();
  expect(mocks.api).toHaveBeenCalledWith("/batches/batch/input-preview?all_roles=true&output_limit=100000", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(screen.getByRole("status")).toHaveTextContent("新额度预检通过");
  expect(screen.getByRole("status")).toHaveTextContent("63,120 / 100,000");
  fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(authorize);
  await waitFor(() => expect(continued).toHaveBeenCalledOnce());
  expect(mocks.write.mock.calls[0][0]).toBe("/batches/batch/input-authorize");
  expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toEqual({ input_limit: 100000, output_limit: 100000, all_roles: true, max_cost_cny: "10", preview_sha256: "preview", confirmed: true });
});

it("changing the limit or saved plan invalidates the preview and confirmation", async () => {
  const props = { batch, base: "/batches", disabled: false, onContinued: vi.fn() };
  const view = render(<InputRecovery {...props} />);
  await screen.findByRole("checkbox"); fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.change(screen.getByLabelText("剩余步骤输入上限"), { target: { value: "72000" } });
  expect(screen.queryByText("确认额度并从 Writer 继续")).toBeNull();
  fireEvent.click(screen.getByText("核算剩余输入与费用（不调用模型）")); await screen.findByRole("checkbox");
  expect(mocks.api).toHaveBeenLastCalledWith("/batches/batch/input-preview?all_roles=true&output_limit=100000&input_limit=72000&max_cost_cny=10", { signal: undefined });
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  view.rerender(<InputRecovery {...props} batch={{ ...batch, state: { ...batch.state, plan_id: "new-plan" } }} />);
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(await screen.findByRole("checkbox")).not.toBeChecked(); expect(mocks.write).not.toHaveBeenCalled();
});

it("blocks unconfirmed, over-budget and unsaved work", async () => {
  mocks.api.mockResolvedValue({ ...preview, blockers: ["超过原阶段预算"] });
  const view = render(<InputRecovery batch={batch} base="/batches" disabled onContinued={vi.fn()} />);
  expect(screen.getByRole("button")).toBeDisabled();
  expect(mocks.api).not.toHaveBeenCalled();
  view.rerender(<InputRecovery batch={batch} base="/batches" disabled={false} onContinued={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("超过原阶段预算");
  expect(screen.queryByRole("checkbox")).toBeNull(); expect(mocks.write).not.toHaveBeenCalled();
});

it("ignores an older preview after the saved plan changes", async () => {
  let finishOld!: (value: typeof preview) => void;
  mocks.api.mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve; }));
  const props = { batch, base: "/batches", disabled: false, onContinued: vi.fn() };
  const view = render(<InputRecovery {...props} />);
  mocks.api.mockResolvedValue({ ...preview, input_limit: 80000, preview_sha256: "new" });
  view.rerender(<InputRecovery {...props} batch={{ ...batch, state: { ...batch.state, plan_id: "new-plan" } }} />);
  expect(await screen.findByRole("checkbox")).not.toBeChecked();
  await act(async () => finishOld(preview));
  expect(screen.getByLabelText("剩余步骤输入上限")).toHaveValue(80000);
  expect(screen.getByRole("status")).toHaveTextContent("63,120 / 80,000");
  expect(mocks.write).not.toHaveBeenCalled();
});

it("shows preview failure and allows a read-only retry without auto authorizing", async () => {
  mocks.api.mockRejectedValueOnce(new Error("核算暂不可用"));
  render(<InputRecovery batch={batch} base="/batches" disabled={false} onContinued={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("核算暂不可用");
  expect(screen.queryByRole("checkbox")).toBeNull();
  fireEvent.click(screen.getByRole("button"));
  expect(await screen.findByRole("checkbox")).not.toBeChecked();
  expect(mocks.api).toHaveBeenCalledTimes(2); expect(mocks.write).not.toHaveBeenCalled();
});

it("binds all outputs and an increased total budget for an already-written paused stage", async () => {
  mocks.api.mockResolvedValueOnce({ ...preview, blockers: ["预算不足"] });
  const paused = { ...batch, status: "paused", next_action: "reader_early", state: { ...batch.state, candidate_id: "body", units_id: "units" } };
  render(<InputRecovery batch={paused} base="/batches" disabled={false} onContinued={vi.fn()} />);
  await screen.findByText("预算不足");
  expect(screen.queryByRole("checkbox")).toBeNull();
  fireEvent.change(screen.getByLabelText("阶段总费用上限（含已用费用，元）"), { target: { value: "20" } });
  mocks.api.mockResolvedValue({ ...preview, action: "reader_early", max_cost_cny: "20" });
  fireEvent.click(screen.getByText("核算剩余输入与费用（不调用模型）"));
  await screen.findByRole("checkbox");
  expect(screen.getByRole("status")).toHaveTextContent("Reader 首章早读");
  expect(screen.getByLabelText("剩余全部角色输出上限")).toHaveValue(100000);
  expect(mocks.write).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByText("确认额度并继续剩余步骤"));
  await waitFor(() => expect(mocks.write).toHaveBeenCalledOnce());
  expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toMatchObject({ input_limit: 100000, output_limit: 100000, all_roles: true, max_cost_cny: "20" });
});

it("does not authorize an old backend preview that omits remaining output limits", async () => {
  mocks.api.mockResolvedValue({ ...preview, all_roles: undefined, output_limit: undefined });
  render(<InputRecovery batch={batch} base="/batches" disabled={false} onContinued={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("后端尚未返回完整");
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(mocks.write).not.toHaveBeenCalled();
});
