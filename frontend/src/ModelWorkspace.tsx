import { useCallback, useEffect, useRef, useState } from "react";

import { api, commandHeaders, errorMessage, jsonBody, type ProviderProfile } from "./api";
import { requestConfirmation } from "./confirmation";
import { deleteLocalDraft, readLocalDraft, sha256, writeLocalDraft, type LocalDraft } from "./localDrafts";
import { clearDirtySurface, setDirtySurface } from "./unsavedChanges";

type Draft = {
  id: string;
  display_name: string;
  base_url: string;
  protocol: ProviderProfile["protocol"];
  structured_output_mode: ProviderProfile["structured_output_mode"];
  is_local: boolean;
  credential_required: boolean;
  allow_story_data: boolean;
  supports_reasoning_effort: boolean;
  streaming_enabled: boolean;
  model_streaming: Record<string, boolean>;
  models: string;
  default_model: string;
  input_price: string;
  output_price: string;
};

type CapabilityDraft = {
  contextWindow: string;
  maxOutputTokens: string;
  sourceNote: string;
  reasoningTokensBilledAsOutput: boolean;
  maxCostCny: string;
};

const emptyDraft: Draft = {
  id: "", display_name: "", base_url: "", protocol: "openai_chat_completions",
  structured_output_mode: "json_object", is_local: false, credential_required: true,
  allow_story_data: true, supports_reasoning_effort: false, streaming_enabled: false,
  model_streaming: {},
  models: "", default_model: "",
  input_price: "", output_price: "",
};

