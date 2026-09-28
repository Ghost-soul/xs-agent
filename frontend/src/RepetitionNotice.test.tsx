import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { GenerationDetail } from "./api";
import { RepetitionNotice, type RepetitionObservation } from "./RepetitionNotice";
import { StageScaleProgress } from "./StageScale";

afterEach(cleanup);
const observation: RepetitionObservation = {
  method: "exact-paragraph-v1", body_sha256: "current", minimum_paragraph_characters: 40,
  characters: 1000, repeated_characters: 400, repeated_paragraphs: 4, percent: 40, groups: 1,
  examples: [{ first_paragraph: 1, repeat_paragraph: 3, occurrences: 5, paragraph_characters: 100 }],
};

it("shows bounded evidence and the non-blocking nature without actions", () => {
  render(<RepetitionNotice value={observation} scope="本次输出内" />);
  expect(screen.getByText(/400 字的重复段落，占 40%/)).toBeVisible();
  fireEvent.click(screen.getByText(/400 字的重复段落，占 40%/));
  expect(screen.getByText(/第 1 段与第 3 段相同/)).toBeVisible();
  expect(screen.getByText(/未检测近似改写/)).toBeVisible();
  expect(screen.getByText(/不影响继续或采用/)).toBeVisible();
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("does not invent a problem for old missing observations or no exact matches", () => {
  const { rerender } = render(<RepetitionNotice scope="本次输出内" />);
  expect(screen.queryByText(/重复段落/)).toBeNull();
  rerender(<RepetitionNotice value={{ ...observation, repeated_characters: 0 }} scope="本次输出内" />);
  expect(screen.queryByText(/重复段落/)).toBeNull();
});

it("reports current stage cross-unit repetition separately from its length target", () => {
  render(<StageScaleProgress batch={{ state: {
    prose_repetition: observation,
    stage_scale_status: { characters: 1000, status: "within", target: { min_characters: 900, max_characters: 1100 } },
  } } as unknown as GenerationDetail} />);
  expect(screen.getByLabelText("阶段篇幅")).toHaveTextContent("处于目标范围");
  expect(screen.getByLabelText("当前阶段正文（包含跨单元）重复提示")).toBeInTheDocument();
});
