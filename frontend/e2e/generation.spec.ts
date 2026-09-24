import { expect, test } from "@playwright/test";

for (const longform of [false, true]) test(`genre entry and saved manuscript remain usable (${longform ? "longform" : "single"})`, async ({ page }, testInfo) => {
  const unexpected: string[] = [];
  const spec = { stage_mode: longform ? "longform-v1" : "single-unit-v1", unit_limit: longform ? 3 : 1, chapter_count: 1, workflow: "novel-run-v1", base_version_id: "base-version", focus_card_id: "girls_love_gl", direction: "让爱情影响行动选择", author_boundaries: "尊重自主性", character_ids: ["a", "b"], viewpoint: "林青", relationship_scope: "explore", relationship_character_ids: ["a", "b"], profile_id: "fixture", chief_model: "m", writer_model: "m", max_cost_cny: "10", target_characters: 4000, input_limit: 58000, timeout_seconds: 600 };
  const candidate = "林青望着她，发现自己在等一个与公务无关的回答。\n\n江月把船票收进衣襟，答应明日同行。";
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/backend/, "");
    let value: unknown = [];
    if (route.request().method() !== "GET") { unexpected.push(path); await route.fulfill({ status: 403 }); return; }
    if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "渡口的第二张船票", current_version: 1, created_at: "2026-09-21T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "已保存的模型", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    else if (path.endsWith("/generation-batches/setup")) value = { configuration_revision: "author-intent-v1", context_budget_revision: "focused-v1", output_budget_revision: "chief-output-v1", automation_revision: "stage-auto-v1", base_version_id: "base-version", version: 1, characters: [{ id: "a", name: "林青" }, { id: "b", name: "江月" }], narrative_position: {}, style: { selection_mode: "specified", genre_card_id: "girls_love_gl", secondary_genre_card_ids: [], matched_cards: [{ id: "girls_love_gl", name: "百合" }] } };
    else if (path.endsWith("/generation-batches")) value = [{ id: "batch", direction: spec.direction, status: "needs_attention" }];
    else if (path.endsWith("/generation-batches/batch")) value = { id: "batch", project_id: "p", status: "needs_attention", spec, revision: longform ? "genre-led-longform-v1" : "genre-led-novel-run-v1", preview_sha256: "preview", snapshot: { normal_calls: 5, maximum_calls: 5, maximum_cost_cny: "1.20", plan_input_tokens: 45000, blockers: [] }, state: { ...(longform ? { segments_id: "segments", units_id: "units" } : {}), candidate_id: "candidate", memory_id: "memory", message: "正文已保存，复核未完成" }, next_action: null, artifacts: [...(longform ? [{ id: "segments", payload: {} }, { id: "units", payload: { items: [{ ordinal: 1, start: 0, end: candidate.length, memory_id: "memory" }] } }] : []), { id: "candidate", kind: "candidate", sha256: "candidate-sha", payload: { body: candidate, complete: true } }, { id: "memory", kind: "memory", sha256: "memory-sha", payload: { status: "partial", outcome: "私人邀请促成同行", position: { current_location: "渡口", recent_major_event: "约定同行" }, factual_changes: {}, changes: [{ observation: "因特殊在意提出私人邀请", evidence: [{ id: "p1", text: "林青望着她，发现自己在等一个与公务无关的回答。" }], value: { summary: "同行" } }], unresolved: ["关系尚未明确"], diagnostics: [] } }], calls: [] };
    else if (path.endsWith("/stage-chapters")) value = { candidate_sha256: "candidate-sha", manifest_sha256: "segments", tail: null, deferred_fact_count: 0, chapters: [{ id: "chapter", number: 1, ordinal: 1, start: 0, end: candidate.length, position: { current_location: "渡口", recent_major_event: "约定同行" }, position_status: "known", factual_changes: {}, observations: [], diagnostics: [], facts_status: "partial" }] };
    else if (!["/api/projects/archived", "/api/local-tasks", "/api/provider-profiles"].includes(path) && !path.endsWith("/chapters") && !path.endsWith("/versions")) unexpected.push(path);
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/create");
  await expect(page.getByRole("heading", { name: "阶段创作" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "正文", exact: true })).toHaveValue(candidate);
  await expect(page.getByRole("option", { name: "选择批次", exact: true })).toHaveJSProperty("disabled", true);
  await expect(page.getByText("正文已保存，复核未完成")).toBeVisible();
  await page.getByRole("tab", { name: "审核与采用", exact: true }).click();
  await expect(page.getByRole("heading", { name: "事实接力与可选反馈" })).toBeVisible();
  await expect(page.getByRole("button", { name: longform ? "预览阶段采用" : "预览正式采用" })).toBeDisabled();
  await page.getByText("新阶段设置 · 正式起点 v1").click();
  await expect(page.getByLabel("主题材")).toHaveValue("girls_love_gl");
  await expect(page.getByLabel("关系方向")).toHaveCount(0);
  await expect(page.getByLabel("本次想写什么（可选）")).toBeVisible();
  await expect(page.getByRole("button", { name: "建立新预览（不调用模型）" })).toBeEnabled();
  await expect(page.getByLabel("人物选择方式")).not.toBeVisible();
  await page.locator(".generation-new-settings").first().screenshot({ path: testInfo.outputPath("simple-settings.png") });
  await page.getByText("调整自动配置（可选）").click();
  await page.getByLabel("人物选择方式").selectOption("chief-auto-v1");
  await expect(page.getByText("固定必须出场的人物（可选）")).toBeVisible();
  await expect(page.getByLabel("指定视角（可选）")).toHaveValue("");
  if (longform) await expect(page.getByRole("heading", { name: "阶段进度与逐章采用" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath(longform ? "longform-workspace.png" : "generation-workspace.png"), fullPage: true });
  const dimensions = await page.evaluate(() => ({ width: document.documentElement.clientWidth, content: document.documentElement.scrollWidth }));
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.width + 1);
  expect(unexpected).toEqual([]);
});
