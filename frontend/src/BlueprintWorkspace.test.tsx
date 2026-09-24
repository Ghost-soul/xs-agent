import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BlueprintWorkspace } from "./BlueprintWorkspace";
import type { StoryBlueprint } from "./api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const blueprint: StoryBlueprint = {
  project_id: "project-1",
  version: 1,
  version_id: "version-1",
  characters: [],
  relationships: [],
  story_forces: [],
  scheduled_developments: [],
  plot_threads: [],
  story_foundation: { theme: "", core_expression: "", personal_side: "", opposing_side: "", long_term_conflict: "", primary_driver: "", secondary_drivers: [] },
  open_questions: [],
  foreshadowings: [],
  narrative_phases: [],
  plot_history: [],
  narrative_position: { current_phase: "", current_time: "", current_location: "", current_characters: [], recent_major_event: "", current_conflict: "", in_progress: "", horizon: "", notes: "" },
  world_lore: [],
  world_rules: [],
};

type BlueprintSection = "world" | "characters" | "relationships" | "outline" | "plot_threads" | "foreshadowings" | "narrative_position" | "history";

function sectionResponse(source: StoryBlueprint, section: BlueprintSection) {
  const data = section === "world" ? { section, world_lore: source.world_lore, world_rules: source.world_rules, story_forces: source.story_forces.filter((item) => item.layer === "world"), scheduled_developments: source.scheduled_developments.filter((item) => item.layer === "world") }
    : section === "characters" ? { section, characters: source.characters }
      : section === "relationships" ? { section, relationships: source.relationships }
        : section === "outline" ? { section, story_foundation: source.story_foundation, open_questions: source.open_questions, narrative_phases: source.narrative_phases, story_forces: source.story_forces.filter((item) => item.layer === "outline"), scheduled_developments: source.scheduled_developments.filter((item) => item.layer === "outline") }
          : section === "plot_threads" ? { section, plot_threads: source.plot_threads }
            : section === "foreshadowings" ? { section, foreshadowings: source.foreshadowings }
              : section === "narrative_position" ? { section, narrative_position: source.narrative_position }
                : { section, plot_history: source.plot_history };
  return { project_id: source.project_id, state_version: source.version, state_version_id: source.version_id, section, data, context: { character_options: section === "relationships" ? source.characters.map(({ id, name, tier }) => ({ id, name, tier })) : [] } };
}

function requestedSection(url: string): BlueprintSection | null {
  const match = url.match(/\/story-blueprint\/sections\/([^/?]+)/);
  return match ? decodeURIComponent(match[1]) as BlueprintSection : null;
}

describe("BlueprintWorkspace manual editing", () => {
  it("reports a confirmed save while project refresh remains pending", async () => {
    let finishRefresh!: () => void;
    const onSaved = vi.fn(() => new Promise<void>((resolve) => { finishRefresh = resolve; }));
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const section = requestedSection(String(input));
      if (section) return { ok: true, json: async () => sectionResponse(init?.method === "PUT" ? { ...blueprint, version: 2 } : blueprint, section) } as Response;
      return { ok: true, json: async () => [] } as Response;
    }));
    render(<BlueprintWorkspace projectId="project-1" onSaved={onSaved} />);
    await screen.findByText("世界数据库");
    fireEvent.click(screen.getByRole("button", { name: "保存手工调整" }));
    expect(await screen.findByText("已保存故事资料，正式版本 v2。")).toBeVisible();
    expect(onSaved).toHaveBeenCalledOnce();
    finishRefresh();
  });

  it("loads and mounts only the selected blueprint partition", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const section = requestedSection(url);
      if (section) return { ok: true, json: async () => sectionResponse(blueprint, section) } as Response;
      if (url.endsWith("/provider-profiles")) return { ok: true, json: async () => [] } as Response;
      throw new Error(`unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<BlueprintWorkspace projectId="project-1" onSaved={async () => undefined} />);

    expect(await screen.findByText("世界数据库")).toBeInTheDocument();
    expect(screen.queryByText("长期剧情线")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "剧情线" }));
    expect(await screen.findByText("长期剧情线")).toBeInTheDocument();
    expect(screen.queryByText("世界数据库")).not.toBeInTheDocument();
    const sectionReads = fetchMock.mock.calls.map(([input]) => String(input)).filter((url) => url.includes("/story-blueprint/sections/"));
    expect(sectionReads).toEqual([
      "/backend/api/projects/project-1/story-blueprint/sections/world",
      "/backend/api/projects/project-1/story-blueprint/sections/plot_threads",
    ]);
  });

  it("saves only the mounted partition with its expected state version", async () => {
    const saved = { ...blueprint, version: 2, version_id: "version-2", world_rules: [{ id: "rule-1", statement: "潮印必须支付记忆" }] };
    const onSaved = vi.fn(async () => undefined);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const section = requestedSection(url);
      if (section && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        expect(body.expected_state_version).toBe(1);
        expect(body).not.toHaveProperty("characters");
        expect(body.data).toMatchObject({ section: "world", world_rules: [{ statement: "潮印必须支付记忆" }] });
        expect(Object.keys(body.data as object).sort()).toEqual(["scheduled_developments", "section", "story_forces", "world_lore", "world_rules"]);
        return { ok: true, json: async () => sectionResponse(saved, section) } as Response;
      }
      if (section) return { ok: true, json: async () => sectionResponse(blueprint, section) } as Response;
      if (url.endsWith("/provider-profiles")) return { ok: true, json: async () => [] } as Response;
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<BlueprintWorkspace projectId="project-1" onSaved={onSaved} />);
    await screen.findByText("世界数据库");
    fireEvent.click(screen.getByRole("button", { name: "新增规则" }));
    fireEvent.change(screen.getByLabelText("世界规则"), { target: { value: "潮印必须支付记忆" } });
    fireEvent.click(screen.getByRole("button", { name: "保存手工调整" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    expect(screen.getByText("基于 v2")).toBeInTheDocument();
  });
});
