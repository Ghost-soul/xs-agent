import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("original prompts and failed responses are readable and downloadable without writes", async ({ page, context }, testInfo) => {
  const unexpected: string[] = [];
  const reads: string[] = [];
  const spec = { workflow: "novel-run-v1", stage_mode: "longform-v1", unit_limit: 5, writing_policy: "guided-v1", feedback_policy: "logic-v1", base_version_id: "v", focus_card_id: "girls_love_gl", direction: "具体的选择与回应", character_ids: [], profile_id: "fixture", chief_model: "m", writer_model: "m", max_cost_cny: "10" };
  const batch = { id: "batch", project_id: "p", status: "needs_attention", revision: "genre-led-longform-v1", spec, preview_sha256: "sha", next_action: null, snapshot: { maximum_calls: 12, blockers: [] }, state: { message: "Memory 报告未通过本地解析，原始响应已保存。" }, artifacts: [], calls: [
    { id: "chief", action: "plan", status: "completed", started_at: "2026-09-23T07:23:00Z", input_tokens: 54000, actual_cost_cny: "0.28" },
    { id: "writer", action: "write:1", status: "completed", started_at: "2026-09-23T07:24:00Z", input_tokens: 33000, actual_cost_cny: "0.24" },
    { id: "memory", action: "memory:1", status: "local_failure", started_at: "2026-09-23T07:25:00Z", input_tokens: 28000, actual_cost_cny: "0.29", can_revalidate: false, revalidation_blocker: "此响应已处理，原文保留。" },
  ] };
  const system = "你是 Memory。只提取正文已发生的事实。\n保留人物互动与回应，不把未来计划写成事实。";
  const user = '{"任务":"提取本单元事实","正文":"完整的原始输入","长上下文":"' + "原始资料，保持全文。".repeat(16000) + '最终原文标记"}';
  const output = '{\n  "position_paragraph_ids": ["正文:1", "正文:30"],\n  "outcome": "两人约定再见，保留原始回应。"\n}';
  const receipt = { status: "local_failure", request: { model_request: { model: "saved-memory-model", system_prompt: system, user_prompt: user }, wire_body: JSON.stringify({ model: "saved-memory-model", messages: [{ role: "system", content: system }, { role: "user", content: user }], max_tokens: 100000 }) }, response: { text: output, raw_response: 'data: {"原始响应":"保留"}\n\ndata: [DONE]\n', terminal: { finish_reason: "stop", terminal_status: "completed" } } };
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/backend/, "");
    if (route.request().method() !== "GET") { unexpected.push(`${route.request().method()} ${path}`); await route.fulfill({ status: 403 }); return; }
    let value: unknown = [];
    if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "原始 Prompt 查看验证", current_version: 1, created_at: "2026-09-23T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "离线测试模型", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    else if (path.endsWith("/generation-batches/setup")) value = { configuration_revision: "author-intent-v1", context_budget_revision: "focused-v1", output_budget_revision: "chief-output-v1", automation_revision: "stage-auto-v1", base_version_id: "v", version: 1, characters: [], narrative_position: {}, style: { selection_mode: "specified", genre_card_id: "girls_love_gl", secondary_genre_card_ids: [], matched_cards: [{ id: "girls_love_gl", name: "百合" }] } };
    else if (path.endsWith("/generation-batches")) value = [{ id: "batch", direction: spec.direction, status: batch.status }];
    else if (path.endsWith("/generation-batches/batch")) value = batch;
    else if (path.includes("/calls/")) { reads.push(path); value = receipt; }
    else if (!["/api/projects/archived", "/api/local-tasks"].includes(path) && !path.endsWith("/chapters") && !path.endsWith("/versions")) unexpected.push(path);
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/create");
  await page.getByRole("button", { name: "查看各角色 Prompt 与输出", exact: true }).click();
  expect(reads).toEqual([]);
  await page.getByRole("button", { name: "Memory（1）", exact: true }).click();
  const inspector = page.getByRole("region", { name: "Memory 提取第 1 单元事实的原始记录" });
  await expect(inspector.getByLabel("系统 Prompt", { exact: true })).toHaveText(system);
  expect(await inspector.getByLabel("任务 Prompt", { exact: true }).textContent()).toBe(user);
  expect(await inspector.getByLabel("模型原始输出", { exact: true }).textContent()).toBe(output);
  await inspector.screenshot({ path: testInfo.outputPath("agent-input-output.png") });
  await page.getByRole("tab", { name: "任务 Prompt", exact: true }).click();
  expect(await inspector.locator("pre").textContent()).toBe(user);
  await page.getByRole("tab", { name: "模型原始输出", exact: true }).click();
  expect(await inspector.locator("pre").textContent()).toBe(output);
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await inspector.getByRole("button", { name: "复制原文", exact: true }).click();
  await expect(inspector.getByText("已复制模型原始输出原文。")).toBeVisible();
  // Windows clipboard text uses CRLF; the downloaded file below remains byte-exact.
  expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, "\n")).toBe(output);
  const downloadPromise = page.waitForEvent("download");
  await inspector.getByRole("button", { name: "下载原文", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("memory-1-memory-output.txt");
  expect(await readFile((await download.path())!, "utf8")).toBe(output);
  await inspector.scrollIntoViewIfNeeded();
  await inspector.screenshot({ path: testInfo.outputPath("agent-call-inspector.png") });
  await page.getByRole("tab", { name: "实际请求体", exact: true }).click();
  expect(await inspector.locator("pre").textContent()).toBe(receipt.request.wire_body);
  await page.getByRole("tab", { name: "供应商原始响应", exact: true }).click();
  expect(await inspector.locator("pre").textContent()).toBe(receipt.response.raw_response);
  await page.getByRole("button", { name: "Chief（1）", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Chief 设计剧情 · 原始输入与输出" })).toBeVisible();
  await expect(inspector).toHaveCount(0);
  const widths = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(widths[0]).toBeLessThanOrEqual(widths[1] + 1);
  expect(reads).toEqual(["/api/projects/p/generation-batches/batch/calls/memory", "/api/projects/p/generation-batches/batch/calls/chief"]);
  expect(unexpected).toEqual([]);
});
