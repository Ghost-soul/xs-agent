import { expect, test } from "@playwright/test";

test("editable prompt defaults preview and version controls fit the workspace", async ({ page }, testInfo) => {
  const text = {
    system_text: "你是负责主要情节的 Chief。围绕作者要求、人物选择和实际后果设计完整事件。\n尊重人物各自的声音，给 Writer 留出表达空间。",
    task_template: "一、作者要求\n{{story_task}}\n\n二、完整叙事卡\n{{narrative_design.selected_cards}}\n\n三、正式参考\n{{formal_reference}}\n\n四、世界背景\n{{world_cards}}",
  };
  let catalog = {
    revision: "builtin", engine_system: "来源、证据与输出结构由程序保留。",
    entries: ["chief", "writer"].map((variant) => ({
      variant, label: variant === "chief" ? "Chief 剧情设计" : "Writer 单元写作",
      text, default_text: text, customized: false,
      fields: [
        { key: "story_task", label: "作者任务", required: true },
        { key: "narrative_design.selected_cards", label: "完整叙事卡", required: true },
        { key: "formal_reference", label: "正式参考", required: true },
        { key: "world_cards", label: "世界背景题材卡", required: true },
      ], output_requirement: "仅返回 output_schema 的完整 JSON。",
    })), history: [] as { revision: string; created_at: string; note: string }[],
  };
  const unexpected: string[] = [];
  const writes: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/backend/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/backend/, "");
    let value: unknown = [];
    if (path === "/api/prompt-templates/chief" && request.method() === "PUT") {
      writes.push(path);
      const payload = request.postDataJSON();
      catalog = { ...catalog, revision: "saved", entries: catalog.entries.map((entry) => entry.variant === "chief" ? { ...entry, text: payload.template, customized: true } : entry) };
      value = catalog;
    } else if (path === "/api/prompt-templates/preview" && request.method() === "POST") {
      writes.push(path);
      value = { system_prompt: request.postDataJSON().template.system_text,
        task_prompt: "作者要求\n安排一次调查事件。\n\n【程序输出与来源合同】\n完整字段结构保持。",
        input_count: 6400, counting_method: "utf8-byte-upper-bound", blockers: [], omitted_optional: [],
        source_action: "plan", source_description: "使用所选批次的冻结资料；没有调用模型。", engine_contract: {},
      };
    } else if (request.method() !== "GET") { unexpected.push(path); await route.abort(); return; }
    else if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "Prompt 模板验证", current_version: 1, created_at: "2026-09-25T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/prompt-templates") value = catalog;
    else if (path.endsWith("/generation-batches")) value = [{ id: "b", direction: "调查遗失的信件", created_at: "2026-09-25T00:00:00Z" }];
    else if (!["/api/projects/archived", "/api/local-tasks", "/api/provider-profiles"].includes(path) && !path.endsWith("/chapters") && !path.endsWith("/versions")) unexpected.push(path);
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/settings?tab=prompts");
  await expect(page.getByRole("heading", { name: "Prompt 模板", exact: true })).toBeVisible();
  await expect(page.getByLabel("系统 Prompt 默认文本")).toHaveValue(text.system_text);
  await page.getByLabel("系统 Prompt 默认文本").fill(text.system_text + "\n作者手动补充：接续已发生事件的后果。");
  await page.getByRole("button", { name: "世界背景题材卡（必要）" }).click();
  await expect(page.getByLabel("任务 Prompt 模板")).toHaveValue(text.task_template);
  await page.getByLabel("任务 Prompt 模板").fill(text.task_template + "\n{{ world_cards }}");
  await page.getByRole("button", { name: "保存为默认", exact: true }).click();
  const feedback = page.getByLabel("模板保存反馈");
  await expect(feedback.getByRole("alert")).toContainText("重复出现 2 次");
  await expect(feedback.getByRole("alert")).toBeInViewport();
  expect(writes).toEqual([]);
  await feedback.screenshot({ path: testInfo.outputPath("save-validation.png") });
  await page.getByRole("button", { name: "定位问题字段 world_cards" }).click();
  await expect(page.getByLabel("任务 Prompt 模板")).toBeFocused();
  await page.keyboard.press("Backspace");
  await expect(page.getByLabel("任务 Prompt 模板")).toHaveValue(text.task_template + "\n");
  await page.locator(".prompt-workspace").screenshot({ path: testInfo.outputPath("prompt-editor.png") });
  await page.getByRole("button", { name: "预览当前草稿（不调用模型）" }).click();
  await expect(page.getByText(/并非实际 tokens/)).toBeVisible();
  await page.locator(".prompt-preview").screenshot({ path: testInfo.outputPath("prompt-preview.png") });
  await page.getByRole("button", { name: "保存为默认", exact: true }).click();
  await expect(page.getByText(/已保存。新建预览/)).toBeVisible();
  const widths = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(widths[0]).toBeLessThanOrEqual(widths[1] + 1);
  expect(writes).toEqual(["/api/prompt-templates/preview", "/api/prompt-templates/chief"]);
  expect(unexpected).toEqual([]);
  expect(errors).toEqual([]);
});
