import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProjectDialog } from "./AppDialogs";
import type { ImportPreviewResponse, ProjectSetupPayload } from "./api";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

const binding = {
  preview_id: "11111111-1111-4111-8111-111111111111",
  upload_sha256: "a".repeat(64),
  chapter_manifest_sha256: "b".repeat(64),
  selected_encoding: "gb18030" as const,
  parser_version: "manuscript-parser-v1",
};

const basePayload: ProjectSetupPayload = {
  mode: "import",
  title: "测试小说",
  genre: "东方玄幻",
  genre_selection_mode: "automatic",
  genre_card_id: null,
  secondary_genre_card_ids: [],
  target_chapters: 20,
  chapter_min_characters: 4000,
  chapter_target_characters: 5000,
  chapter_max_characters: 6500,
  world_summary: "",
  character_names: [],
  import_filename: "测试小说.txt",
  import_encoding: "gb18030",
  import_preview: binding,
  step: 5,
};

const genreCards = [
  { id: "primary", layer: "genre", name: "主题材", tagline: "主承诺", source_file: "主题材.md" },
  { id: "side", layer: "genre", name: "副题材", tagline: "背景", source_file: "副题材.md" },
  ...Array.from({ length: 13 }, (_, index) => ({
    id: `secondary-${index + 1}`, layer: "narrative",
    name: `副题材${index + 1}`,
    tagline: `补充${index + 1}`,
    source_file: `副题材${index + 1}.md`,
  })),
];

function preview(overrides: Partial<ImportPreviewResponse> = {}): ImportPreviewResponse {
  return {
    preview_id: binding.preview_id,
    schema_version: "import-preview-v1",
    title: "测试小说",
    original_filename: "测试小说.txt",
    upload_sha256: binding.upload_sha256,
    byte_size: 128,
    selected_encoding: "gb18030",
    requires_encoding_confirmation: false,
    parser_version: binding.parser_version,
    chapter_manifest_sha256: binding.chapter_manifest_sha256,
    chapter_count: 2,
    character_count: 1200,
    chapters: [
      { ordinal: 1, title: "第一章", characters: 600, first_sample: "开端", last_sample: "转折" },
      { ordinal: 2, title: "第二章", characters: 600, first_sample: "追踪", last_sample: "悬念" },
    ],
    warnings: [],
    status: "previewed",
    expires_at: "2099-08-03T00:00:00+00:00",
    ...overrides,
  };
}

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function savedDraft(payload: ProjectSetupPayload) {
  return {
    draft_id: "22222222-2222-4222-8222-222222222222",
    schema_version: "project-setup-draft-v1",
    title: payload.title || "未命名作品",
    payload,
    status: "draft",
    finalized_project_id: null,
    updated_at: "2026-08-03T00:00:00+00:00",
  };
}

async function advanceToImportStep() {
  fireEvent.click(screen.getByLabelText("导入已有正文"));
  fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));
  await screen.findByText(/第 2 步/u);
  fireEvent.change(screen.getByLabelText("项目名称"), { target: { value: "测试小说" } });
  fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));
  await screen.findByText(/第 3 步/u);
  fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));
  await screen.findByText(/第 4 步/u);
  fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));
  await screen.findByText(/第 5 步/u);
}

