import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StyleWorkspace } from "./StyleWorkspace";

vi.mock("./ReferenceStylePanel", () => ({ ReferenceStylePanel: () => null }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const cards = [
  { id: "primary", name: "主题材", tagline: "主承诺", reader_contract: ["主承诺"], quality_checks: ["主检查"], failure_modes: ["主禁区"], source_file: "主题材.md", match_evidence: ["作者指定主题材卡"] },
  { id: "secondary-a", name: "副题材甲", tagline: "补充甲", reader_contract: ["副承诺"], quality_checks: ["副检查"], failure_modes: ["副禁区"], source_file: "副题材甲.md", match_evidence: ["作者指定副题材卡"] },
];

function response(payload: unknown): Response {
  return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
}

describe("style workspace genre-card selection", () => {
  it("keeps the specified primary and secondary cards when saving style assets", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const profile = {
      selection_mode: "specified" as const,
      genre_sampling_mode: "deterministic_random_v1" as const,
      genre_card_id: "primary",
      secondary_genre_card_ids: ["secondary-a"],
      matched_cards: cards,
      matched_mechanisms: [],
      assets: [],
      policy: "本地规则",
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.endsWith("/api/genre-quality-cards")) return response(cards);
      if (url.endsWith("/style-profile") && init?.method === "PUT") return response(profile);
      if (url.endsWith("/style-profile")) return response(profile);
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<StyleWorkspace projectId="project-1" />);
    await screen.findByRole("heading", { name: "已保存的创作卡" });
    expect(screen.queryByRole("heading", { name: "候选池角色规则上限预览" })).not.toBeInTheDocument();
    expect(requests.some((item) => item.url.includes("genre-rule-preview"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "保存质量偏好" }));

    await waitFor(() => {
      const request = requests.find((item) => item.init?.method === "PUT");
      expect(JSON.parse(String(request?.init?.body))).toMatchObject({
        selection_mode: "specified",
        genre_card_id: "primary",
        secondary_genre_card_ids: ["secondary-a"],
      });
    });
    expect(screen.queryByRole("option", { name: "自动匹配" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("题材卡模式"), { target: { value: "unselected" } });
    fireEvent.click(screen.getByRole("button", { name: "保存质量偏好" }));
    await waitFor(() => {
      const request = requests.filter((item) => item.init?.method === "PUT").at(-1);
      expect(JSON.parse(String(request?.init?.body))).toMatchObject({
        selection_mode: "unselected", genre_card_id: null, secondary_genre_card_ids: [],
      });
    });
  });

  it("preserves reclassified choices in two groups and shows actionable writing guidance", async () => {
    const library = [cards[0],
      { ...cards[1], id: "tone", name: "群像悲剧", layer: "narrative", writing_guidance: ["让同一件物品在不同人物手中承担不同意义。"], combination_guidance: ["与战争搭配，写停战后众人不同的归途。"] },
      { ...cards[1], id: "system", name: "系统流", layer: "narrative", writing_guidance: ["让奖励改变下一次任务的解法。"] },
      { ...cards[1], id: "hidden", name: "马甲文", layer: "narrative", writing_guidance: ["用不同身份的信息差组织揭露。"] },
      { ...cards[1], id: "world", name: "西方奇幻", layer: "genre" },
    ];
    let saved: unknown;
    const profile = { selection_mode: "specified", genre_card_id: "primary", secondary_genre_card_ids: ["tone", "system"], matched_cards: library.slice(0, 3), matched_mechanisms: [], assets: [], policy: "作者手选" };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") saved = JSON.parse(String(init.body));
      return response(String(input).endsWith("/genre-quality-cards") ? library : profile);
    }));
    render(<StyleWorkspace projectId="p" />);
    expect(await screen.findByRole("checkbox", { name: "叙事卡：群像悲剧" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "叙事卡：系统流" })).toBeChecked();
    expect(screen.getAllByRole("group").filter((group) => group.tagName === "FIELDSET")).toHaveLength(2);
    expect(screen.queryByText("机制卡")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "叙事卡：马甲文" }));
    expect(screen.getByText("让同一件物品在不同人物手中承担不同意义。")).toBeVisible();
    expect(screen.getByText("与战争搭配，写停战后众人不同的归途。")).toBeVisible();
    expect(screen.queryByText("重点避免")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存质量偏好" }));
    await waitFor(() => expect(saved).toMatchObject({ genre_card_id: "primary", secondary_genre_card_ids: ["tone", "system", "hidden"] }));
  });
});
