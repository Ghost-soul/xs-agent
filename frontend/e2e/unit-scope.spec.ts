import { expect, test } from "@playwright/test";

test("new stage uses narrative units without word or chapter targets", async ({ page }, testInfo) => {
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/backend/, "");
    let value: unknown = [];
    if (route.request().method() !== "GET") {
      const body = route.request().postDataJSON();
      writes.push({ path, body });
      if (path !== "/api/projects/p/generation-batches/random-preview") { await route.fulfill({ status: 403 }); return; }
      value = { id: "draft", project_id: "p", spec: body, revision: "genre-led-longform-v1", status: "draft", next_action: "plan", state: {}, artifacts: [], calls: [], preview_sha256: "preview", snapshot: { maximum_calls: 15, maximum_cost_cny: "2", blockers: [] } };
    } else if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "叙事单元设置验证", current_version: 1, created_at: "2026-09-24T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "离线替身", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    else if (path.endsWith("/generation-batches/setup")) value = { configuration_revision: "author-intent-v1", context_budget_revision: "focused-v1", output_budget_revision: "chief-output-v1", automation_revision: "stage-auto-v1", base_version_id: "v", version: 1, characters: [], narrative_position: {}, available_cards: [{ id: "world", name: "奇幻", layer: "genre" }], style: { genre_card_id: "world", secondary_genre_card_ids: [], matched_cards: [{ id: "world", name: "奇幻", layer: "genre" }] } };
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/create");
  await expect(page.getByLabel("叙事单元上限")).toHaveValue("3");
  await expect(page.getByLabel("本次篇幅")).toHaveCount(0);
  await expect(page.getByLabel("每章目标字数")).toHaveCount(0);
  await expect(page.getByLabel("预计章节数")).toHaveCount(0);
  await page.getByLabel("叙事单元上限").fill("6");
  await expect(page.getByText(/最多 6 个叙事单元/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("unit-settings.png"), fullPage: true });
  await page.getByRole("button", { name: "建立新预览（不调用模型）" }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].body).toMatchObject({ length_policy: "unit-v1", unit_limit: 6, target_characters: null, chapter_count: null, input_limit: 200000, writer_output_limit: 100000 });
  expect((await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))).toBeLessThanOrEqual(1);
});
