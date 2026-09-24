import { expect, test } from "@playwright/test";

test("random pairs are previewed before authorization and network retries keep the same draw", async ({ page }, testInfo) => {
  const cards = [
    { id: "world", name: "奇幻", layer: "genre" },
    { id: "farm", name: "种田文", layer: "narrative" },
    { id: "mystery", name: "推理", layer: "narrative" },
    { id: "revenge", name: "复仇", layer: "narrative" },
  ];
  const keys: string[] = [];
  const bodies: Record<string, unknown>[] = [];
  let saved: Record<string, unknown> | null = null;
  await page.route("**/backend/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/backend/, "");
    let value: unknown = [];
    if (request.method() !== "GET") {
      expect(path).toBe("/api/projects/p/generation-batches/random-preview");
      expect(request.method()).toBe("POST");
      keys.push(request.headers()["idempotency-key"]);
      const body = request.postDataJSON();
      bodies.push(body);
      const pair = keys.length <= 2 ? ["farm", "mystery"] : ["mystery", "revenge"];
      saved = { id: keys.length <= 2 ? "first" : "second", project_id: "p", spec: { ...body, narrative_card_ids: pair }, revision: "genre-led-longform-v1", status: "draft", next_action: "plan", preview_sha256: keys.at(-1), snapshot: { maximum_cost_cny: "1", maximum_calls: 7, blockers: [], narrative_selection_policy: "random-two-v1", cards: cards.filter((c) => pair.includes(c.id)) }, state: {}, artifacts: [], calls: [] };
      if (keys.length === 1) { await route.abort("failed"); return; }
      value = saved;
    } else if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "随机叙事验证", current_version: 1, created_at: "2026-09-24T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "离线替身", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    else if (path.endsWith("/generation-batches/setup")) value = { configuration_revision: "author-intent-v1", context_budget_revision: "focused-v1", output_budget_revision: "chief-output-v1", automation_revision: "stage-auto-v1", base_version_id: "v", version: 1, characters: [], narrative_position: {}, available_cards: cards, style: { genre_card_id: "world", secondary_genre_card_ids: ["revenge"], matched_cards: cards } };
    else if (path.endsWith("/generation-batches")) value = saved ? [{ id: saved.id, direction: "随机叙事", status: "draft" }] : [];
    else if (path.endsWith("/first") || path.endsWith("/second")) value = saved;
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/create");
  await expect(page.getByText("叙事卡：每次随机两张")).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /^叙事卡：/ })).toHaveCount(0);
  const preview = page.getByRole("button", { name: "建立新预览（不调用模型）" });
  await preview.click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(preview).toBeEnabled();
  await preview.click();
  const selection = page.getByLabel("本阶段叙事卡");
  await expect(selection).toContainText("种田文、推理");
  await expect(page.getByRole("button", { name: "授权并开始阶段创作" })).toBeDisabled();
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
  await page.getByRole("button", { name: "刷新状态", exact: true }).click();
  await expect(selection).toContainText("种田文、推理");
  expect(keys).toHaveLength(2);
  await page.getByRole("button", { name: "新建阶段", exact: true }).click();
  await preview.click();
  await expect(selection).toContainText("推理、复仇");
  expect(keys[2]).not.toBe(keys[1]);
  expect(bodies.every((body) => JSON.stringify(body.narrative_card_ids) === "[]")).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("random-pair-preview.png"), fullPage: true });
  await page.reload();
  await expect(selection).toContainText("推理、复仇");
  expect(keys).toHaveLength(3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
});
