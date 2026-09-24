import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { GenerationDetail } from "./api";
import { MemoryRecovery } from "./MemoryRecovery";
import { revalidationBlocker } from "./GenerationProgress";
import { generationJourney } from "./generationFlow";

const mocks = vi.hoisted(() => ({ api: vi.fn(), write: vi.fn() }));
vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), api: mocks.api, StableWriteOperationKeys: class { request = mocks.write; } }));
const call = { id: "failed", action: "memory:2", status: "local_failure", diagnostic: { code: "output_limit_exceeded", visible_characters: 11145 } };
const batch = { id: "batch", status: "needs_attention", memory_recovery_available: true, state: { candidate_id: "body", units_id: "units" }, spec: { stage_mode: "longform-v1" }, artifacts: [], calls: [call] } as unknown as GenerationDetail;
const preview = { preview_sha256: "preview", action: "memory:2", previous_output_limit: 6000, output_limit: 100000, input_limit: 100000, input_tokens: 30390, spent_cost_cny: "1.451187", remaining_cost_upper_cny: "14.4", total_cost_upper_cny: "15.851187", previous_max_cost_cny: "10", max_cost_cny: "20", all_roles: true, blockers: [] };
afterEach(cleanup);
beforeEach(() => { vi.clearAllMocks(); mocks.api.mockResolvedValue(preview); mocks.write.mockResolvedValue({ ...batch, status: "queued" }); });

it("previews without dispatch and requires explicit confirmation of the additional call and output", async () => {
  const continued = vi.fn();
  render(<MemoryRecovery batch={batch} base="/batches" disabled={false} onContinued={continued} />);
  const button = await screen.findByText("确认补全 Memory 并继续");
  expect(button).toBeDisabled(); expect(mocks.write).not.toHaveBeenCalled();
  expect(screen.getByText(/输入计数／上界/)).toHaveTextContent("30,390 / 100,000");
  fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(button);
  await waitFor(() => expect(continued).toHaveBeenCalledOnce());
  expect(mocks.write.mock.calls[0][0]).toBe("/batches/batch/memory-recovery-authorize");
  expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toEqual({ confirmed: true, output_limit: 100000, all_roles: true, max_cost_cny: "20", preview_sha256: "preview" });
});

it("explains the independent amendment scope and confirms only its recovery preview", async () => {
  const amendment = { ...batch, state: { ...batch.state, amendment_authorized_sha256: "amendment" }, calls: [{ ...call, action: "memory_amend" }] };
  mocks.api.mockResolvedValue({ ...preview, scope: "amendment", action: "memory_amend", remaining_cost_upper_cny: "3", total_cost_upper_cny: "3.1", spent_cost_cny: "0.1", max_cost_cny: "10" });
  render(<MemoryRecovery batch={amendment} base="/batches" disabled={false} onContinued={vi.fn()} />);
  await screen.findByRole("checkbox");
  expect(screen.getByText("补全 Memory 并继续修订核验")).toBeVisible();
  expect(screen.getByText(/补全后停在作者审核/)).toBeVisible();
  expect(screen.getByLabelText("本次修订所有剩余角色输出上限")).toHaveValue(100000);
  expect(screen.getByText(/本次修订已用/)).toHaveTextContent("0.1");
  expect(generationJourney(amendment).next).toContain("独立修订费用");
  expect(generationJourney({ ...amendment, memory_recovery_available: false }).next).toContain("本地重验无法补齐");
  expect(mocks.write).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByText("确认补全 Memory 并继续"));
  await waitFor(() => expect(mocks.write).toHaveBeenCalledOnce());
  expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toMatchObject({ output_limit: 100000, max_cost_cny: "10", all_roles: true });
});

