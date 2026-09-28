import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { WriterOutputSummary, type WriterObservation } from "./WriterOutputSummary";
import { StageScaleProgress } from "./StageScale";
import type { GenerationDetail } from "./api";

afterEach(cleanup);
const observation: WriterObservation = {
  call_id: "saved", ordinal: 1, characters: 1091, saved_characters: 1098,
  target: { min_characters: 7500, max_characters: 10000 }, status: "below",
  percent_of_minimum: 14.5, terminal_marker: "<|eos|>",
  reasoning_effort: null, reasoning_tokens: 1300, call_status: "completed", complete: true,
};

it("separates a completed call from target fulfillment without triggering actions", () => {
  render(<StageScaleProgress batch={{ state: { writer_unit_status: [observation] } } as unknown as GenerationDetail} />);
  expect(screen.getByLabelText("第 1 单元篇幅")).toHaveTextContent("1,091 字，该次请求目标 7,500–10,000 字");
  expect(screen.getByText(/明显偏短/)).toHaveTextContent("14.5%");
  expect(screen.getByText(/不会自动补写/)).toBeVisible();
  expect(screen.queryByRole("button")).toBeNull();
});

it("reports actual reasoning even when the requested switch says none", () => {
  render(<WriterOutputSummary value={{ ...observation, reasoning_effort: "none" }} reasoning />);
  expect(screen.getByText(/1,300 个推理 tokens/)).toHaveTextContent("与关闭推理的请求不一致");
});

it("does not claim reasoning is disabled if the parameter or usage is absent", () => {
  render(<WriterOutputSummary value={{ ...observation, reasoning_tokens: null }} reasoning />);
  expect(screen.getByText(/本次请求未传推理开关，不能确认已关闭/)).toBeVisible();
});

it("does not invent a target for natural length or call partial prose complete", () => {
  render(<WriterOutputSummary value={{ ...observation, target: null, status: "not_requested", call_status: "local_failure", complete: false }} />);
  expect(screen.getByText(/调用尚未完成，收到的片段保留/)).toBeVisible();
  expect(screen.queryByText(/该次请求目标/)).toBeNull();
});

it("distinguishes a small deficit from a severe shortfall", () => {
  render(<WriterOutputSummary value={{ ...observation, percent_of_minimum: 99.9 }} />);
  expect(screen.getByText(/低于目标，达到目标下界的 99.9%/)).toBeVisible();
  expect(screen.queryByText(/明显偏短/)).toBeNull();
});
