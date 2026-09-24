import { expect, test } from "@playwright/test";

test("failed Chief shows progress and prepares an unapproved output increase", async ({ page }, testInfo) => {
  const unexpected: string[] = [];
  const writes: Record<string, unknown>[] = [];
  const spec = { workflow: "novel-run-v1", context_policy: "bounded-v1", stage_mode: "longform-v1", unit_limit: 6, chapter_count: 3, base_version_id: "v", focus_card_id: "girls_love_gl", direction: "她主动选择留下", author_boundaries: "不改变人物身份", relationship_scope: "genre-led", character_selection: "chief-auto-v1", character_ids: [], viewpoint: "", relationship_character_ids: [], profile_id: "fixture", chief_model: "m", writer_model: "m", chief_output_limit: 6000, writer_output_limit: 12000, input_limit: 58000, max_cost_cny: "10" };
  let batch = { id: "failed", project_id: "p", spec, status: "needs_attention", revision: "genre-led-longform-v1", preview_sha256: "old", next_action: null as string | null, snapshot: { maximum_cost_cny: "4.428", blockers: [] }, state: {}, artifacts: [], calls: [{ id: "c", action: "plan", status: "local_failure", actual_cost_cny: "0.199803", diagnostic: { code: "output_limit_exceeded", visible_characters: 0, message: "本次达到输出上限 6000 tokens。推理用量 6000 tokens，可见结果为空；本地重验无法补出缺失内容。" } }], plan_retry_preview: { chief_output_limit: 24000, auxiliary_output_limit: 6000, previous_output_limit: 6000 } as Record<string, unknown> | null };
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/backend/, "");
    let value: unknown = [];
    if (route.request().method() === "POST" && path === "/api/projects/p/generation-batches/random-preview") {
      const payload = route.request().postDataJSON(); writes.push(payload);
      batch = { ...batch, id: "new-preview", spec: payload, status: "draft", next_action: "plan", preview_sha256: "new", calls: [], plan_retry_preview: null, snapshot: { ...batch.snapshot, maximum_cost_cny: "4.914" } };
      value = batch;
    } else if (route.request().method() !== "GET") { unexpected.push(path); await route.fulfill({ status: 403 }); return; }
    else if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "进度验证作品", current_version: 1, created_at: "2026-09-21T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "离线模型", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    else if (path.endsWith("/generation-batches/setup")) value = { configuration_revision: "author-intent-v1", context_budget_revision: "focused-v1", output_budget_revision: "chief-output-v1", automation_revision: "stage-auto-v1", base_version_id: "v", version: 1, characters: [], narrative_position: {}, style: { selection_mode: "specified", genre_card_id: "girls_love_gl", secondary_genre_card_ids: [], matched_cards: [{ id: "girls_love_gl", name: "百合" }] } };
    else if (path.endsWith("/generation-batches")) value = [{ id: batch.id, direction: spec.direction, status: batch.status }];
    else if (path.endsWith(`/generation-batches/${batch.id}`)) value = batch;
    else if (!["/api/projects/archived", "/api/local-tasks"].includes(path) && !path.endsWith("/chapters") && !path.endsWith("/versions")) unexpected.push(path);
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/create");
  await expect(page.getByRole("heading", { name: "已暂停：Chief 设计剧情" })).toBeVisible();
  await expect(page.getByText(/剧情方案：未完成；已写 0 \/ 6/)).toBeVisible();
  await expect(page.getByText(/已记录费用 ¥0.1998/)).toBeVisible();
  await expect(page.getByRole("button", { name: "纯本地重验" })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("chief-failure-progress.png"), fullPage: true });
  await page.getByRole("button", { name: "提高 Chief 输出并重新预览（不调用模型）" }).click();
  await expect(page.getByRole("heading", { name: "尚未开始：等待费用确认与授权" })).toBeVisible();
  await expect(page.getByRole("button", { name: "授权并开始阶段创作" })).toBeDisabled();
  expect(writes).toEqual([expect.objectContaining({ chief_output_limit: 100000, writer_output_limit: 100000, auxiliary_output_limit: 100000, narrative_card_ids: [], card_selection_policy: "separate-v1" })]);
  expect(unexpected).toEqual([]);
  const width = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(width[0]).toBeLessThanOrEqual(width[1] + 1);
});