it("resumes remaining amendment checks after successful replacement and an explicit pause", async () => {
  const paused = { ...batch, status: "paused", next_action: "checker_amend", memory_recovery_available: false, state: { ...batch.state, amendment_authorized_sha256: "amendment", memory_output_authorization_id: "auth" }, artifacts: [{ id: "auth", payload: { scope: "amendment", action: "memory_amend", failed_call_id: "failed" } }], calls: [{ ...call, action: "memory_amend" }, { id: "replacement", action: "memory_amend", status: "completed" }] };
  const continued = vi.fn(); mocks.api.mockResolvedValue({ ...paused, status: "queued" });
  render(<MemoryRecovery batch={paused} base="/batches" disabled={false} onContinued={continued} />);
  fireEvent.click(screen.getByText("继续已授权的修订核验"));
  await waitFor(() => expect(continued).toHaveBeenCalledOnce());
  expect(mocks.write.mock.calls[0][0]).toBe("/batches/batch/authorize");
});

it("invalidates confirmation when output or source changes and blocks unsaved edits", async () => {
  const props = { batch, base: "/batches", disabled: false, onContinued: vi.fn() };
  const view = render(<MemoryRecovery {...props} />);
  await screen.findByRole("checkbox"); fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.change(screen.getByLabelText("本阶段所有剩余角色输出上限"), { target: { value: "32000" } });
  expect(screen.queryByText("确认补全 Memory 并继续")).toBeNull();
  view.rerender(<MemoryRecovery {...props} disabled />);
  expect(screen.getByText("核算 Memory 补全与剩余费用（不调用模型）")).toBeDisabled();
  expect(mocks.api).toHaveBeenCalledOnce();
  view.rerender(<MemoryRecovery {...props} batch={{ ...batch, state: { ...batch.state, candidate_id: "edited" } }} />);
  expect(await screen.findByRole("checkbox")).not.toBeChecked();
  expect(mocks.write).not.toHaveBeenCalled();
});

it("discards a late preview from a different source and shows budget blockers", async () => {
  let resolve!: (value: unknown) => void;
  mocks.api.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
  const props = { batch, base: "/batches", disabled: false, onContinued: vi.fn() };
  const view = render(<MemoryRecovery {...props} />);
  mocks.api.mockResolvedValueOnce({ ...preview, blockers: ["超过原阶段预算"] });
  view.rerender(<MemoryRecovery {...props} batch={{ ...batch, state: { ...batch.state, units_id: "new" } }} />);
  await screen.findByText("超过原阶段预算");
  await act(async () => resolve(preview));
  expect(screen.getByText("超过原阶段预算")).toBeVisible();
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(mocks.write).not.toHaveBeenCalled();
});

it("suppresses local revalidation even against the old backend when visible text is truncated", () => {
  expect(revalidationBlocker(batch, call)).toContain("截断");
  expect(revalidationBlocker({ ...batch, state: { compiled: ["failed:current-parser"] } }, { ...call, diagnostic: null })).toContain("不能循环重验");
  expect(revalidationBlocker(batch, { ...call, replaced_by_recovery: true })).toContain("已由另一次");
  expect(generationJourney(batch).next).toContain("仅额外调用一次 Memory");
  expect(generationJourney({ ...batch, memory_recovery_available: false }).next).toContain("本地重验无法补齐");
});

it("offers resume for an authorized replacement paused before its first dispatch", async () => {
  const pending = { ...batch, status: "paused", next_action: "memory:2", memory_recovery_available: false, state: { ...batch.state, memory_output_authorization_id: "auth" }, artifacts: [{ id: "auth", payload: { action: "memory:2", failed_call_id: "failed" } }] };
  const continued = vi.fn(); mocks.api.mockResolvedValue({ ...pending, status: "queued" });
  render(<MemoryRecovery batch={pending} base="/batches" disabled={false} onContinued={continued} />);
  expect(mocks.api).not.toHaveBeenCalled();
  expect(generationJourney(pending).next).toContain("尚未发送");
  fireEvent.click(screen.getByText("继续已授权的 Memory 补全"));
  await waitFor(() => expect(continued).toHaveBeenCalledOnce());
  expect(mocks.write.mock.calls[0][0]).toBe("/batches/batch/authorize");
});
