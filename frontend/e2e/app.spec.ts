import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";

const projectId = "10000000-0000-0000-0000-000000000001";

async function installLocalFixture(page: Page): Promise<void> {
  await page.route("**/health", async (route: Route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok", database: "ok" }) });
  });
  await page.route("**/backend/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const payload = path === "/backend/health"
      ? { status: "ok", database: "ok" }
      : path === "/backend/api/projects"
        ? [{ project_id: projectId, title: "长篇验收样本", current_version: 3, created_at: "2026-08-03T00:00:00+08:00" }]
        : path === "/backend/api/projects/archived"
          ? [{ project_id: "20000000-0000-0000-0000-000000000002", title: "归档验收样本", current_version: 2, created_at: "2026-08-01T00:00:00+08:00", archived_at: "2026-08-02T00:00:00+08:00" }]
          : path.endsWith("/chapters") || path.endsWith("/versions") || path === "/backend/api/local-tasks"
            ? []
            : path === "/backend/api/search/status"
              ? { status: "ready" }
              : path.endsWith("/workspace")
                ? { path: "F:/fixture", exists: false, formal_version: 3, session_count: 0, chunk_count: 0, last_synced_at: null, synced_version: null, up_to_date: false, policy: "fixture" }
                : [];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
  });
}

test("author workspace remains accessible and stable at the baseline viewport", async ({ page }, testInfo) => {
  await installLocalFixture(page);
  await page.goto(`/projects/${projectId}/data?tab=backup`);
  await expect(page.getByRole("heading", { name: "项目文件夹、导入导出与备份" })).toBeVisible();

  await page.keyboard.press("ControlOrMeta+K");
  await expect(page.getByLabel("搜索正文和故事资料")).toBeFocused();

  const metrics = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    documentWidth: document.documentElement.scrollWidth,
    dialogCount: document.querySelectorAll('[role="dialog"], [role="alertdialog"]').length,
    overflowing: [...document.querySelectorAll("body, body *")]
      .map((element) => ({
        tag: element.tagName.toLowerCase(),
        className: typeof element.className === "string" ? element.className : "",
        right: Math.round(element.getBoundingClientRect().right),
        width: Math.round(element.getBoundingClientRect().width),
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      }))
      .filter((item) => item.right > document.documentElement.clientWidth + 1 || item.scrollWidth > item.clientWidth + 1)
      .slice(0, 12),
  }));
  expect(metrics.documentWidth, JSON.stringify(metrics.overflowing)).toBeLessThanOrEqual(metrics.viewportWidth + 1);
  expect(metrics.dialogCount).toBe(0);

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? ""))).toEqual([]);

  await page.screenshot({
    path: testInfo.outputPath(`${testInfo.project.name}-data-workspace.png`),
    fullPage: true,
  });
});
