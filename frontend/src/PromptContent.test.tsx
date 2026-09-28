import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { PromptContent } from "./PromptContent";

afterEach(cleanup);
it("defaults to complete Chinese labels and switches back to byte-for-byte original text", () => {
  const original = '标题\r\n {"maximum_new_characters":3,"unknown":"<script>内容</script>"}\n';
  const { container } = render(<PromptContent text={original} label="任务输入" />);
  expect(screen.getByLabelText("任务输入")).toHaveTextContent("新增人物上限（maximum_new_characters）");
  expect(container.querySelector("script")).toBeNull();
  fireEvent.click(within(screen.getByRole("group", { name: "任务输入阅读方式" })).getByText("原文对照"));
  expect(screen.getByLabelText("任务输入").textContent).toBe(original);
});
