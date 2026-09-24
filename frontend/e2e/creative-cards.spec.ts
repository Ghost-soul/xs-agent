import { expect, test } from "@playwright/test";

test("expanded content cards use two groups and preserve selected IDs", async ({ page }, testInfo) => {
  const definitions = [
    ["genre", "西方奇幻（魔法文明·骑士贵族·多种族·团队冒险）"],
    ["narrative", "群像悲剧（多人物命运·情义羁绊·失去与余波）"],
  ];
  const cards = definitions.flatMap(([layer, name], group) => Array.from({ length: group === 0 ? 15 : 91 }, (_, i) => ({
    id: `${layer}-${i}`, layer, name: i === 0 ? name : i === 1 ? (layer === "genre" ? "古典仙侠" : "时间循环") : i === 90 ? "成人重口" : `${name} · 示例 ${i}`,
    tagline: "用场景里的职业动作、观察与人物回应，形成鲜明的叙事质感。",
    reader_contract: ["观察人物怎样把自身本领用于共同困境。"],
    writing_guidance: ["用向导辨路、医师换药、法师检查封印的不同动作写小队分工。", "让同一件物品在不同人物手中获得新的含义。"],
    combination_guidance: ["让世界的通行制度影响人物相会的方式。"],
    quality_checks: ["哪些具体动作留下了本题材的辨识度？"], failure_modes: ["历史兼容字段"], source_file: `${layer}/${i}.md`,
  })));
  let profile = { selection_mode: "specified", genre_card_id: "genre-0", secondary_genre_card_ids: ["narrative-0"], matched_cards: [cards[0], cards[15]], matched_mechanisms: [], assets: [], policy: "两类均由作者手选，新预览使用当前卡文，已保存批次沿用冻结版本。" };
  const writes: unknown[] = [];
  const unexpected: string[] = [];
  await page.route("**/backend/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/backend/, "");
    let value: unknown = [];
    if (request.method() !== "GET") {
      if (path === "/api/projects/p/style-profile" && request.method() === "PUT") {
        const data = request.postDataJSON(); writes.push(data);
        profile = { ...profile, ...data, matched_cards: [cards.find((c) => c.id === data.genre_card_id)!, ...cards.filter((c) => data.secondary_genre_card_ids.includes(c.id))] };
        value = profile;
      } else { unexpected.push(`${request.method()} ${path}`); await route.fulfill({ status: 403 }); return; }
    } else if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "创作卡分类验证", current_version: 1, created_at: "2026-09-23T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/genre-quality-cards") value = cards;
    else if (path.endsWith("/style-profile")) value = profile;
    else if (path.endsWith("/reference-style")) value = { manifest: null, samples: [], profiles: [] };
    else if (!["/api/projects/archived", "/api/local-tasks", "/api/provider-profiles"].includes(path) && !path.endsWith("/chapters") && !path.endsWith("/versions")) unexpected.push(path);
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/settings?tab=style");
  await expect(page.getByRole("heading", { name: "题材与叙事" })).toBeVisible();
  const pool = page.locator(".creative-card-pool");
  await expect(pool.locator("fieldset")).toHaveCount(2);
  await expect(pool.getByRole("checkbox")).toHaveCount(91);
  await expect(pool.getByText(/可同时选择多张/u)).toBeVisible();
  await expect(page.getByRole("checkbox", { name: `叙事卡：${definitions[1][1]}`, exact: true })).toBeChecked();
  await expect(page.getByText("机制卡", { exact: true })).toHaveCount(0);
  await page.getByLabel("副题材（可选）").selectOption("genre-1");
  await page.getByLabel("查找叙事卡").fill("时间循环");
  await expect(pool.getByRole("checkbox")).toHaveCount(1);
  await page.getByRole("checkbox", { name: "叙事卡：时间循环", exact: true }).check();
  await expect(pool.getByText(/已选 2 张/u)).toBeVisible();
  await pool.screenshot({ path: testInfo.outputPath("creative-card-groups.png") });
  await page.getByRole("button", { name: "保存质量偏好", exact: true }).click();
  await expect(page.getByText("题材卡与风格资料已保存。正式章节没有改变。")).toBeVisible();
  expect(writes).toEqual([expect.objectContaining({ genre_card_id: "genre-0", secondary_genre_card_ids: ["genre-1", "narrative-0", "narrative-1"] })]);
  const detail = page.locator(".creative-card-detail").first();
  await expect(detail.getByText("情节展开与写法", { exact: true })).toBeVisible();
  await expect(detail.getByText("用向导辨路、医师换药、法师检查封印的不同动作写小队分工。")).toBeVisible();
  await expect(page.getByText("重点避免", { exact: true })).toHaveCount(0);
  await detail.screenshot({ path: testInfo.outputPath("creative-card-guidance.png") });
  const widths = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(widths[0]).toBeLessThanOrEqual(widths[1] + 1);
  expect(unexpected).toEqual([]);
});
