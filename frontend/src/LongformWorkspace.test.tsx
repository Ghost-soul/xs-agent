import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LongformWorkspace } from "./LongformWorkspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const dashboard = {
  project: { title: "测试作品", formal_version: 4 },
  overview: { chapters: 6, volumes: 1, characters: 2, open_promises: 1, open_threads: 1, warnings: [] },
  volumes: [],
  character_arcs: [],
  foreshadow: {
    managed: [
      {
        id: "open-1",
        content: "铜铃只在无人处响",
        status: "ready_for_payoff",
        importance: "high",
        introduced_chapter: 1,
        last_advanced_chapter: 4,
        silent_chapters: 2,
        payoff_readiness: 100,
        expected_payoff: "身份线结束前",
        recovery_condition: "摇铃者公开身份",
        attention: true,
        reminder: "可以自然兑现或继续延后。",
        selection_reasons: ["达到沉默阈值", "准备兑现"],
        suggested_action: "consider_payoff",
        lifecycle_timeline: [
          {
            action: "advanced",
            chapter_ordinal: 4,
            before_status: "active",
            after_status: "ready_for_payoff",
            note: "脚印与靴底一致",
            evidence: [{ chapter_id: "chapter-4", chapter_ordinal: 4, start: 4, end: 18, quote: "脚印与摇铃者的靴底完全一致" }],
          },
        ],
        ledger_out_of_sync: true,
      },
      {
        id: "closed-1",
        content: "旧印章已经兑现",
        status: "fulfilled",
        importance: "medium",
        introduced_chapter: 1,
        last_advanced_chapter: 6,
        silent_chapters: 0,
        payoff_readiness: 100,
        expected_payoff: "第六章",
        recovery_condition: "印章持有人现身",
        attention: false,
        reminder: "",
        selection_reasons: [],
        suggested_action: "closed",
        lifecycle_timeline: [],
        ledger_out_of_sync: false,
      },
    ],
    promises: [],
    threads: [],
  },
  timeline: [],
  relationships: { nodes: [], edges: [] },
  narrative_position: { current_time: "入夜", current_location: "侧门", horizon: "今夜", notes: "" },
  narrative_pressure: {
    scene_outcomes: [],
    expectation_stack: [],
    repetition: { window_scenes: 0, phrase_counts: {}, duplicate_choices: [], duplicate_results: [], warnings: [] },
  },
  causal_continuity: {
    handoffs: [
      {
        handoff_id: "handoff-1",
        predecessor_handoff_ids: [],
        source_chapter_ordinal: 6,
        result: "侧门被封闭",
        cost: "失去合法通行证",
        open_consequence: "必须寻找另一条路",
        payoff_delta: "delivered",
        evidence: [{ chapter_id: "chapter-6", chapter_ordinal: 6, start: 8, end: 18, quote: "守军立刻封闭侧门" }],
      },
    ],
    pulse: {
      observations: [
        {
          kind: "missing_payoff_absorption",
          state: "observed",
          source_ids: ["handoff-1"],
          chapter_age: 2,
          threshold: 2,
          advisory_only: true,
          note: "安静、私人或无观众的现实变化同样是合法余波。",
        },
      ],
    },
    source_diagnostic: null,
    advisory_only: true,
  },
  state_health: { healthy: true, version: 4, findings: [] },
};

describe("LongformWorkspace continuity views", () => {
  it("filters lifecycle evidence and presents causal advice without forced witnesses", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => dashboard })));

    render(<LongformWorkspace projectId="project-1" />);

    expect(await screen.findByRole("heading", { name: "长篇脉络" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "伏笔与承诺" }));
    expect(screen.getByText("铜铃只在无人处响")).toBeInTheDocument();
    expect(screen.getAllByText("账本不同步")).toHaveLength(2);
    expect(screen.getByText("预计兑现：身份线结束前")).toBeInTheDocument();
    expect(screen.getByText("回收条件：摇铃者公开身份")).toBeInTheDocument();
    expect(screen.getByText("脚印与摇铃者的靴底完全一致")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("伏笔筛选"), { target: { value: "fulfilled" } });
    expect(screen.getByText("旧印章已经兑现")).toBeInTheDocument();
    expect(screen.queryByText("铜铃只在无人处响")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "因果交接" }));
    expect(screen.getByText("侧门被封闭")).toBeInTheDocument();
    expect(screen.getByText("未决后果：必须寻找另一条路")).toBeInTheDocument();
    expect(screen.getByText("回报后吸收")).toBeInTheDocument();
    expect(screen.getByText("安静、私人或无观众的现实变化同样是合法余波。")).toBeInTheDocument();
    expect(screen.queryByText(/五幕|缺少见证者/u)).not.toBeInTheDocument();
  });
});