export function ModelWorkspace() {
  const [profiles, setProfiles] = useState<ProviderProfile[]>([]);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [selectedModels, setSelectedModels] = useState<Record<string, string>>({});
  const [capabilityDrafts, setCapabilityDrafts] = useState<Record<string, Record<string, CapabilityDraft>>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [recoveryDraft, setRecoveryDraft] = useState<LocalDraft<Draft> | null>(null);
  const baseDraftRef = useRef(JSON.stringify(emptyDraft));
  const credentialSurfaceKeysRef = useRef<Set<string>>(new Set());
  const draftKey = `model-config:${editingProfileId ?? "new"}`;

  const load = useCallback(async () => setProfiles(await api<ProviderProfile[]>("/api/provider-profiles")), []);
  useEffect(() => { void load().catch((caught) => setError(errorMessage(caught, "载入模型供应商失败"))); }, [load]);
  useEffect(() => {
    const models = draft.models.split(/[,\n]/u).map((item) => item.trim()).filter(Boolean);
    if (models.length === 1 && draft.default_model !== models[0]) {
      setDraft((current) => ({ ...current, default_model: models[0] }));
    }
  }, [draft.models, draft.default_model]);
  useEffect(() => {
    let active = true;
    void Promise.all([readLocalDraft<Draft>(draftKey), sha256(baseDraftRef.current)])
      .then(([saved, baseSha]) => {
        if (!active || !saved || saved.payload_sha256 === baseSha) return;
        const sanitized = sanitizeDraft(saved.payload);
        if (sanitized && saved.payload_sha256 !== baseSha) {
          setRecoveryDraft({ ...saved, payload: sanitized });
        }
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [draftKey]);
  useEffect(() => {
    const current = JSON.stringify(draft);
    const dirty = current !== baseDraftRef.current;
    setDirtySurface(draftKey, editingProfileId ? "模型供应商配置" : "新增模型供应商配置", dirty, {
      save: async () => {
        if (dirty && !await saveProfile()) throw new Error("模型供应商配置保存失败");
      },
      discard: async () => {
        setDraft(JSON.parse(baseDraftRef.current) as Draft);
        await deleteLocalDraft(draftKey);
      },
      focus: () => document.getElementById("provider-profile-form")?.scrollIntoView?.({ block: "start" }),
    });
    if (!dirty) return () => clearDirtySurface(draftKey);
    let active = true;
    const timer = window.setTimeout(() => {
      void Promise.all([sha256(current), sha256(baseDraftRef.current)]).then(([payloadSha, baseSha]) => active ? writeLocalDraft({
        schema_version: "local-draft-v1",
        draft_key: draftKey,
        project_id: "global",
        surface: "model_config",
        resource_id: editingProfileId ?? "new",
        base_version: null,
        base_sha256: baseSha,
        payload: draft,
        payload_sha256: payloadSha,
        updated_at: new Date().toISOString(),
      }) : undefined).catch(() => undefined);
    }, 500);
    return () => { active = false; window.clearTimeout(timer); clearDirtySurface(draftKey); };
  }, [draft, draftKey, editingProfileId]);
  useEffect(() => {
    const activeKeys = new Set<string>();
    for (const [profileId, value] of Object.entries(keys)) {
      const surfaceKey = `provider-credential:${profileId}`;
      if (!value.trim()) continue;
      activeKeys.add(surfaceKey);
      setDirtySurface(surfaceKey, `${profiles.find((item) => item.id === profileId)?.display_name ?? profileId} API Key`, true, {
        save: async () => {
          if (!await saveKey(profileId)) throw new Error("API Key 保存失败");
        },
        discard: () => setKeys((current) => ({ ...current, [profileId]: "" })),
      });
    }
    for (const previous of credentialSurfaceKeysRef.current) {
      if (!activeKeys.has(previous)) clearDirtySurface(previous);
    }
    credentialSurfaceKeysRef.current = activeKeys;
    return () => { for (const key of activeKeys) clearDirtySurface(key); };
  }, [keys, profiles]);

  function editProfile(profile: ProviderProfile) {
    if (profile.built_in) return;
    setRecoveryDraft(null);
    setEditingProfileId(profile.id);
    const nextDraft: Draft = {
      id: profile.id,
      display_name: profile.display_name,
      base_url: profile.base_url,
      protocol: profile.protocol,
      structured_output_mode: profile.structured_output_mode,
      is_local: profile.is_local,
      credential_required: profile.credential_required,
      allow_story_data: profile.allow_story_data,
      supports_reasoning_effort: profile.supports_reasoning_effort,
      streaming_enabled: profile.streaming_enabled,
      model_streaming: Object.fromEntries(profile.models.map((model) => [
        model.id,
        model.streaming_enabled ?? profile.streaming_enabled,
      ])),
      models: profile.models.map((model) => model.id).join("\n"),
      default_model: profile.default_model,
      input_price: profile.models[0]?.input_price_cny_per_million ?? "",
      output_price: profile.models[0]?.output_price_cny_per_million ?? "",
    };
    baseDraftRef.current = JSON.stringify(nextDraft);
    setDraft(nextDraft);
    setError(null);
    setNotice("正在编辑“" + profile.display_name + "”；API Key 不会被读取或覆盖。");
    window.requestAnimationFrame(() => document.getElementById("provider-profile-form")?.scrollIntoView?.({ behavior: "smooth", block: "start" }));
  }

  function resetEditor() {
    baseDraftRef.current = JSON.stringify(emptyDraft);
    setEditingProfileId(null); setDraft(emptyDraft); setError(null); setNotice(null);
    setRecoveryDraft(null);
  }

  function cancelEdit() {
    const previousKey = draftKey;
    resetEditor();
    clearDirtySurface(previousKey);
    void deleteLocalDraft(previousKey).catch(() => setError("本地配置草稿清理失败，请稍后重试。"));
  }

  async function saveProfile(): Promise<boolean> {
    const models = draft.models.split(/[,\n]/u).map((item) => item.trim()).filter(Boolean);
    const defaultModel = draft.default_model.trim() || models[0] || "";
    if (!draft.id.trim() || !draft.display_name.trim() || !draft.base_url.trim() || !defaultModel) {
      setError("请填写配置 ID、显示名称、Base URL 和至少一个模型 ID。"); return false;
    }
    if (!models.includes(defaultModel)) {
      setError("默认模型必须与模型列表中的某个 ID 完全一致（包括大小写）。"); return false;
    }
    const editingProfile = editingProfileId ? profiles.find((profile) => profile.id === editingProfileId) : null;
    setBusy(true); setError(null); setNotice(null);
    try {
      const savedDraftKey = draftKey;
      await api(`/api/provider-profiles/${draft.id.trim()}`, {
        method: "PUT", headers: commandHeaders(),
        body: jsonBody({ confirmed: true, profile: {
          id: draft.id.trim(), display_name: draft.display_name.trim(), base_url: draft.base_url.trim(),
          protocol: draft.protocol, structured_output_mode: draft.structured_output_mode,
          is_local: draft.is_local, credential_required: draft.credential_required,
          allow_story_data: draft.allow_story_data, supports_reasoning_effort: draft.supports_reasoning_effort,
          streaming_enabled: draft.streaming_enabled,
          enabled: editingProfile?.enabled ?? true, default_model: defaultModel, built_in: false,
          models: models.map((id) => {
            const previous = editingProfile?.models.find((model) => model.id === id);
            return {
            id, label: previous?.label ?? id,
            input_price_cny_per_million: draft.input_price ? Number(draft.input_price) : null,
            output_price_cny_per_million: draft.output_price ? Number(draft.output_price) : null,
            context_window: previous?.context_window ?? null,
            max_output_tokens: previous?.max_output_tokens ?? null,
            streaming_enabled: draft.protocol === "openai_chat_completions"
              ? (draft.model_streaming[id] ?? draft.streaming_enabled)
              : null,
          }; }),
        }}),
      });
      const wasEditing = editingProfileId !== null;
      const savedProfileId = draft.id.trim();
      setSelectedModels((current) => Object.fromEntries(Object.entries(current).filter(([id]) => id !== savedProfileId)));
      setCapabilityDrafts((current) => Object.fromEntries(Object.entries(current).filter(([id]) => id !== savedProfileId)));
      baseDraftRef.current = JSON.stringify(emptyDraft);
      setEditingProfileId(null); setDraft(emptyDraft);
      clearDirtySurface(savedDraftKey);
      await deleteLocalDraft(savedDraftKey);
      setNotice(wasEditing ? "第三方模型配置已更新；API Key 保持不变，尚未产生模型调用。" : "第三方模型配置已保存在本机；尚未产生任何模型调用。");
      await load();
      return true;
    } catch (caught) { setError(errorMessage(caught)); return false; } finally { setBusy(false); }
  }

  async function saveKey(profileId: string): Promise<boolean> {
    const apiKey = (keys[profileId] ?? "").trim();
    if (!apiKey) { setError("请输入 API Key。"); return false; }
    setBusy(true); setError(null); setNotice(null);
    try {
      await api(`/api/provider-profiles/${profileId}/credential`, {
        method: "POST", headers: commandHeaders(), body: jsonBody({ api_key: apiKey, confirmed: true }),
      });
      setKeys((current) => ({ ...current, [profileId]: "" }));
      setNotice("API Key 已写入本机凭据存储，页面和配置文件不会回显明文。"); await load();
      return true;
    } catch (caught) { setError(errorMessage(caught)); return false; } finally { setBusy(false); }
  }

  async function toggle(profile: ProviderProfile) {
    setBusy(true); setError(null);
    try {
      await api(`/api/provider-profiles/${profile.id}/enabled`, {
        method: "POST", headers: commandHeaders(), body: jsonBody({ enabled: !profile.enabled, confirmed: true }),
      });
      await load();
    } catch (caught) { setError(errorMessage(caught)); } finally { setBusy(false); }
  }

  function selectedModel(profile: ProviderProfile): string {
    const selected = selectedModels[profile.id];
    return profile.models.some((model) => model.id === selected) ? selected : profile.default_model;
  }

  async function saveDefaultModel(profile: ProviderProfile) {
    const model = selectedModel(profile);
    setBusy(true); setError(null); setNotice(null);
    try {
      const saved = await api<ProviderProfile>(`/api/provider-profiles/${profile.id}/default-model`, {
        method: "POST", headers: commandHeaders(), body: jsonBody({ model, confirmed: true }),
      });
      setProfiles((current) => current.map((item) => item.id === profile.id ? saved : item));
      setNotice(`默认模型已设为 ${model}；仅影响后续新建配置的初始选择，不改变已有阶段、角色模型、费用计划或授权，也不会调用供应商。`);
    } catch (caught) { setError(errorMessage(caught, "保存默认模型失败")); }
    finally { setBusy(false); }
  }

  async function deleteProfile(profile: ProviderProfile) {
    if (profile.built_in || !profile.profile_revision) return;
    if (!await requestConfirmation({
      title: "删除供应商配置",
      message: `删除“${profile.display_name}”（${profile.id}，${profile.base_url}）及其本机 API Key？小说、原始响应和费用审计不会删除。引用它的后续调用将停止，不会自动切换供应商。未保存的编辑和 Key 将丢弃；该配置 ID 不可复用，新增请使用新 ID。`,
      confirmLabel: "确认删除配置", danger: true,
    })) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      await api(`/api/provider-profiles/${profile.id}`, {
        method: "DELETE", headers: commandHeaders(),
        body: jsonBody({ confirmed: true, expected_revision: profile.profile_revision }),
      });
      setProfiles((current) => current.filter((item) => item.id !== profile.id));
      setKeys((current) => Object.fromEntries(Object.entries(current).filter(([id]) => id !== profile.id)));
      setSelectedModels((current) => Object.fromEntries(Object.entries(current).filter(([id]) => id !== profile.id)));
      setCapabilityDrafts((current) => Object.fromEntries(Object.entries(current).filter(([id]) => id !== profile.id)));
      clearDirtySurface(`provider-credential:${profile.id}`);
      clearDirtySurface(`model-config:${profile.id}`);
      if (editingProfileId === profile.id) resetEditor();
      setRecoveryDraft((current) => current?.payload.id === profile.id ? null : current);
      try { await deleteLocalDraft(`model-config:${profile.id}`); }
      catch { setError("配置与 Key 已删除，但本地编辑草稿清理失败；旧草稿不能重新保存此 ID。"); }
      setNotice(`已删除“${profile.display_name}”及其 API Key；小说和历史审计保持不变，未调用供应商。`);
    } catch (caught) { setError(errorMessage(caught, "删除配置失败")); }
    finally { setBusy(false); }
  }

  async function testProfile(profile: ProviderProfile) {
    const model = selectedModel(profile);
    if (!await requestConfirmation({ title: "确认测试 Provider", message: `测试 ${profile.display_name} / ${model}？这会发送最小非故事请求，并可能产生极少量费用。`, confirmLabel: "确认发送请求" })) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const result = await api<{ latency_ms: number; model: string }>(`/api/provider-profiles/${profile.id}/test`, {
        method: "POST", headers: commandHeaders(),
        body: jsonBody({ confirmed: true, model }),
      });
      setNotice(`连接测试成功：${result.model}，耗时 ${result.latency_ms} ms。`);
    } catch (caught) { setError(errorMessage(caught, "连接测试失败")); }
    finally { setBusy(false); }
  }

  function capabilityDraft(profile: ProviderProfile): CapabilityDraft {
    const modelId = selectedModel(profile);
    const model = profile.models.find((item) => item.id === modelId);
    return capabilityDrafts[profile.id]?.[modelId] ?? {
      contextWindow: model?.context_window?.toString() ?? "",
      maxOutputTokens: model?.max_output_tokens?.toString() ?? "",
      sourceNote: "",
      reasoningTokensBilledAsOutput: model?.reasoning_tokens_billed_as_output ?? false,
      maxCostCny: "0.01",
    };
  }

  function updateCapabilityDraft(profile: ProviderProfile, patch: Partial<CapabilityDraft>) {
    setCapabilityDrafts((current) => ({
      ...current,
      [profile.id]: { ...current[profile.id], [selectedModel(profile)]: { ...capabilityDraft(profile), ...patch } },
    }));
  }

  async function verifyCapability(profile: ProviderProfile) {
    const model = selectedModel(profile);
    const draft = capabilityDraft(profile);
    const contextWindow = Number(draft.contextWindow);
    const maxOutputTokens = Number(draft.maxOutputTokens);
    const maxCostCny = Number(draft.maxCostCny);
    if (!Number.isInteger(contextWindow) || contextWindow <= 0 || !Number.isInteger(maxOutputTokens) || maxOutputTokens <= 0) {
      setError("请根据供应商正式资料填写有效的上下文窗口和最大输出 tokens。"); return;
    }
    if (!draft.sourceNote.trim()) {
      setError("请填写 capability 数值来源，避免把未经核实的容量标记为已验证。"); return;
    }
    if (!Number.isFinite(maxCostCny) || maxCostCny <= 0 || maxCostCny > 1) {
      setError("Capability 验证费用上限必须大于 0 且不超过 ¥1。"); return;
    }
    if (!await requestConfirmation({ title: "确认 capability 验证", message: `验证 ${profile.display_name} / ${model}？将恰好发送 2 次最小非故事请求，费用不得超过 ¥${draft.maxCostCny}；失败不会自动重试，也不会标记 verified。`, confirmLabel: "确认两次请求" })) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const result = await api<{ model: string; actual_cost_cny: string; provider_calls: number }>(`/api/provider-profiles/${profile.id}/capability-verification`, {
        method: "POST", headers: commandHeaders(), body: jsonBody({
          confirmed: true,
          acknowledge_provider_calls_and_cost: true,
          expected_provider_calls: 2,
          model,
          context_window: contextWindow,
          max_output_tokens: maxOutputTokens,
          reasoning_tokens_billed_as_output: draft.reasoningTokensBilledAsOutput,
          source_note: draft.sourceNote.trim(),
          max_cost_cny: maxCostCny,
        }),
      });
      setNotice(`Capability 验证通过：${result.model}，${result.provider_calls} 次非故事请求，记录费用 ¥${result.actual_cost_cny}。`);
      await load();
    } catch (caught) { setError(errorMessage(caught, "Capability 验证失败；未自动重试，也未标记 verified")); }
    finally { setBusy(false); }
  }

  function updateModelIds(value: string) {
    const previousIds = draft.models.split(/[,\n]/u).map((item) => item.trim()).filter(Boolean);
    const nextIds = value.split(/[,\n]/u).map((item) => item.trim()).filter(Boolean);
    const modelStreaming = { ...draft.model_streaming };
    if (previousIds.length === 1 && nextIds.length === 1 && previousIds[0] !== nextIds[0]) {
      modelStreaming[nextIds[0]] = modelStreaming[previousIds[0]] ?? draft.streaming_enabled;
    }
    for (const id of nextIds) {
      if (!(id in modelStreaming)) modelStreaming[id] = draft.streaming_enabled;
    }
    setDraft({ ...draft, models: value, model_streaming: modelStreaming });
  }

  const draftModelIds = draft.models.split(/[,\n]/u).map((item) => item.trim()).filter(Boolean);

  return <div className="model-workspace">
    {recoveryDraft && <div className="dialog-backdrop" role="presentation"><section className="dialog" role="dialog" aria-modal="true" aria-label="模型配置本地草稿"><h2>发现模型配置本地草稿</h2><p>{recoveryDraft.base_sha256 === null ? "发现未完成配置。" : "配置基线可能已经变化。"}恢复只会回填非密钥字段，保存前仍可逐项检查。</p><details><summary>查看草稿字段</summary><ul>{draftSummary(recoveryDraft.payload).map((item) => <li key={item}>{item}</li>)}</ul></details><div className="dialog-actions"><button type="button" onClick={() => setRecoveryDraft(null)}>保留并查看当前配置</button><button type="button" className="danger-command" onClick={() => { void deleteLocalDraft(recoveryDraft.draft_key); setRecoveryDraft(null); }}>丢弃草稿</button><button type="button" className="primary-button" onClick={() => { baseDraftRef.current = JSON.stringify(draft); setDraft(recoveryDraft.payload); setRecoveryDraft(null); }}>恢复到编辑器</button></div></section></div>}
    <header className="workspace-heading"><div><span className="eyebrow">作者模型控制台</span><h2>模型与第三方供应商</h2><p>通过通用 OpenAI-compatible Chat Completions 接入聚合网关、本地服务或其他厂商。配置和凭据不会触发调用；每次正文外发与费用仍需单独确认。</p></div></header>
    {error && <div className="error-banner">{error}</div>}{notice && <div className="success-banner">{notice}</div>}
    <section className="provider-profile-grid">{profiles.map((profile) => <article className={"provider-profile-card" + (editingProfileId === profile.id ? " editing-profile" : "")} key={profile.id}>
      <header><div><span className="card-label">{profile.protocol}</span><h3>{profile.display_name}</h3></div><span className={profile.enabled ? "status-ready" : "status-muted"}>{profile.enabled ? "已启用" : "已停用"}</span></header>
      <p><code>{profile.id}</code> · {profile.base_url}</p><p>默认模型：<strong>{profile.default_model}</strong></p>
      <div className="model-chip-list">{profile.models.map((model) => <span key={model.id}>{model.label || model.id}{(model.streaming_enabled ?? profile.streaming_enabled) ? " · 流式" : ""}{model.verified_at ? " · capability 已验证" : " · capability 未验证"}</span>)}</div>
      <label>本次操作模型（{profile.display_name}）<select disabled={busy} value={selectedModel(profile)} onChange={(event) => setSelectedModels((current) => ({ ...current, [profile.id]: event.target.value }))}>{profile.models.map((model) => <option key={model.id} value={model.id}>{model.label && model.label !== model.id ? `${model.label} · ${model.id}` : model.id}</option>)}</select></label>
      <button disabled={busy || selectedModel(profile) === profile.default_model} onClick={() => void saveDefaultModel(profile)}>保存为默认模型</button>
      <p>测试和能力验证使用所选模型，均需另行授权。保存默认值仅用于后续新建配置；已有阶段的六角色模型不变。</p>
      <p>{profile.is_local ? "本地端点；失败时不会自动切换到外部供应商。" : profile.allow_story_data ? "允许在作者逐次确认后发送故事数据。" : "禁止发送故事数据。"}</p>
      {profile.credential_required && <div className="credential-row"><input type="password" autoComplete="new-password" placeholder={profile.has_api_key ? "API Key 已配置，可输入新值替换" : "输入 API Key"} value={keys[profile.id] ?? ""} onChange={(event) => setKeys((current) => ({ ...current, [profile.id]: event.target.value }))} /><button disabled={busy} onClick={() => void saveKey(profile.id)}>安全保存</button></div>}
      <div className="button-row"><button disabled={busy || !profile.enabled} onClick={() => void testProfile(profile)}>测试 API</button>{!profile.built_in && <><button disabled={busy} onClick={() => editProfile(profile)}>编辑配置</button><button className="danger-command" disabled={busy || !profile.profile_revision} onClick={() => void deleteProfile(profile)}>删除配置</button><button disabled={busy} onClick={() => void toggle(profile)}>{profile.enabled ? "停用配置" : "重新启用"}</button></>}</div>
      <details className="capability-verification"><summary>验证所选模型 capability</summary><p>先依据供应商正式资料填写容量，再由两次最小非故事请求核验结构化输出、usage、成功终态和输出上限终态。不会测试故事质量。</p><div className="form-grid"><label>上下文窗口<input disabled={busy} type="number" min="1" value={capabilityDraft(profile).contextWindow} onChange={(event) => updateCapabilityDraft(profile, { contextWindow: event.target.value })} /></label><label>最大输出 tokens<input disabled={busy} type="number" min="1" value={capabilityDraft(profile).maxOutputTokens} onChange={(event) => updateCapabilityDraft(profile, { maxOutputTokens: event.target.value })} /></label><label>费用上限 CNY<input disabled={busy} type="number" min="0.0001" max="1" step="0.0001" value={capabilityDraft(profile).maxCostCny} onChange={(event) => updateCapabilityDraft(profile, { maxCostCny: event.target.value })} /></label><label><input disabled={busy} type="checkbox" checked={capabilityDraft(profile).reasoningTokensBilledAsOutput} onChange={(event) => updateCapabilityDraft(profile, { reasoningTokensBilledAsOutput: event.target.checked })} />reasoning tokens 按输出计费</label><label className="span-2">容量资料来源<input disabled={busy} value={capabilityDraft(profile).sourceNote} placeholder="供应商文档名称、版本或链接" onChange={(event) => updateCapabilityDraft(profile, { sourceNote: event.target.value })} /></label></div><button disabled={busy || !profile.enabled} onClick={() => void verifyCapability(profile)}>授权 2 次最小请求并验证</button></details>
    </article>)}</section>
    <section id="provider-profile-form" className="provider-profile-form"><div className="section-title"><div><span className="card-label">{editingProfileId ? "编辑第三方配置" : "新增第三方配置"}</span><h3>{editingProfileId ? "修改 " + (draft.display_name || draft.id) : "OpenAI-compatible 优先"}</h3><p>{editingProfileId ? "可以修改访问地址、模型列表、默认模型、协议和结构化输出等设置；保存不会改变已有 API Key。" : "模型列表是作者可选项，不做厂商白名单。价格按人民币/百万 tokens 填写，用于费用计划；不清楚时请先向供应商核实。"}</p></div></div>
      <div className="form-grid"><label>配置 ID<input disabled={editingProfileId !== null} value={draft.id} placeholder="例如 openrouter-main" onChange={(event) => setDraft({ ...draft, id: event.target.value.toLowerCase() })} /></label><label>显示名称<input value={draft.display_name} placeholder="例如 OpenRouter 主账号" onChange={(event) => setDraft({ ...draft, display_name: event.target.value })} /></label><label className="span-2">Base URL<input value={draft.base_url} placeholder="https://example.com/v1" onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} /></label><label>协议<select value={draft.protocol} onChange={(event) => { const protocol = event.target.value as Draft["protocol"]; setDraft({ ...draft, protocol, structured_output_mode: protocol === "openai_responses" ? "json_schema" : draft.structured_output_mode, streaming_enabled: protocol === "openai_chat_completions" ? draft.streaming_enabled : false, model_streaming: protocol === "openai_chat_completions" ? draft.model_streaming : Object.fromEntries(draftModelIds.map((id) => [id, false])) }); }}><option value="openai_chat_completions">OpenAI Chat Completions</option><option value="openai_responses">OpenAI Responses</option><option value="deepseek_chat">DeepSeek Chat</option></select></label><label>结构化输出<select value={draft.structured_output_mode} onChange={(event) => setDraft({ ...draft, structured_output_mode: event.target.value as Draft["structured_output_mode"] })}><option value="json_schema">JSON Schema</option><option value="json_object">JSON Object</option><option value="prompt_only">仅提示词约束</option></select></label><label className="span-2">模型 ID（区分大小写，逗号或换行分隔）<textarea value={draft.models} placeholder="例如 qwen3.8-max-preview" onChange={(event) => updateModelIds(event.target.value)} /></label><label>默认模型<input value={draft.default_model} placeholder="留空则使用第一个" onChange={(event) => setDraft({ ...draft, default_model: event.target.value })} /></label><label>输入价 CNY/百万<input type="number" min="0" step="0.0001" value={draft.input_price} onChange={(event) => setDraft({ ...draft, input_price: event.target.value })} /></label><label>输出价 CNY/百万<input type="number" min="0" step="0.0001" value={draft.output_price} onChange={(event) => setDraft({ ...draft, output_price: event.target.value })} /></label></div>
      {draftModelIds.length > 0 && <fieldset className="model-streaming-options" disabled={draft.protocol !== "openai_chat_completions"}><legend>逐模型流式响应</legend>{draftModelIds.map((id) => <label key={id}><input type="checkbox" checked={draft.model_streaming[id] ?? draft.streaming_enabled} onChange={(event) => setDraft({ ...draft, model_streaming: { ...draft.model_streaming, [id]: event.target.checked } })} />{id}</label>)}</fieldset>}
      <div className="profile-flags"><label><input type="checkbox" checked={draft.is_local} onChange={(event) => setDraft({ ...draft, is_local: event.target.checked })} />本地端点</label><label><input type="checkbox" checked={draft.credential_required} onChange={(event) => setDraft({ ...draft, credential_required: event.target.checked })} />需要 API Key</label><label><input type="checkbox" checked={draft.allow_story_data} onChange={(event) => setDraft({ ...draft, allow_story_data: event.target.checked })} />允许经作者确认后发送故事数据</label><label><input type="checkbox" checked={draft.supports_reasoning_effort} onChange={(event) => setDraft({ ...draft, supports_reasoning_effort: event.target.checked })} />支持 reasoning_effort</label><label><input type="checkbox" disabled={draft.protocol !== "openai_chat_completions"} checked={draft.streaming_enabled} onChange={(event) => setDraft({ ...draft, streaming_enabled: event.target.checked })} />新模型默认流式</label></div>
      <div className="button-row">{editingProfileId && <button disabled={busy} onClick={cancelEdit}>取消编辑</button>}<button className="primary-button" disabled={busy} onClick={() => void saveProfile()}>{busy ? "正在保存…" : editingProfileId ? "保存修改" : "保存第三方配置"}</button></div>
    </section>
  </div>;
}

