import { expect, test } from "@playwright/test";

for (const checkerEnabled of [false, true]) {
  test(`new stage binds optional Checker=${checkerEnabled} without starting models`, async ({ page }, testInfo) => {
    const mutations: { path: string; data: Record<string, unknown> }[] = [];
    const unexpected: string[] = [];
    await page.route("**/backend/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace("/backend", "");
      const method = route.request().method();
      let value: unknown = [];
      if (method !== "GET") {
        const data = route.request().postDataJSON();
        mutations.push({ path, data });
        if (path === "/api/projects/p/generation-batches/random-preview" && method === "POST") {
          value = { id: "draft", project_id: "p", spec: data, status: "draft", revision: "genre-led-longform-v1", next_action: "plan", preview_sha256: "preview", state: {}, artifacts: [], calls: [], snapshot: { blockers: [], maximum_calls: checkerEnabled ? 8 : 7, maximum_cost_cny: "8", normal_calls: checkerEnabled ? 8 : 7 } };
        } else { unexpected.push(`${method} ${path}`); await route.fulfill({ status: 403 }); return; }
      } else if (path === "/health") value = { status: "ok" };
      else if (path === "/api/projects") value = [{ project_id: "p", title: "创作优先验证", current_version: 1, created_at: "2026-09-22T00:00:00Z" }];
      else if (path === "/api/search/status") value = { status: "ready" };
      else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "离线模型", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
      else if (path.endsWith("/setup")) value = { configuration_revision: "author-intent-v1", context_budget_revision: "focused-v1", output_budget_revision: "chief-output-v1", automation_revision: "stage-auto-v1", base_version_id: "v", version: 1, characters: [], narrative_position: {}, style: { selection_mode: "specified", genre_card_id: "girls_love_gl", secondary_genre_card_ids: [], matched_cards: [{ id: "girls_love_gl", name: "百合" }] } };
      else if (!["/api/projects/archived", "/api/local-tasks", "/api/projects/p/generation-batches"].includes(path) && !path.endsWith("/chapters") && !path.endsWith("/versions")) unexpected.push(path);
      await route.fulfill({ status: 200, json: value });
    });
    await page.goto("/projects/p/create");
    const checker = page.getByLabel("完成正文后核对逻辑矛盾");
    await expect(checker).toBeChecked();
    if (!checkerEnabled) await checker.uncheck();
    await expect(page.getByLabel("完成正文后听取 Reader 阅读感受")).toHaveCount(0);
    await expect(page.getByLabel("中途复核剧情计划（可选）")).toHaveCount(0);
    await expect(page.getByLabel("生成方式")).toHaveCount(1);
    await expect(page.getByLabel("预留独立标题动作")).toHaveCount(1);
    await checker.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("retained-feedback.png") });
    await page.getByRole("button", { name: "建立新预览（不调用模型）" }).click();
    await expect.poll(() => mutations.length).toBe(1);
    expect(mutations[0].data).toMatchObject({ narrative_policy: "causal-v1", feedback_policy: "logic-v1", enable_reader: false, enable_checker: checkerEnabled, input_limit: 200000, chief_output_limit: 100000, writer_output_limit: 100000, auxiliary_output_limit: 100000 });
    await expect(page.getByRole("button", { name: "授权并开始阶段创作" })).toBeDisabled();
    expect(unexpected).toEqual([]);
  });
}