describe("project setup import", () => {
  it("restores legacy automatic drafts as unselected without picking a default card", async () => {
    const draft = savedDraft({ ...basePayload, mode: "blank", step: 2, import_preview: null });
    const savedPayloads: ProjectSetupPayload[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/genre-quality-cards")) return response(genreCards);
      if (url.endsWith("/api/provider-profiles")) return response([]);
      if (url.endsWith("/api/project-setup-drafts") && !init?.method) return response([draft]);
      if (url.includes("/api/project-setup-drafts") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as { payload: ProjectSetupPayload };
        savedPayloads.push(body.payload);
        return response(savedDraft(body.payload));
      }
      throw new Error(`unexpected request: ${url}`);
    }));
    render(<ProjectDialog busy={false} close={vi.fn()} submit={vi.fn(async () => undefined)} />);
    fireEvent.click(await screen.findByRole("button", { name: "继续" }));
    expect(await screen.findByLabelText("题材卡模式")).toHaveValue("unselected");
    expect(screen.queryByRole("option", { name: "自动匹配" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));
    await waitFor(() => expect(savedPayloads.at(-1)).toMatchObject({
      genre_selection_mode: "unselected", genre_card_id: null, secondary_genre_card_ids: [],
    }));
  });

  it("persists a genre pair and more than twelve narrative cards independently", async () => {
    const savedPayloads: ProjectSetupPayload[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/project-setup-drafts") && !init?.method) return response([]);
      if (url.endsWith("/api/provider-profiles")) return response([]);
      if (url.endsWith("/api/genre-quality-cards")) return response(genreCards);
      if (url.includes("/api/project-setup-drafts")) {
        const body = JSON.parse(String(init?.body)) as { payload: ProjectSetupPayload };
        savedPayloads.push(body.payload);
        return response(savedDraft(body.payload), init?.method === "POST" ? 201 : 200);
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<ProjectDialog busy={false} close={vi.fn()} submit={vi.fn(async () => undefined)} />);
    fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));
    await screen.findByText(/第 2 步/u);
    fireEvent.change(screen.getByLabelText("项目名称"), { target: { value: "多题材小说" } });
    expect(screen.getByLabelText("题材卡模式")).toHaveValue("unselected");
    expect(screen.queryByRole("option", { name: "自动匹配" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("题材卡模式"), { target: { value: "specified" } });
    expect(screen.getByRole("button", { name: "保存并继续" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("主题材卡"), { target: { value: "primary" } });
    fireEvent.change(screen.getByLabelText("副题材（可选）"), { target: { value: "side" } });
    for (let index = 1; index <= 13; index += 1) {
      fireEvent.click(screen.getByLabelText(`叙事卡：副题材${index}`));
    }
    expect(screen.getByLabelText("叙事卡：副题材13")).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));

    await waitFor(() => expect(savedPayloads.at(-1)).toMatchObject({
      genre_selection_mode: "specified",
      genre_card_id: "primary",
      secondary_genre_card_ids: ["side", ...Array.from({ length: 13 }, (_, index) => `secondary-${index + 1}`)],
    }));
  });

  it("uploads raw bytes and requires an explicit GB18030 re-preview", async () => {
    const submit = vi.fn(async (
      _title: string,
      _setup: ProjectSetupPayload,
      _draftId: string,
    ) => undefined);
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.endsWith("/api/project-setup-drafts") && !init?.method) return response([]);
      if (url.endsWith("/api/provider-profiles")) return response([]);
      if (url.includes("/api/imports/preview")) {
        const needsConfirmation = url.includes("encoding=auto");
        return response(preview({ requires_encoding_confirmation: needsConfirmation } ), 201);
      }
      if (url.includes("/api/project-setup-drafts")) {
        const body = JSON.parse(String(init?.body)) as { payload: ProjectSetupPayload };
        return response(savedDraft(body.payload), init?.method === "POST" ? 201 : 200);
      }
      throw new Error(`unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProjectDialog busy={false} close={vi.fn()} submit={submit} />);
    await advanceToImportStep();

    const file = new File([new Uint8Array([0xd2, 0xbb, 0xd5, 0xc2])], "测试小说.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("选择正文文件"), { target: { files: [file] } });
    expect(await screen.findByText(/请明确选择该编码并重新预览/u)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存并继续" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("编码"), { target: { value: "gb18030" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "保存并继续" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));
    await screen.findByText(/第 6 步/u);
    fireEvent.click(screen.getByRole("button", { name: "创建作品" }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());

    const uploadRequests = requests.filter((item) => item.url.includes("/api/imports/preview"));
    expect(uploadRequests).toHaveLength(2);
    expect(uploadRequests[0].init?.body).toBe(file);
    const uploadHeaders = new Headers(uploadRequests[0].init?.headers);
    expect(uploadHeaders.get("Content-Type")).toBe("application/octet-stream");
    expect(uploadHeaders.get("X-Upload-Filename")).toBe(encodeURIComponent(file.name));
    expect(uploadRequests[1].url).toContain("encoding=gb18030");
    expect(submit.mock.calls[0][1].import_preview).toEqual(binding);
    expect(submit.mock.calls[0][1]).toMatchObject({
      genre_selection_mode: "unselected", genre_card_id: null, secondary_genre_card_ids: [],
    });
    expect(requests.some((item) => /provider-next-action|run-to-review|execute/u.test(item.url))).toBe(false);
  });

  it("restores a matching server preview from a setup draft", async () => {
    const draft = savedDraft(basePayload);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/project-setup-drafts")) return response([draft]);
      if (url.endsWith("/api/provider-profiles")) return response([]);
      if (url.endsWith(`/api/imports/${binding.preview_id}`)) return response(preview());
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<ProjectDialog busy={false} close={vi.fn()} submit={vi.fn(async () => undefined)} />);
    fireEvent.click(await screen.findByRole("button", { name: "继续" }));

    expect(await screen.findByText(/预览识别 2 章/u)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存并继续" })).toBeEnabled();
  });

  it("rejects a restored preview whose immutable SHA binding changed", async () => {
    const draft = savedDraft(basePayload);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/project-setup-drafts")) return response([draft]);
      if (url.endsWith("/api/provider-profiles")) return response([]);
      if (url.endsWith(`/api/imports/${binding.preview_id}`)) {
        return response(preview({ upload_sha256: "c".repeat(64) }));
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<ProjectDialog busy={false} close={vi.fn()} submit={vi.fn(async () => undefined)} />);
    fireEvent.click(await screen.findByRole("button", { name: "继续" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("导入预览绑定已变化");
    expect(screen.getByRole("button", { name: "保存并继续" })).toBeDisabled();
  });
});
