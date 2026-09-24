import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FormalTitleEditor, VersionHistory } from "./App";
import type { ChapterWorkflow, VersionSummary } from "./api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const committedWorkflow: ChapterWorkflow = {
  chapter_id: "chapter-3",
  project_id: "project-1",
  ordinal: 3,
  title: "侍从不敢接话",
  current_version: 17,
  phase: "committed",
  next_action: null,
  revision_id: "revision-3",
};

describe("formal chapter titles", () => {
  it("saves a complete edited title set as one versioned operation", () => {
    const save = vi.fn(async () => undefined);
    render(<FormalTitleEditor
      chapters={[committedWorkflow, { ...committedWorkflow, chapter_id: "chapter-4", ordinal: 4, title: "旧标题" }]}
      busy={false}
      cancel={vi.fn()}
      save={save}
    />);

    fireEvent.change(screen.getByLabelText("第4章正式标题"), { target: { value: "门外有雨" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    expect(save).toHaveBeenCalledWith([
      { chapter_id: "chapter-3", title: "侍从不敢接话" },
      { chapter_id: "chapter-4", title: "门外有雨" },
    ]);
    expect(screen.getByText(/正文、Revision 和 StoryState 保持不变/u)).toBeInTheDocument();
  });
});

describe("version maintenance", () => {
  const versions: VersionSummary[] = Array.from({ length: 11 }, (_, index) => {
    const number = 25 - index;
    return {
      version_id: `version-${number}`,
      number,
      parent_id: number > 1 ? `version-${number - 1}` : null,
      parent_number: number > 1 ? number - 1 : null,
      rollback_of_id: null,
      rollback_of_number: null,
      restart_of_id: number === 25 ? "version-24" : null,
      restart_of_number: number === 25 ? 24 : null,
      restart_base_number: number === 25 ? 24 : null,
      chapter_count: number === 25 ? 0 : 2,
      created_at: "2026-07-27T12:00:00Z",
    };
  });

  it("exposes explicit restart and retention commands", () => {
    const restartStory = vi.fn();
    const pruneOldVersions = vi.fn();
    render(<VersionHistory
      versions={versions}
      currentVersion={25}
      busy={false}
      rollback={vi.fn()}
      restartStory={restartStory}
      pruneOldVersions={pruneOldVersions}
    />);

    fireEvent.click(screen.getByRole("button", { name: "按当前设定重新开书" }));
    fireEvent.click(screen.getByRole("button", { name: "清理旧版本" }));
    expect(restartStory).toHaveBeenCalledOnce();
    expect(pruneOldVersions).toHaveBeenCalledOnce();
    expect(screen.getByText("重开")).toBeInTheDocument();
  });
});
