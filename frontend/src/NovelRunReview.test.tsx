import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { GenerationDetail } from "./api";
import { NovelRunReview } from "./NovelRunReview";

const mocks = vi.hoisted(() => ({ write: vi.fn() }));
vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), StableWriteOperationKeys: class { request = mocks.write; } }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const batch = { id: "stage", status: "needs_attention", state: { candidate_id: "draft" }, artifacts: [{ id: "draft", sha256: "a".repeat(64), payload: { body: "完整正文。" } }], passages: [{ id: "p1", start: 0, end: 5 }] } as unknown as GenerationDetail;

it("does not turn an optional feedback change into an authorization or accept a late preview", async () => {
  let resolve!: (value: unknown) => void;
  mocks.write.mockReturnValue(new Promise((done) => { resolve = done; }));
  render(<NovelRunReview batch={batch} base="/batches" onRefresh={vi.fn()} disabled={false} />);
  expect(screen.queryByLabelText("修订后听取 Reader 阅读感受")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("修改或核验意图"), { target: { value: "更新事实" } });
  fireEvent.click(screen.getByText("预览修订与核验费用"));
  fireEvent.click(screen.getByLabelText("修订后核对逻辑矛盾"));
  resolve({ preview_sha256: "old", slots: ["memory_amend", "checker_amend"], maximum_cost_cny: "2", input_limit: 200000, output_limit: 100000, feedback_policy: "logic-v1", enable_checker: true, enable_reader: false });
  await waitFor(() => expect(screen.getByText("预览修订与核验费用")).not.toBeDisabled());
  expect(screen.queryByText("授权本次修订并核验")).not.toBeInTheDocument();
  expect(mocks.write).toHaveBeenCalledTimes(1);
});

it("does not accept a backend that still forces the old review chain", async () => {
  mocks.write.mockResolvedValue({ preview_sha256: "old", slots: ["memory_amend", "checker_amend", "reader_amend"], maximum_cost_cny: "3", input_limit: 200000, output_limit: 100000 });
  render(<NovelRunReview batch={batch} base="/batches" onRefresh={vi.fn()} disabled={false} />);
  fireEvent.change(screen.getByLabelText("修改或核验意图"), { target: { value: "更新事实" } });
  fireEvent.click(screen.getByText("预览修订与核验费用"));
  expect(await screen.findByText(/反馈选项尚未由后端确认/)).toBeTruthy();
  fireEvent.click(screen.getByLabelText("确认本次修订范围、资料外发与费用"));
  expect(screen.getByText("授权本次修订并核验")).toBeDisabled();
});

it("amendment preview never authorizes calls and current unread prose stays explicit", async () => {
  mocks.write.mockResolvedValue({ preview_sha256: "preview", slots: ["memory_amend", "checker_amend"], maximum_cost_cny: "2", input_limit: 200000, output_limit: 100000, feedback_policy: "logic-v1", enable_checker: true, enable_reader: false });
  render(<NovelRunReview batch={batch} base="/batches" onRefresh={vi.fn()} disabled={false} />);
  expect(screen.getByText(/未提供反馈，不影响读稿采用/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("修改或核验意图"), { target: { value: "核验作者修改" } });
  fireEvent.click(screen.getByText("预览修订与核验费用"));
  await waitFor(() => expect(mocks.write).toHaveBeenCalledTimes(1));
  expect(mocks.write.mock.calls[0][0]).toBe("/batches/stage/amendment-preview");
  expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toMatchObject({ input_limit: 200000, output_limit: 100000, writing_policy: "guided-v1", narrative_policy: "causal-v1", feedback_policy: "logic-v1", enable_checker: true, enable_reader: false });
  expect(await screen.findByText("授权本次修订并核验")).toBeDisabled();
  fireEvent.click(screen.getByLabelText("确认本次修订范围、资料外发与费用"));
  expect(screen.getByText("授权本次修订并核验")).not.toBeDisabled();
  fireEvent.change(screen.getByLabelText("修改或核验意图"), { target: { value: "改变意图" } });
  expect(screen.queryByText("授权本次修订并核验")).toBeNull();
});

it("blocks authorization when an older backend cannot bind the requested limits", async () => {
  mocks.write.mockResolvedValue({ preview_sha256: "legacy", slots: ["memory_amend"], maximum_cost_cny: "0.1" });
  render(<NovelRunReview batch={batch} base="/batches" onRefresh={vi.fn()} disabled={false} />);
  fireEvent.change(screen.getByLabelText("修改或核验意图"), { target: { value: "核验" } });
  fireEvent.click(screen.getByText("预览修订与核验费用"));
  expect(await screen.findByText(/后端尚未返回二十万输入／十万输出/)).toBeTruthy();
  fireEvent.click(screen.getByLabelText("确认本次修订范围、资料外发与费用"));
  expect(screen.getByText("授权本次修订并核验")).toBeDisabled();
  expect(mocks.write).toHaveBeenCalledTimes(1);
});
