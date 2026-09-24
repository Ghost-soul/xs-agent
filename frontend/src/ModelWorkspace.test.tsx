import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModelWorkspace } from "./ModelWorkspace";
import type { ProviderProfile } from "./api";
import { getDirtySurfaces } from "./unsavedChanges";

const qwenProfile: ProviderProfile = {
  id: "qwen",
  profile_revision: "a".repeat(64),
  display_name: "qwen",
  protocol: "openai_chat_completions",
  base_url: "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
  enabled: true,
  is_local: false,
  credential_required: true,
  allow_story_data: true,
  structured_output_mode: "json_object",
  supports_reasoning_effort: false,
  streaming_enabled: true,
  default_model: "Qwen3.8-Max-Preview",
  models: [{
    id: "Qwen3.8-Max-Preview",
    label: "Qwen3.8-Max-Preview",
    input_price_cny_per_million: "1",
    output_price_cny_per_million: "1",
    context_window: null,
    max_output_tokens: null,
    streaming_enabled: false,
  }],
  built_in: false,
  has_api_key: true,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ModelWorkspace", () => {
  it("edits a custom profile without reading or replacing its API key", async () => {
    let savedProfile = qwenProfile;
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") {
        const payload = JSON.parse(String(init.body)) as { profile: ProviderProfile };
        savedProfile = { ...payload.profile, has_api_key: true };
        return { ok: true, json: async () => savedProfile } as Response;
      }
      return { ok: true, json: async () => [savedProfile] } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ModelWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑配置" }));

    expect(screen.getByLabelText("配置 ID")).toBeDisabled();
    expect(screen.getByLabelText("Base URL")).toHaveValue(qwenProfile.base_url);
    expect(screen.getByLabelText("默认模型")).toHaveValue("Qwen3.8-Max-Preview");
    expect(screen.getByLabelText("新模型默认流式")).toBeChecked();
    expect(screen.getByLabelText("Qwen3.8-Max-Preview")).not.toBeChecked();

    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "https://example.aliyuncs.com/compatible-mode/v1" },
    });
    fireEvent.change(screen.getByLabelText("模型 ID（区分大小写，逗号或换行分隔）"), {
      target: { value: "qwen3.8-max-preview" },
    });
    await waitFor(() => expect(screen.getByLabelText("默认模型")).toHaveValue(
      "qwen3.8-max-preview",
    ));
    expect(screen.getByLabelText("qwen3.8-max-preview")).not.toBeChecked();
    fireEvent.click(screen.getByLabelText("qwen3.8-max-preview"));
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/backend/api/provider-profiles/qwen",
      expect.objectContaining({ method: "PUT" }),
    ));
    const putCall = fetchMock.mock.calls.find((call) => call[1]?.method === "PUT");
    const body = JSON.parse(String(putCall?.[1]?.body)) as {
      profile: ProviderProfile;
      api_key?: string;
    };
    expect(body.profile.base_url).toBe(
      "https://example.aliyuncs.com/compatible-mode/v1",
    );
    expect(body.profile.default_model).toBe("qwen3.8-max-preview");
    expect(body.profile.streaming_enabled).toBe(true);
    expect(body.profile.models.map((model) => model.id)).toEqual([
      "qwen3.8-max-preview",
    ]);
    expect(body.profile.models[0]?.streaming_enabled).toBe(true);
    expect(body.api_key).toBeUndefined();
    expect(await screen.findByText(/API Key 保持不变/)).toBeInTheDocument();
  });

  it("requires an explicit two-call capability authorization and declared limits", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/capability-verification")) {
        return { ok: true, json: async () => ({
          verified: true,
          model: qwenProfile.default_model,
          provider_calls: 2,
          actual_cost_cny: "0.000047",
        }) } as Response;
      }
      return { ok: true, json: async () => [qwenProfile] } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn(() => true));

    render(<ModelWorkspace />);
    await screen.findByText("capability 未验证", { exact: false });
    fireEvent.change(screen.getByLabelText("上下文窗口"), {
      target: { value: "128000" },
    });
    fireEvent.change(screen.getByLabelText("最大输出 tokens"), {
      target: { value: "32000" },
    });
    fireEvent.change(screen.getByLabelText("容量资料来源"), {
      target: { value: "供应商文档 2026-08-02" },
    });
    fireEvent.click(screen.getByRole("button", { name: "授权 2 次最小请求并验证" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/backend/api/provider-profiles/qwen/capability-verification",
      expect.objectContaining({ method: "POST" }),
    ));
    const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith(
      "/capability-verification",
    ));
    const body = JSON.parse(String(call?.[1]?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({
      confirmed: true,
      acknowledge_provider_calls_and_cost: true,
      expected_provider_calls: 2,
      model: qwenProfile.default_model,
      context_window: 128000,
      max_output_tokens: 32000,
      max_cost_cny: 0.01,
    });
    expect(await screen.findByText(/2 次非故事请求/)).toBeInTheDocument();
  });

  it("registers configuration and API keys as separate unsaved surfaces", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => [qwenProfile],
    } as Response)));

    render(<ModelWorkspace />);
    fireEvent.change(await screen.findByPlaceholderText("API Key 已配置，可输入新值替换"), {
      target: { value: "secret-never-drafted" },
    });
    await waitFor(() => expect(getDirtySurfaces().map((item) => item.key)).toContain(
      "provider-credential:qwen",
    ));

    fireEvent.click(screen.getByRole("button", { name: "编辑配置" }));
    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "https://changed.example/v1" },
    });
    await waitFor(() => expect(getDirtySurfaces().map((item) => item.key)).toEqual(
      expect.arrayContaining(["provider-credential:qwen", "model-config:qwen"]),
    ));
  });

  it("cancels deletion without sending a command", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => [qwenProfile] } as Response));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn(() => false));
    render(<ModelWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "删除配置" }));
    await waitFor(() => expect(window.confirm).toHaveBeenCalled());
    expect(fetchMock.mock.calls).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "qwen" })).toBeInTheDocument();
  });

  it("deletes the bound configuration and clears its unsaved edit and key", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => ({
      ok: true, json: async () => init?.method === "DELETE" ? { deleted: true } : [qwenProfile],
    } as Response));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn(() => true));
    render(<ModelWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑配置" }));
    fireEvent.change(screen.getByLabelText("显示名称"), { target: { value: "未保存的修改" } });
    fireEvent.change(screen.getByPlaceholderText("API Key 已配置，可输入新值替换"), { target: { value: "unsaved-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "删除配置" }));
    await screen.findByText(/已删除“qwen”/);
    expect(screen.queryByRole("heading", { name: "qwen" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("配置 ID")).toHaveValue("");
    expect(getDirtySurfaces().map((item) => item.key)).not.toEqual(expect.arrayContaining([
      "model-config:qwen", "provider-credential:qwen",
    ]));
    const call = fetchMock.mock.calls.find((item) => item[1]?.method === "DELETE");
    expect(call?.[0]).toBe("/backend/api/provider-profiles/qwen");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ confirmed: true, expected_revision: "a".repeat(64) });
    expect(fetchMock.mock.calls.filter((item) => item[1]?.method)).toHaveLength(1);
  });

  it("preserves the card and draft when deletion is blocked", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => init?.method === "DELETE"
      ? { ok: false, status: 409, headers: new Headers(), json: async () => ({ detail: "仍有执行中的调用" }) } as Response
      : { ok: true, json: async () => [qwenProfile] } as Response);
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn(() => true));
    render(<ModelWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑配置" }));
    fireEvent.change(screen.getByLabelText("显示名称"), { target: { value: "未保存的修改" } });
    fireEvent.click(screen.getByRole("button", { name: "删除配置" }));
    await screen.findByText("仍有执行中的调用");
    expect(screen.getByRole("heading", { name: "qwen" })).toBeInTheDocument();
    expect(screen.getByLabelText("显示名称")).toHaveValue("未保存的修改");
  });

  const officialProfile: ProviderProfile = {
    ...qwenProfile, id: "deepseek", display_name: "DeepSeek", built_in: true,
    protocol: "deepseek_chat", base_url: "https://api.deepseek.com", streaming_enabled: false,
    default_model: "deepseek-v4-pro",
    models: ["deepseek-v4-pro", "deepseek-v4-flash"].map((id) => ({
      ...qwenProfile.models[0], id, label: id, streaming_enabled: null,
    })),
  };

  it("saves the official default locally and tests the selected model only after confirmation", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => ({
      ok: true, json: async () => String(input).endsWith("/default-model")
        ? { ...officialProfile, default_model: JSON.parse(String(init?.body)).model }
        : String(input).endsWith("/test") ? { model: "deepseek-v4-flash", latency_ms: 1 } : [officialProfile],
    } as Response));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn(() => true));
    render(<ModelWorkspace />);
    const selector = await screen.findByLabelText("本次操作模型（DeepSeek）");
    expect(screen.queryByRole("button", { name: "删除配置" })).not.toBeInTheDocument();
    fireEvent.change(selector, { target: { value: "deepseek-v4-flash" } });
    expect(fetchMock.mock.calls).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "保存为默认模型" }));
    await screen.findByText(/默认模型已设为 deepseek-v4-flash/);
    expect(window.confirm).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.filter((item) => item[1]?.method)).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "测试 API" }));
    await screen.findByText(/连接测试成功：deepseek-v4-flash/);
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("DeepSeek / deepseek-v4-flash"));
    const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/test"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ confirmed: true, model: "deepseek-v4-flash" });
  });

  it("isolates capability drafts by selected official model", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => ({
      ok: true, json: async () => String(input).endsWith("/capability-verification")
        ? { model: "deepseek-v4-pro", provider_calls: 2, actual_cost_cny: "0.001" } : [officialProfile],
    } as Response));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn(() => true));
    render(<ModelWorkspace />);
    const selector = await screen.findByLabelText("本次操作模型（DeepSeek）");
    fireEvent.change(screen.getByLabelText("上下文窗口"), { target: { value: "1000000" } });
    fireEvent.change(screen.getByLabelText("最大输出 tokens"), { target: { value: "384000" } });
    fireEvent.change(screen.getByLabelText("容量资料来源"), { target: { value: "Pro 资料" } });
    fireEvent.change(selector, { target: { value: "deepseek-v4-flash" } });
    expect(screen.getByLabelText("上下文窗口")).toHaveValue(null);
    expect(screen.getByLabelText("容量资料来源")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("容量资料来源"), { target: { value: "Flash 资料" } });
    fireEvent.change(selector, { target: { value: "deepseek-v4-pro" } });
    expect(screen.getByLabelText("上下文窗口")).toHaveValue(1000000);
    expect(screen.getByLabelText("容量资料来源")).toHaveValue("Pro 资料");
    fireEvent.click(screen.getByRole("button", { name: "授权 2 次最小请求并验证" }));
    await screen.findByText(/Capability 验证通过/);
    const call = fetchMock.mock.calls.find((item) => String(item[0]).endsWith("/capability-verification"));
    expect(call).toBeDefined();
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("DeepSeek / deepseek-v4-pro"));
  });
});