function sanitizeDraft(value: unknown): Draft | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<Draft> & Record<string, unknown>;
  if ("api_key" in candidate || "authorization" in candidate || "credential" in candidate) return null;
  if (
    typeof candidate.id !== "string"
    || typeof candidate.display_name !== "string"
    || typeof candidate.base_url !== "string"
    || !["openai_chat_completions", "openai_responses", "deepseek_chat"].includes(String(candidate.protocol))
    || !["json_schema", "json_object", "prompt_only"].includes(String(candidate.structured_output_mode))
    || typeof candidate.models !== "string"
    || typeof candidate.default_model !== "string"
  ) return null;
  return {
    id: candidate.id,
    display_name: candidate.display_name,
    base_url: candidate.base_url,
    protocol: candidate.protocol as Draft["protocol"],
    structured_output_mode: candidate.structured_output_mode as Draft["structured_output_mode"],
    is_local: Boolean(candidate.is_local),
    credential_required: Boolean(candidate.credential_required),
    allow_story_data: Boolean(candidate.allow_story_data),
    supports_reasoning_effort: Boolean(candidate.supports_reasoning_effort),
    streaming_enabled: Boolean(candidate.streaming_enabled),
    model_streaming: candidate.model_streaming && typeof candidate.model_streaming === "object"
      ? Object.fromEntries(Object.entries(candidate.model_streaming).map(([key, enabled]) => [key, Boolean(enabled)]))
      : {},
    models: candidate.models,
    default_model: candidate.default_model,
    input_price: typeof candidate.input_price === "string" ? candidate.input_price : "",
    output_price: typeof candidate.output_price === "string" ? candidate.output_price : "",
  };
}

function draftSummary(value: Draft): string[] {
  return [
    `配置：${value.display_name || value.id || "未命名"}`,
    `地址：${value.base_url || "未填写"}`,
    `协议：${value.protocol}`,
    `模型：${value.models.split(/[ ,\n]/u).filter(Boolean).length} 个`,
    "API Key：未保存到草稿",
  ];
}
