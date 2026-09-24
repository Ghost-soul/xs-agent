import { useCallback, useEffect, useRef, useState } from "react";

import { api, commandHeaders, jsonBody, type CharacterMindState, type Foreshadowing, type NarrativePhase, type OpenQuestion, type PlotHistoryDecision, type PlotThread, type ScheduledDevelopment, type StoryBlueprint, type StoryCharacter, type StoryForce, type WorldLoreEntry } from "./api";
import { clearDirtySurface, setDirtySurface } from "./unsavedChanges";
import { deleteLocalDraft, readLocalDraft, sha256, writeLocalDraft, type LocalDraft } from "./localDrafts";
import { requestConfirmation } from "./confirmation";

type Layer = "world" | "characters" | "outline";
export type BlueprintSection = Layer | "relationships" | "plot_threads" | "foreshadowings" | "narrative_position" | "history";
type BlueprintCharacterOption = { id: string; name: string; tier: StoryCharacter["tier"] };
type BlueprintSectionResponse = {
  project_id: string;
  state_version: number;
  state_version_id: string;
  section: BlueprintSection;
  data: { section: BlueprintSection } & Partial<StoryBlueprint>;
  context: { character_options: BlueprintCharacterOption[] };
};
const blueprintSections: Array<{ id: BlueprintSection; label: string }> = [
  { id: "world", label: "世界" },
  { id: "characters", label: "人物" },
  { id: "relationships", label: "关系" },
  { id: "outline", label: "故事框架" },
  { id: "plot_threads", label: "剧情线" },
  { id: "foreshadowings", label: "伏笔" },
  { id: "narrative_position", label: "叙事位置" },
  { id: "history", label: "历史" },
];
const worldKinds = ["时代背景", "力量体系", "重要物件", "地理地图", "王朝势力", "历史事件", "规则禁忌", "其他"];
const loreCategories: Array<{ id: WorldLoreEntry["category"]; name: string; hint: string }> = [
  { id: "world_structure", name: "世界结构", hint: "空间层级、其他世界与连接方式" },
  { id: "geography", name: "地理体系", hint: "大陆、国家、城市、秘境与禁区" },
  { id: "history", name: "历史背景", hint: "起源、黄金时代、灾变与当前时代" },
  { id: "power_system", name: "力量体系", hint: "来源、等级、能力规则与代价" },
  { id: "society", name: "社会体系", hint: "政治、经济、阶级与资源控制" },
  { id: "civilization", name: "文明体系", hint: "科技、文化、宗教、价值观与禁忌" },
  { id: "species", name: "种族生物", hint: "种族特征、关系、优势与限制" },
  { id: "faction", name: "势力体系", hint: "顶级、中层、底层势力与冲突" },
  { id: "core_secret", name: "底层秘密", hint: "世界规则、终极秘密与禁止事项" },
];

export function BlueprintWorkspace({ projectId, onSaved, initialSection = "world" }: { projectId: string; onSaved: () => Promise<void>; initialSection?: BlueprintSection }) {
  const [blueprint, setBlueprint] = useState<StoryBlueprint | null>(null);
  const baseBlueprintRef = useRef<string | null>(null);
  const [section, setSection] = useState<BlueprintSection>(initialSection);
  const dirtyKey = `blueprint:${projectId}:${section}`;
  const layer = sectionLayer(section);
  const [characterOptions, setCharacterOptions] = useState<BlueprintCharacterOption[]>([]);
  const [worldCategory, setWorldCategory] = useState<WorldLoreEntry["category"]>("world_structure");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [draftConflict, setDraftConflict] = useState<LocalDraft<string> | null>(null);
  const load = useCallback(async (signal?: AbortSignal) => {
    const response = await api<BlueprintSectionResponse>(`/api/projects/${projectId}/story-blueprint/sections/${section}`, { signal });
    const value = blueprintFromSection(response);
    baseBlueprintRef.current = JSON.stringify(value);
    setBlueprint(value);
    setCharacterOptions(response.context.character_options);
    clearDirtySurface(dirtyKey);
    const draft = await readLocalDraft<string>(dirtyKey).catch(() => null);
    if (draft && draft.payload_sha256 !== await sha256(JSON.stringify(value))) setDraftConflict(draft);
  }, [dirtyKey, projectId, section]);
  useEffect(() => {
    const controller = new AbortController();
    const report = (caught: unknown) => { if (!isAbortError(caught)) setError(message(caught)); };
    void load(controller.signal).catch(report);
    return () => controller.abort();
  }, [load]);
  useEffect(() => {
    if (!blueprint || baseBlueprintRef.current === null) return;
    const current = JSON.stringify(blueprint);
    const dirty = current !== baseBlueprintRef.current;
    setDirtySurface(dirtyKey, "故事资料", dirty, {
      save: async () => { if (dirty) await save(); },
      discard: async () => { await load(); await deleteLocalDraft(dirtyKey); },
    });
    if (dirty) {
      const timer = window.setTimeout(() => {
        void Promise.all([sha256(current), sha256(baseBlueprintRef.current ?? "")]).then(([payload_sha256, base_sha256]) => writeLocalDraft({
          schema_version: "local-draft-v1",
          draft_key: dirtyKey,
          project_id: projectId,
          surface: "blueprint",
          resource_id: projectId,
          base_version: blueprint.version,
          base_sha256,
          payload: current,
          payload_sha256,
          updated_at: new Date().toISOString(),
        })).catch(() => undefined);
      }, 500);
      return () => { window.clearTimeout(timer); clearDirtySurface(dirtyKey); };
    }
    return () => clearDirtySurface(dirtyKey);
  }, [blueprint, dirtyKey, load, projectId]);
  if (!blueprint) return <div className="blueprint-workspace">{error ?? "正在读取世界状态..."}</div>;
  const forces = blueprint.story_forces.filter((item) => item.layer === layer);
  const developments = blueprint.scheduled_developments.filter((item) => item.layer === layer);
  const loreEntries = blueprint.world_lore.filter((item) => item.category === worldCategory);
  const updateForce = (id: string, patch: Partial<StoryForce>) => setBlueprint((current) => current && ({ ...current, story_forces: current.story_forces.map((item) => item.id === id ? { ...item, ...patch } : item) }));
  const updateDevelopment = (id: string, patch: Partial<ScheduledDevelopment>) => setBlueprint((current) => current && ({ ...current, scheduled_developments: current.scheduled_developments.map((item) => item.id === id ? { ...item, ...patch } : item) }));
  async function save() {
    if (!blueprint) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const response = await api<BlueprintSectionResponse>(`/api/projects/${projectId}/story-blueprint/sections/${section}`, { method: "PUT", headers: commandHeaders(), body: jsonBody({ expected_state_version: blueprint.version, data: blueprintSectionPayload(blueprint, section), confirmed: true, reason: `作者保存${sectionLabel(section)}` }) });
      const saved = blueprintFromSection(response);
      baseBlueprintRef.current = JSON.stringify(saved); setBlueprint(saved); setCharacterOptions(response.context.character_options); clearDirtySurface(dirtyKey);
      setNotice(`已保存故事资料，正式版本 v${saved.version}。`);
      const followups = await Promise.allSettled([deleteLocalDraft(dirtyKey), onSaved()]);
      if (followups.some((result) => result.status === "rejected")) setNotice(`故事资料已保存为正式 v${saved.version}；关联页面或本地草稿同步失败，请刷新核对。`);
    } catch (caught) { setError(message(caught)); throw caught; } finally { setBusy(false); }
  }
  async function switchSection(next: BlueprintSection) {
    if (next === section || busy) return;
    if (baseBlueprintRef.current !== null && JSON.stringify(blueprint) !== baseBlueprintRef.current) {
      const accepted = await requestConfirmation({ title: "保存当前资料分区", message: `“${sectionLabel(section)}”有未保存修改。保存成功后才能切换到“${sectionLabel(next)}”。`, confirmLabel: "保存后切换" });
      if (!accepted) return;
      try { await save(); } catch { return; }
    }
    baseBlueprintRef.current = null; setBlueprint(null); setDraftConflict(null); setSection(next);
  }
  const removeForce = (id: string) => setBlueprint({ ...blueprint, story_forces: blueprint.story_forces.filter((item) => item.id !== id) });
  const removeDevelopment = (id: string) => setBlueprint({ ...blueprint, scheduled_developments: blueprint.scheduled_developments.filter((item) => item.id !== id) });
  return <div className="blueprint-workspace">
    {draftConflict && <section className="dialog-backdrop" role="presentation"><section className="dialog" role="dialog" aria-modal="true" aria-label="本地草稿恢复"><h2>发现本地故事资料草稿</h2><p>后端资料已经变化。请选择恢复、查看差异或丢弃；系统不会自动覆盖当前正式资料。</p><div className="dialog-actions"><button type="button" onClick={() => setDraftConflict(null)}>查看差异</button><button type="button" className="danger-command" onClick={() => { void deleteLocalDraft(dirtyKey); setDraftConflict(null); }}>丢弃草稿</button><button type="button" className="primary-button" onClick={() => { if (!draftConflict) return; try { setBlueprint(JSON.parse(draftConflict.payload) as StoryBlueprint); setDraftConflict(null); } catch { setError("本地草稿格式无效"); } }}>恢复草稿</button></div></section></section>}
    <header className="blueprint-header"><div><span className="eyebrow">故事基础</span><h2>世界观、人物与故事大纲</h2></div><div className="blueprint-actions"><span>基于 v{blueprint.version}</span><button className="primary-button" disabled={busy} onClick={() => void save()}>保存手工调整</button></div></header>
    {error && <div className="error-banner" role="alert">{error}</div>}{notice && <p role="status">{notice}</p>}
    <div className="blueprint-tabs" role="tablist" aria-label="故事资料分区">{blueprintSections.map((item) => <button role="tab" aria-selected={section === item.id} className={section === item.id ? "active" : ""} key={item.id} onClick={() => void switchSection(item.id)}>{item.label}</button>)}</div>
    {section === "characters" && <CharacterProfiles blueprint={blueprint} setBlueprint={setBlueprint} />}
    {section === "relationships" && <RelationshipsEditor blueprint={blueprint} characterOptions={characterOptions} setBlueprint={setBlueprint} />}
    {section === "world" && <section className="blueprint-band"><div className="band-heading"><div><h3>世界数据库</h3><p>固定设定、动态状态和底层规则分开保存；只记录会影响人物选择与剧情因果的内容。</p></div><button onClick={() => setBlueprint({ ...blueprint, world_lore: [...blueprint.world_lore, newLore(worldCategory)] })}>新增{loreCategories.find((item) => item.id === worldCategory)?.name}</button></div><div className="lore-category-grid">{loreCategories.map((category) => <button className={worldCategory === category.id ? "active" : ""} key={category.id} onClick={() => setWorldCategory(category.id)}><strong>{category.name}</strong><span>{category.hint}</span><small>{blueprint.world_lore.filter((item) => item.category === category.id).length}项</small></button>)}</div><div className="world-lore-list">{loreEntries.map((entry) => <WorldLoreEditor key={entry.id} entry={entry} update={(patch) => setBlueprint({ ...blueprint, world_lore: blueprint.world_lore.map((item) => item.id === entry.id ? { ...item, ...patch } : item) })} remove={() => setBlueprint({ ...blueprint, world_lore: blueprint.world_lore.filter((item) => item.id !== entry.id) })} />)}</div>{loreEntries.length === 0 && <p className="blueprint-empty">此分类尚无设定，作者可以手工补充。</p>}</section>}
    {section === "world" && <section className="blueprint-band"><div className="band-heading"><div><h3>规则引擎</h3><p>只写后文不能随意违反的能力、因果和禁止事项；规则越明确，冲突越可信。</p></div><button onClick={() => setBlueprint({ ...blueprint, world_rules: [...blueprint.world_rules, { id: crypto.randomUUID(), statement: "" }] })}>新增规则</button></div><div className="world-rule-list">{blueprint.world_rules.map((rule) => <div key={rule.id}><textarea aria-label="世界规则" rows={2} placeholder="例如：空间挪移最多跨越百米，连续使用会损伤神魂。" value={rule.statement} onChange={(event) => setBlueprint({ ...blueprint, world_rules: blueprint.world_rules.map((item) => item.id === rule.id ? { ...item, statement: event.target.value } : item) })} /><button className="danger-command" onClick={() => setBlueprint({ ...blueprint, world_rules: blueprint.world_rules.filter((item) => item.id !== rule.id) })}>删除</button></div>)}</div>{blueprint.world_rules.length === 0 && <p className="blueprint-empty">尚无底层规则。力量限制、代价和绝对禁区适合放在这里。</p>}</section>}
    {section === "outline" && <StoryFoundationEditor blueprint={blueprint} setBlueprint={setBlueprint} />}
    {section === "plot_threads" && <PlotThreads blueprint={blueprint} setBlueprint={setBlueprint} />}
    {section === "outline" && <OpenQuestions blueprint={blueprint} setBlueprint={setBlueprint} />}
    {section === "foreshadowings" && <ForeshadowingEditor blueprint={blueprint} setBlueprint={setBlueprint} />}
    {section === "outline" && <section className="blueprint-band"><div className="band-heading"><div><h3>未来发展节点</h3><p>记录作者设想的未来事件、触发条件和潜在影响。</p></div><button onClick={() => setBlueprint({ ...blueprint, scheduled_developments: [...blueprint.scheduled_developments, newDevelopment("outline")] })}>新增可能节点</button></div><div className="development-list">{developments.map((item) => <DevelopmentEditor key={item.id} item={item} update={(patch) => updateDevelopment(item.id, patch)} remove={() => removeDevelopment(item.id)} />)}</div>{developments.length === 0 && <p className="blueprint-empty">尚无未来发展节点，可以按需添加。</p>}</section>}
    {section === "outline" && <NarrativePhases blueprint={blueprint} setBlueprint={setBlueprint} />}
    {section === "outline" && forces.length > 0 && <details className="blueprint-advanced"><summary>旧版大纲约束（兼容资料）</summary><p>这些是旧结构保留下来的战略约束，可逐步迁移到核心框架、剧情线或开放问题。</p><div className="force-list">{forces.map((force) => <ForceEditor key={force.id} force={force} update={(patch) => updateForce(force.id, patch)} remove={() => removeForce(force.id)} />)}</div></details>}
    {section === "narrative_position" && <NarrativePositionEditor blueprint={blueprint} setBlueprint={setBlueprint} />}
    {section === "history" && <PlotHistory blueprint={blueprint} setBlueprint={setBlueprint} />}
    {section === "world" && developments.length > 0 && <details className="blueprint-advanced"><summary>已有世界自动变化（兼容旧数据）</summary><p>这些记录描述满足条件后世界自行发生的变化，不是世界背景本身。</p><div className="development-list">{developments.map((item) => <DevelopmentEditor key={item.id} item={item} update={(patch) => updateDevelopment(item.id, patch)} remove={() => removeDevelopment(item.id)} />)}</div></details>}
    {section === "world" && blueprint.story_forces.some((item) => item.layer === "world") && <details className="blueprint-advanced"><summary>旧版世界驱动力（兼容旧数据）</summary><p>保存后仍会保留，不再作为新世界设定的主要格式。</p><div className="force-list">{blueprint.story_forces.filter((item) => item.layer === "world").map((force) => <ForceEditor key={force.id} force={force} update={(patch) => updateForce(force.id, patch)} remove={() => removeForce(force.id)} />)}</div></details>}
  </div>;
}

function isAbortError(value: unknown) { return value instanceof DOMException && value.name === "AbortError"; }

function WorldLoreEditor({ entry, update, remove }: { entry: WorldLoreEntry; update: (patch: Partial<WorldLoreEntry>) => void; remove: () => void }) {
  return <section className="world-lore-editor"><header><div><span>{entry.source === "author" ? "作者设定" : "正文提取"} · {entry.stability === "static" ? "固定" : "动态"}</span><input aria-label="设定名称" placeholder="设定名称" value={entry.name} onChange={(event) => update({ name: event.target.value })} /></div><button className="danger-command" onClick={remove}>删除</button></header><div className="blueprint-grid compact-blueprint-grid"><label>子分类<input placeholder="例如：等级体系、经济资源、顶级势力" value={entry.subsection} onChange={(event) => update({ subsection: event.target.value })} /></label><label>性质<select value={entry.stability} onChange={(event) => update({ stability: event.target.value as WorldLoreEntry["stability"] })}><option value="static">固定设定</option><option value="dynamic">动态状态</option></select></label><label className="wide">核心定义<textarea rows={4} placeholder="说明是什么、如何运行、有哪些限制，以及为什么影响剧情。" value={entry.summary} onChange={(event) => update({ summary: event.target.value })} /></label><label className="wide">关键细节（每行一条）<textarea rows={4} value={entry.details.join("\n")} onChange={(event) => update({ details: lines(event.target.value) })} /></label>{entry.evidence_quote && <label className="wide">正文证据<textarea readOnly rows={2} value={entry.evidence_quote} /></label>}</div></section>;
}

function CharacterProfiles({ blueprint, setBlueprint }: { blueprint: StoryBlueprint; setBlueprint: (value: StoryBlueprint) => void }) {
  const update = (id: string, patch: Partial<StoryCharacter>) => setBlueprint({ ...blueprint, characters: blueprint.characters.map((item) => item.id === id ? { ...item, ...patch } : item) });
  const counts = { A: blueprint.characters.filter((item) => (item.tier ?? "A") === "A").length, B: blueprint.characters.filter((item) => item.tier === "B").length, C: blueprint.characters.filter((item) => item.tier === "C").length };
  const add = (tier: StoryCharacter["tier"]) => setBlueprint({ ...blueprint, characters: [...blueprint.characters, newCharacter(tier)] });
  return <section className="blueprint-band"><div className="band-heading"><div><h3>分级人物库</h3><p>A级核心人物 {counts.A}/20（完整档案） · B级重要人物 {counts.B}/100（简化档案） · C级普通人物 {counts.C}（出现、关系与状态索引）。</p></div><div className="character-add-actions"><button disabled={counts.A >= 20} onClick={() => add("A")}>新增A级</button><button disabled={counts.B >= 100} onClick={() => add("B")}>新增B级</button><button onClick={() => add("C")}>新增C级</button></div></div><div className="world-lore-list">{blueprint.characters.map((character) => { const history = character.development_history ?? []; const forbidden = character.forbidden_behaviors ?? []; const tier = character.tier ?? "A"; return <section className="world-lore-editor" key={character.id}><header><div><span>{tier}级人物 · {tier === "A" ? "完整档案" : tier === "B" ? "简化档案" : "状态索引"}</span><input aria-label="人物姓名" placeholder="人物姓名" value={character.name} onChange={(event) => update(character.id, { name: event.target.value })} /></div><button className="danger-command" onClick={() => setBlueprint({ ...blueprint, characters: blueprint.characters.filter((item) => item.id !== character.id) })}>删除</button></header><div className="blueprint-grid compact-blueprint-grid"><label>人物级别<select value={tier} onChange={(event) => update(character.id, { tier: event.target.value as StoryCharacter["tier"] })}><option value="A">A级核心</option><option value="B">B级重要</option><option value="C">C级普通</option></select></label><label className="wide">人物定位<textarea rows={2} placeholder="身份、经历、欲望与长期处境。" value={character.description} onChange={(event) => update(character.id, { description: event.target.value })} /></label>{tier !== "C" && <><label className="wide">人物别名（每行一条）<textarea rows={2} value={(character.aliases ?? []).join("\n")} onChange={(event) => update(character.id, { aliases: lines(event.target.value) })} /></label><label className="wide">性格核心<textarea rows={3} placeholder="人物不可突变的核心性格。" value={character.personality} onChange={(event) => update(character.id, { personality: event.target.value })} /></label><label className="wide">说话风格<textarea rows={3} value={character.speech_style} onChange={(event) => update(character.id, { speech_style: event.target.value })} /></label><label className="wide">决策与行动模式<textarea rows={3} value={character.decision_style} onChange={(event) => update(character.id, { decision_style: event.target.value })} /></label><label className="wide">人物禁止行为（每行一条）<textarea rows={4} value={forbidden.join("\n")} onChange={(event) => update(character.id, { forbidden_behaviors: lines(event.target.value) })} /></label></>}<label className="wide">当前状态（作者维护）<textarea rows={3} placeholder="记录人物在当前正式剧情中的能力、处境、目标、关系等状态；保存为新版本后生效。" value={character.current_state} onChange={(event) => update(character.id, { current_state: event.target.value })} /></label>{tier !== "C" && <label className="wide">成长记录（作者维护）<textarea rows={Math.min(8, Math.max(3, history.length + 1))} placeholder="每行一条，记录人物已经发生并需要长期保留的成长、选择或关系变化。" value={history.join("\n")} onChange={(event) => update(character.id, { development_history: lines(event.target.value) })} /></label>}</div>{tier !== "C" && <><PortrayalProfileEditor value={character.portrayal_profile ?? emptyPortrayalProfile()} update={(portrayal_profile) => update(character.id, { portrayal_profile })} /><MindStateEditor value={character.mind_state ?? emptyMindState()} update={(mind_state) => update(character.id, { mind_state })} /></>}</section>; })}</div></section>;
}

function RelationshipsEditor({ blueprint, characterOptions, setBlueprint }: { blueprint: StoryBlueprint; characterOptions: BlueprintCharacterOption[]; setBlueprint: (value: StoryBlueprint) => void }) {
  type Relationship = StoryBlueprint["relationships"][number];
  const nameOf = (id: string) => characterOptions.find((item) => item.id === id)?.name ?? "未知人物";
  const update = (id: string, patch: Partial<Relationship>) => setBlueprint({ ...blueprint, relationships: blueprint.relationships.map((item) => item.id === id ? { ...item, ...patch } : item) });
  const add = () => {
    if (characterOptions.length < 2) return;
    setBlueprint({ ...blueprint, relationships: [...blueprint.relationships, { id: crypto.randomUUID(), source_character_id: characterOptions[0].id, target_character_id: characterOptions[1].id, relation_type: "ally", description: "" }] });
  };
  return <section className="blueprint-band"><div className="band-heading"><div><h3>人物关系</h3><p>关系独立于人物档案保存；这里的修改会在完整 StoryState 重建校验通过后形成新版本。</p></div><button disabled={characterOptions.length < 2} onClick={add}>新增关系</button></div>{characterOptions.length < 2 && <p className="blueprint-empty">至少需要两个当前人物才能建立关系。</p>}<div className="world-lore-list">{blueprint.relationships.map((relationship) => <section className="world-lore-editor" key={relationship.id}><header><div><span>{nameOf(relationship.source_character_id)} → {nameOf(relationship.target_character_id)}</span><input aria-label="关系类型" placeholder="例如：盟友、师徒、竞争者" value={relationship.relation_type} onChange={(event) => update(relationship.id, { relation_type: event.target.value })} /></div><button className="danger-command" onClick={() => setBlueprint({ ...blueprint, relationships: blueprint.relationships.filter((item) => item.id !== relationship.id) })}>删除</button></header><div className="blueprint-grid"><label>起点人物<select aria-label="起点人物" value={relationship.source_character_id} onChange={(event) => update(relationship.id, { source_character_id: event.target.value })}>{characterOptions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>目标人物<select aria-label="目标人物" value={relationship.target_character_id} onChange={(event) => update(relationship.id, { target_character_id: event.target.value })}>{characterOptions.filter((item) => item.id !== relationship.source_character_id || item.id === relationship.target_character_id).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="wide">关系说明<textarea rows={3} value={relationship.description} onChange={(event) => update(relationship.id, { description: event.target.value })} /></label></div></section>)}</div></section>;
}

function PortrayalProfileEditor({ value, update }: { value: StoryCharacter["portrayal_profile"]; update: (value: StoryCharacter["portrayal_profile"]) => void }) {
  return <details className="item-advanced" open><summary>人物塑造档案</summary><div className="blueprint-grid"><label className="wide">独立目标<textarea rows={2} value={value.independent_goal} onChange={(event) => update({ ...value, independent_goal: event.target.value })} /></label><label className="wide">不可替代能力<textarea rows={2} value={value.unique_competence} onChange={(event) => update({ ...value, unique_competence: event.target.value })} /></label><label className="wide">价值边界<textarea rows={2} value={value.value_boundary} onChange={(event) => update({ ...value, value_boundary: event.target.value })} /></label><label className="wide">注意力偏向（每行一条）<textarea rows={3} value={value.attention_bias.join("\n")} onChange={(event) => update({ ...value, attention_bias: lines(event.target.value) })} /></label><label className="wide">冲突方式<textarea rows={2} value={value.conflict_method} onChange={(event) => update({ ...value, conflict_method: event.target.value })} /></label><label className="wide">压力反应<textarea rows={2} value={value.stress_response} onChange={(event) => update({ ...value, stress_response: event.target.value })} /></label><label className="wide">反例（每行一条）<textarea rows={3} value={value.anti_examples.join("\n")} onChange={(event) => update({ ...value, anti_examples: lines(event.target.value) })} /></label></div></details>;
}

function MindStateEditor({ value, update }: { value: CharacterMindState; update: (value: CharacterMindState) => void }) {
  const list = (key: "beliefs" | "desires" | "fears") => value[key].map((item) => item.content).join("\n");
  const setList = (key: "beliefs" | "desires" | "fears", text: string) => update({ ...value, [key]: lines(text).map((content) => key === "beliefs" ? { content, confidence: 50 } : { content, intensity: 50 }) });
  return <details className="item-advanced" open><summary>人物心理状态 · Character Mind State</summary><div className="blueprint-grid"><label className="wide">当前信念（每行一条）<textarea rows={3} value={list("beliefs")} onChange={(event) => setList("beliefs", event.target.value)} /></label><label className="wide">当前欲望（每行一条）<textarea rows={3} value={list("desires")} onChange={(event) => setList("desires", event.target.value)} /></label><label className="wide">当前恐惧（每行一条）<textarea rows={3} value={list("fears")} onChange={(event) => setList("fears", event.target.value)} /></label><label className="wide">当前情绪（格式：情绪｜原因）<textarea rows={3} value={value.current_emotions.map((item) => `${item.emotion}｜${item.cause}`).join("\n")} onChange={(event) => update({ ...value, current_emotions: lines(event.target.value).map((item) => { const [emotion, cause = ""] = item.split("｜"); return { emotion, cause, intensity: 50 }; }) })} /></label><label className="wide">内在冲突（格式：一方｜另一方）<textarea rows={3} value={value.internal_conflicts.map((item) => `${item.side_a}｜${item.side_b}`).join("\n")} onChange={(event) => update({ ...value, internal_conflicts: lines(event.target.value).map((item) => { const [side_a, side_b = "待明确"] = item.split("｜"); return { side_a, side_b, pressure: 50 }; }) })} /></label><label className="wide">压力应对方式<textarea rows={2} value={value.coping_strategy} onChange={(event) => update({ ...value, coping_strategy: event.target.value })} /></label></div></details>;
}

function ForceEditor({ force, update, remove }: { force: StoryForce; update: (patch: Partial<StoryForce>) => void; remove: () => void }) {
  const world = force.layer === "world";
  return <section className="force-editor"><header><input aria-label="名称" placeholder={world ? "例如：九州修炼体系、北境王朝" : "约束名称"} value={force.name} onChange={(event) => update({ name: event.target.value })} /><button className="danger-command" onClick={remove}>删除</button></header><div className="blueprint-grid compact-blueprint-grid"><label>{world ? "设定类别" : "约束类别"}{world ? <select value={force.kind} onChange={(event) => update({ kind: event.target.value })}>{worldKinds.includes(force.kind) || <option>{force.kind}</option>}{worldKinds.map((kind) => <option key={kind}>{kind}</option>)}</select> : <input placeholder="人物目标、势力压力、主题..." value={force.kind} onChange={(event) => update({ kind: event.target.value })} />}</label><label>影响范围<input placeholder={world ? "例如：天下、北境、修士阶层" : "影响哪些章节或人物"} value={force.scope} onChange={(event) => update({ scope: event.target.value })} /></label><label className="wide">核心内容<textarea rows={4} placeholder={world ? worldPlaceholder(force.kind) : "用几句话说明这项约束为什么必须影响剧情。"} value={force.description} onChange={(event) => update({ description: event.target.value })} /></label><label className="check-label"><input type="checkbox" checked={force.active} onChange={(event) => update({ active: event.target.checked })} />当前有效</label></div><details className="item-advanced"><summary>补充细节（可选）</summary><div className="blueprint-grid"><label>当前状态<input value={force.state} onChange={(event) => update({ state: event.target.value })} /></label><label>重要程度<input type="number" min={0} max={100} value={force.influence} onChange={(event) => update({ influence: Number(event.target.value) })} /></label><label className="wide">目标或自然倾向<textarea rows={2} value={force.goal} onChange={(event) => update({ goal: event.target.value })} /></label><label className="wide">无人干预时的发展<textarea rows={2} value={force.trajectory} onChange={(event) => update({ trajectory: event.target.value })} /></label><label className="wide">人物、物资或关联对象（逗号分隔）<input value={force.resources.join(", ")} onChange={(event) => update({ resources: split(event.target.value) })} /></label></div></details></section>;
}

function DevelopmentEditor({ item, update, remove }: { item: ScheduledDevelopment; update: (patch: Partial<ScheduledDevelopment>) => void; remove: () => void }) {
  return <section className="development-editor"><header><input aria-label="可能节点名称" placeholder="例如：古神遗迹开启" value={item.name} onChange={(event) => update({ name: event.target.value })} /><button className="danger-command" onClick={remove}>删除</button></header><div className="blueprint-grid compact-blueprint-grid"><label>大致时期（可选）<input placeholder="例如：身份建立完成后；不要填固定章号" value={item.timing} onChange={(event) => update({ timing: event.target.value })} /></label><label>状态<select value={item.status} onChange={(event) => update({ status: event.target.value as ScheduledDevelopment["status"] })}><option value="pending">尚未触发</option><option value="triggered">已经触发</option><option value="cancelled">不再采用</option></select></label><label className="wide">触发条件<input placeholder="哪些事实自然成立后，这个可能性才会出现？" value={item.trigger} onChange={(event) => update({ trigger: event.target.value })} /></label><label className="wide">可能事件<textarea rows={3} placeholder="记录可能发生的事件。" value={item.outcome} onChange={(event) => update({ outcome: event.target.value })} /></label><label className="wide">潜在影响<textarea rows={3} placeholder="可能改变哪些人物、矛盾或世界格局？" value={item.potential_impact ?? ""} onChange={(event) => update({ potential_impact: event.target.value })} /></label></div></section>;
}

function StoryFoundationEditor({ blueprint, setBlueprint }: { blueprint: StoryBlueprint; setBlueprint: (value: StoryBlueprint) => void }) {
  const foundation = blueprint.story_foundation;
  const update = (patch: Partial<typeof foundation>) => setBlueprint({ ...blueprint, story_foundation: { ...foundation, ...patch } });
  return <section className="blueprint-band"><div className="band-heading"><div><h3>故事核心框架</h3><p>只保存最稳定的主题、长期冲突和故事驱动力，不填写具体章节事件。</p></div></div><div className="blueprint-grid"><label className="wide">核心主题<textarea rows={2} placeholder="故事最终在讨论什么？" value={foundation.theme} onChange={(event) => update({ theme: event.target.value })} /></label><label className="wide">核心表达<textarea rows={2} placeholder="希望故事通过人物选择表达什么？" value={foundation.core_expression} onChange={(event) => update({ core_expression: event.target.value })} /></label><label>个人一侧<textarea rows={2} placeholder="主角或个人想要什么" value={foundation.personal_side} onChange={(event) => update({ personal_side: event.target.value })} /></label><label>对立一侧<textarea rows={2} placeholder="体系、势力或现实要求什么" value={foundation.opposing_side} onChange={(event) => update({ opposing_side: event.target.value })} /></label><label className="wide">长期核心冲突<textarea rows={2} placeholder="例如：个人自由 vs 体系秩序" value={foundation.long_term_conflict} onChange={(event) => update({ long_term_conflict: event.target.value })} /></label><label className="wide">主驱动力<textarea rows={2} placeholder="为什么这个故事能够长期持续？" value={foundation.primary_driver} onChange={(event) => update({ primary_driver: event.target.value })} /></label><label className="wide">次驱动力（每行一条）<textarea rows={3} value={foundation.secondary_drivers.join("\n")} onChange={(event) => update({ secondary_drivers: lines(event.target.value) })} /></label></div></section>;
}

function OpenQuestions({ blueprint, setBlueprint }: { blueprint: StoryBlueprint; setBlueprint: (value: StoryBlueprint) => void }) {
  const update = (id: string, patch: Partial<OpenQuestion>) => setBlueprint({ ...blueprint, open_questions: blueprint.open_questions.map((item) => item.id === id ? { ...item, ...patch } : item) });
  return <section className="blueprint-band"><div className="band-heading"><div><h3>当前开放问题</h3><p>记录故事尚未解决的问题，而不是伏笔清单；它告诉系统哪些答案仍不能遗忘。</p></div><button onClick={() => setBlueprint({ ...blueprint, open_questions: [...blueprint.open_questions, newOpenQuestion()] })}>新增问题</button></div><div className="world-lore-list">{blueprint.open_questions.map((item) => <section className="world-lore-editor" key={item.id}><header><div><span>{questionCategory(item.category)} · 重要度{item.importance}</span><input placeholder="尚未解决的问题" value={item.question} onChange={(event) => update(item.id, { question: event.target.value })} /></div><button className="danger-command" onClick={() => setBlueprint({ ...blueprint, open_questions: blueprint.open_questions.filter((entry) => entry.id !== item.id) })}>删除</button></header><div className="blueprint-grid"><label>分类<select value={item.category} onChange={(event) => update(item.id, { category: event.target.value as OpenQuestion["category"] })}><option value="character">角色问题</option><option value="world">世界问题</option><option value="plot">剧情问题</option></select></label><label>状态<select value={item.status} onChange={(event) => update(item.id, { status: event.target.value as OpenQuestion["status"] })}><option value="open">尚未解决</option><option value="partial">已有部分线索</option><option value="answered">已经回答</option><option value="abandoned">不再追踪</option></select></label><label>提出章节<input type="number" min={1} value={item.raised_chapter ?? ""} onChange={(event) => update(item.id, { raised_chapter: event.target.value ? Number(event.target.value) : null })} /></label><label>重要程度<input type="number" min={0} max={100} value={item.importance} onChange={(event) => update(item.id, { importance: Number(event.target.value) })} /></label><label className="wide">当前线索<textarea rows={2} value={item.current_clues} onChange={(event) => update(item.id, { current_clues: event.target.value })} /></label><label className="wide">答案计划（允许未知）<textarea rows={2} value={item.answer_plan} onChange={(event) => update(item.id, { answer_plan: event.target.value })} /></label></div></section>)}</div></section>;
}

function ForeshadowingEditor({ blueprint, setBlueprint }: { blueprint: StoryBlueprint; setBlueprint: (value: StoryBlueprint) => void }) {
  const items = blueprint.foreshadowings ?? [];
  const update = (id: string, patch: Partial<Foreshadowing>) => setBlueprint({ ...blueprint, foreshadowings: items.map((item) => item.id === id ? { ...item, ...patch } : item) });
  return <section className="blueprint-band"><div className="band-heading"><div><h3>伏笔生命周期</h3><p>管理埋设、轻推、推进、准备兑现和回收，沉默提醒供作者参考。</p></div><button onClick={() => setBlueprint({ ...blueprint, foreshadowings: [...items, newForeshadowing()] })}>新增伏笔</button></div><div className="world-lore-list">{items.map((item) => <section className="world-lore-editor" key={item.id}><header><div><span>{item.importance === "high" ? "高重要度" : item.importance === "medium" ? "中重要度" : "低重要度"} · 准备度{item.payoff_readiness}%</span><input placeholder="伏笔内容" value={item.content} onChange={(event) => update(item.id, { content: event.target.value })} /></div><button className="danger-command" onClick={() => setBlueprint({ ...blueprint, foreshadowings: items.filter((entry) => entry.id !== item.id) })}>删除</button></header><div className="blueprint-grid"><label>状态<select value={item.status} onChange={(event) => update(item.id, { status: event.target.value as Foreshadowing["status"] })}><option value="candidate">候选</option><option value="active">已埋设</option><option value="lightly_reinforced">轻微关联</option><option value="advanced">已推进</option><option value="ready_for_payoff">可兑现</option><option value="fulfilled">已回收</option><option value="abandoned">已废弃</option></select></label><label>重要度<select value={item.importance} onChange={(event) => update(item.id, { importance: event.target.value as Foreshadowing["importance"] })}><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label><label>首次章节<input type="number" min={1} value={item.introduced_chapter} onChange={(event) => update(item.id, { introduced_chapter: Number(event.target.value) })} /></label><label>最近推进章节<input type="number" min={1} value={item.last_advanced_chapter} onChange={(event) => update(item.id, { last_advanced_chapter: Number(event.target.value) })} /></label><label>沉默提醒阈值<input type="number" min={1} max={500} value={item.reminder_after_chapters} onChange={(event) => update(item.id, { reminder_after_chapters: Number(event.target.value) })} /></label><label>兑现准备度<input type="number" min={0} max={100} value={item.payoff_readiness} onChange={(event) => update(item.id, { payoff_readiness: Number(event.target.value) })} /></label><label className="wide">预计兑现区间<input value={item.expected_payoff} onChange={(event) => update(item.id, { expected_payoff: event.target.value })} /></label><label className="wide">回收条件<textarea rows={2} value={item.recovery_condition} onChange={(event) => update(item.id, { recovery_condition: event.target.value })} /></label><label className="wide">关联人物（逗号分隔）<input value={item.related_characters.join("，")} onChange={(event) => update(item.id, { related_characters: split(event.target.value) })} /></label></div></section>)}</div></section>;
}

function NarrativePhases({ blueprint, setBlueprint }: { blueprint: StoryBlueprint; setBlueprint: (value: StoryBlueprint) => void }) {
  const update = (id: string, patch: Partial<NarrativePhase>) => setBlueprint({ ...blueprint, narrative_phases: blueprint.narrative_phases.map((item) => item.id === id ? { ...item, ...patch } : item) });
  return <section className="blueprint-band"><div className="band-heading"><div><h3>故事阶段快照 · Narrative Phase</h3><p>描述阶段目标和阶段结束时应发生的变化，不指定逐章任务。</p></div><button onClick={() => setBlueprint({ ...blueprint, narrative_phases: [...blueprint.narrative_phases, newNarrativePhase()] })}>新增阶段</button></div><div className="development-list">{blueprint.narrative_phases.map((item) => <section className="development-editor" key={item.id}><header><input placeholder="阶段名称" value={item.name} onChange={(event) => update(item.id, { name: event.target.value })} /><button className="danger-command" onClick={() => setBlueprint({ ...blueprint, narrative_phases: blueprint.narrative_phases.filter((entry) => entry.id !== item.id) })}>删除</button></header><div className="blueprint-grid"><label>状态<select value={item.status} onChange={(event) => update(item.id, { status: event.target.value as NarrativePhase["status"] })}><option value="pending">尚未开始</option><option value="active">进行中</option><option value="completed">已完成</option><option value="cancelled">已取消</option></select></label><label className="wide">阶段目标<textarea rows={2} value={item.goal} onChange={(event) => update(item.id, { goal: event.target.value })} /></label><label className="wide">阶段变化<textarea rows={2} placeholder="阶段结束后，人物、冲突或世界局势发生什么结构性变化？" value={item.expected_change} onChange={(event) => update(item.id, { expected_change: event.target.value })} /></label></div></section>)}</div></section>;
}

function PlotHistory({ blueprint, setBlueprint }: { blueprint: StoryBlueprint; setBlueprint: (value: StoryBlueprint) => void }) {
  const update = (id: string, patch: Partial<PlotHistoryDecision>) => setBlueprint({ ...blueprint, plot_history: blueprint.plot_history.map((item) => item.id === id ? { ...item, ...patch } : item) });
  return <section className="blueprint-band"><div className="band-heading"><div><h3>剧情历史 · Plot History</h3><p>保存已完成、取消或修订过的战略决定，防止系统重复提出废弃方案。</p></div><button onClick={() => setBlueprint({ ...blueprint, plot_history: [...blueprint.plot_history, newPlotHistory()] })}>新增历史决定</button></div><div className="development-list">{blueprint.plot_history.map((item) => <section className="development-editor" key={item.id}><header><textarea rows={2} placeholder="曾经采用或考虑的计划" value={item.plan} onChange={(event) => update(item.id, { plan: event.target.value })} /><button className="danger-command" onClick={() => setBlueprint({ ...blueprint, plot_history: blueprint.plot_history.filter((entry) => entry.id !== item.id) })}>删除</button></header><div className="blueprint-grid"><label>结果<select value={item.status} onChange={(event) => update(item.id, { status: event.target.value as PlotHistoryDecision["status"] })}><option value="completed">已完成</option><option value="cancelled">已取消</option><option value="revised">已修改</option></select></label><label>发生章节<input type="number" min={1} value={item.chapter_ordinal ?? ""} onChange={(event) => update(item.id, { chapter_ordinal: event.target.value ? Number(event.target.value) : null })} /></label><label className="wide">原因<textarea rows={2} value={item.reason} onChange={(event) => update(item.id, { reason: event.target.value })} /></label></div></section>)}</div></section>;
}

function PlotThreads({ blueprint, setBlueprint }: { blueprint: StoryBlueprint; setBlueprint: (value: StoryBlueprint) => void }) {
  const update = (id: string, patch: Partial<PlotThread>) => setBlueprint({ ...blueprint, plot_threads: blueprint.plot_threads.map((item) => item.id === id ? { ...item, ...patch } : item) });
  return <section className="blueprint-band"><div className="band-heading"><div><h3>长期剧情线</h3><p>描述最终要解决什么、当前进度和关联人物，不拆成章节任务。</p></div><button onClick={() => setBlueprint({ ...blueprint, plot_threads: [...blueprint.plot_threads, newThread()] })}>新增剧情线</button></div><div className="world-lore-list">{blueprint.plot_threads.map((item) => <section className="world-lore-editor" key={item.id}><header><div><span>{item.thread_type === "main" ? "主线" : "支线"} · {item.progress}%</span><input placeholder="剧情线名称" value={item.name} onChange={(event) => update(item.id, { name: event.target.value })} /></div><button className="danger-command" onClick={() => setBlueprint({ ...blueprint, plot_threads: blueprint.plot_threads.filter((entry) => entry.id !== item.id) })}>删除</button></header><div className="blueprint-grid compact-blueprint-grid"><label>类型<select value={item.thread_type ?? "main"} onChange={(event) => update(item.id, { thread_type: event.target.value as PlotThread["thread_type"] })}><option value="main">主线</option><option value="side">支线</option></select></label><label>状态<select value={item.status} onChange={(event) => update(item.id, { status: event.target.value as PlotThread["status"] })}><option value="open">进行中</option><option value="resolved">已完成</option><option value="abandoned">已放弃</option></select></label><label>当前进度（%）<input type="number" min={0} max={100} value={item.progress ?? 0} onChange={(event) => update(item.id, { progress: Number(event.target.value) })} /></label><label>重要程度<input type="number" min={0} max={100} value={item.priority} onChange={(event) => update(item.id, { priority: Number(event.target.value) })} /></label><label className="wide">最终目标<textarea rows={2} placeholder="这条线最终想解决什么？" value={item.goal ?? ""} onChange={(event) => update(item.id, { goal: event.target.value })} /></label><label className="wide">当前进展<textarea rows={3} placeholder="已经发现什么、推进到哪里、仍缺什么。" value={item.summary} onChange={(event) => update(item.id, { summary: event.target.value })} /></label><label className="wide">关联人物（逗号分隔）<input value={(item.related_characters ?? []).join("，")} onChange={(event) => update(item.id, { related_characters: split(event.target.value) })} /></label></div></section>)}</div></section>;
}

function NarrativePositionEditor({ blueprint, setBlueprint }: { blueprint: StoryBlueprint; setBlueprint: (value: StoryBlueprint) => void }) {
  const update = (patch: Partial<StoryBlueprint["narrative_position"]>) => setBlueprint({ ...blueprint, narrative_position: { ...blueprint.narrative_position, ...patch } });
  return <section className="blueprint-band narrative-state-band"><div className="band-heading"><div><h3>当前叙事位置 · Story State</h3><p>记录故事当前的时间、地点、人物和未解决事项，供作者查阅与维护。</p></div></div><div className="blueprint-grid position-grid"><label>当前阶段<input placeholder="例如：身份建立阶段" value={blueprint.narrative_position.current_phase ?? ""} onChange={(event) => update({ current_phase: event.target.value })} /></label><label>当前时间<input placeholder="例如：帝国历302年深冬" value={blueprint.narrative_position.current_time} onChange={(event) => update({ current_time: event.target.value })} /></label><label>当前地点<input placeholder="例如：北境莽夫关" value={blueprint.narrative_position.current_location} onChange={(event) => update({ current_location: event.target.value })} /></label><label>当前主要人物<input placeholder="逗号分隔" value={(blueprint.narrative_position.current_characters ?? []).join("，")} onChange={(event) => update({ current_characters: split(event.target.value) })} /></label><label className="wide">最近重大事件<textarea rows={2} value={blueprint.narrative_position.recent_major_event ?? ""} onChange={(event) => update({ recent_major_event: event.target.value })} /></label><label className="wide">当前主要冲突<textarea rows={2} value={blueprint.narrative_position.current_conflict ?? ""} onChange={(event) => update({ current_conflict: event.target.value })} /></label><label className="wide">正在推进<textarea rows={2} value={blueprint.narrative_position.in_progress ?? ""} onChange={(event) => update({ in_progress: event.target.value })} /></label><label>下一步推演边界<input placeholder="只限制续写范围，不指定事件" value={blueprint.narrative_position.horizon} onChange={(event) => update({ horizon: event.target.value })} /></label><label className="wide">接续备注<textarea rows={3} value={blueprint.narrative_position.notes} onChange={(event) => update({ notes: event.target.value })} /></label></div></section>;
}

function blueprintFromSection(response: BlueprintSectionResponse): StoryBlueprint {
  const blueprint: StoryBlueprint = {
    project_id: response.project_id,
    version: response.state_version,
    version_id: response.state_version_id,
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
  const data = response.data;
  if (response.section === "characters") blueprint.characters = data.characters ?? [];
  else if (response.section === "relationships") blueprint.relationships = data.relationships ?? [];
  else if (response.section === "world") {
    blueprint.world_lore = data.world_lore ?? [];
    blueprint.world_rules = data.world_rules ?? [];
    blueprint.story_forces = data.story_forces ?? [];
    blueprint.scheduled_developments = data.scheduled_developments ?? [];
  } else if (response.section === "outline") {
    blueprint.story_foundation = data.story_foundation ?? blueprint.story_foundation;
    blueprint.open_questions = data.open_questions ?? [];
    blueprint.narrative_phases = data.narrative_phases ?? [];
    blueprint.story_forces = data.story_forces ?? [];
    blueprint.scheduled_developments = data.scheduled_developments ?? [];
  } else if (response.section === "plot_threads") blueprint.plot_threads = data.plot_threads ?? [];
  else if (response.section === "foreshadowings") blueprint.foreshadowings = data.foreshadowings ?? [];
  else if (response.section === "narrative_position") blueprint.narrative_position = data.narrative_position ?? blueprint.narrative_position;
  else blueprint.plot_history = data.plot_history ?? [];
  return blueprint;
}

function blueprintSectionPayload(blueprint: StoryBlueprint, section: BlueprintSection): Record<string, unknown> {
  if (section === "characters") return { section, characters: blueprint.characters };
  if (section === "relationships") return { section, relationships: blueprint.relationships };
  if (section === "world") return { section, world_lore: blueprint.world_lore, world_rules: blueprint.world_rules, story_forces: blueprint.story_forces, scheduled_developments: blueprint.scheduled_developments };
  if (section === "outline") return { section, story_foundation: blueprint.story_foundation, open_questions: blueprint.open_questions, narrative_phases: blueprint.narrative_phases, story_forces: blueprint.story_forces, scheduled_developments: blueprint.scheduled_developments };
  if (section === "plot_threads") return { section, plot_threads: blueprint.plot_threads };
  if (section === "foreshadowings") return { section, foreshadowings: blueprint.foreshadowings };
  if (section === "narrative_position") return { section, narrative_position: blueprint.narrative_position };
  return { section, plot_history: blueprint.plot_history };
}

function sectionLayer(section: BlueprintSection): Layer {
  return section === "world" || section === "characters" ? section : "outline";
}

function sectionLabel(section: BlueprintSection): string {
  return blueprintSections.find((item) => item.id === section)?.label ?? section;
}

function worldPlaceholder(kind: string): string { return ({ "时代背景": "时代处于什么阶段？社会秩序、主要矛盾和普通人的生活是什么样？", "力量体系": "列出主要修为层级、对应能力、限制和突破代价。", "重要物件": "记录法宝、武器、古籍或资源的作用、归属、限制与代价。", "地理地图": "说明区域位置、交通阻隔、资源与危险，不必绘制无剧情作用的细节。", "王朝势力": "说明主要王朝、宗门或组织的范围、诉求和冲突。", "历史事件": "只写仍在影响当下人物、制度或冲突的历史。", "规则禁忌": "记录不能被后文随意违反的世界规则和禁忌。" } as Record<string, string>)[kind] ?? "只写会影响人物选择和剧情因果的内容。"; }
function newDevelopment(layer: ScheduledDevelopment["layer"]): ScheduledDevelopment { return { id: crypto.randomUUID(), layer, name: "", trigger: "", outcome: "待填写", timing: "", probability: 100, status: "pending", potential_impact: "" }; }
function newThread(): PlotThread { return { id: crypto.randomUUID(), name: "", status: "open", priority: 50, last_advanced_chapter: null, summary: "", thread_type: "main", goal: "", progress: 0, related_characters: [] }; }
function newOpenQuestion(): OpenQuestion { return { id: crypto.randomUUID(), question: "", category: "plot", status: "open", raised_chapter: null, current_clues: "", answer_plan: "", importance: 50 }; }
function newNarrativePhase(): NarrativePhase { return { id: crypto.randomUUID(), name: "", goal: "", expected_change: "", status: "pending" }; }
function newPlotHistory(): PlotHistoryDecision { return { id: crypto.randomUUID(), plan: "", status: "cancelled", reason: "", chapter_ordinal: null }; }
function questionCategory(value: OpenQuestion["category"]): string { return value === "character" ? "角色问题" : value === "world" ? "世界问题" : "剧情问题"; }
function emptyMindState(): CharacterMindState { return { beliefs: [], desires: [], fears: [], current_emotions: [], internal_conflicts: [], coping_strategy: "" }; }
function newCharacter(tier: StoryCharacter["tier"] = "A"): StoryCharacter { return { id: crypto.randomUUID(), name: "", description: "", personality: "", speech_style: "", decision_style: "", forbidden_behaviors: [], current_state: "", development_history: [], library_status: "active", tier, mind_state: emptyMindState(), aliases: [], portrayal_profile: emptyPortrayalProfile(), location_id: null }; }
function emptyPortrayalProfile(): StoryCharacter["portrayal_profile"] { return { independent_goal: "", unique_competence: "", value_boundary: "", attention_bias: [], conflict_method: "", stress_response: "", voice_examples: [], anti_examples: [] }; }
function newForeshadowing(): Foreshadowing { return { id: crypto.randomUUID(), content: "", importance: "medium", introduced_chapter: 1, last_advanced_chapter: 1, expected_payoff: "", recovery_condition: "", status: "active", related_characters: [], related_plot_threads: [], related_open_questions: [], reader_visible: true, payoff_readiness: 0, reminder_after_chapters: 20, history: [] }; }
function newLore(category: WorldLoreEntry["category"]): WorldLoreEntry { return { id: crypto.randomUUID(), category, subsection: "", name: "", summary: "", details: [], stability: category === "faction" || category === "society" ? "dynamic" : "static", risk_level: "high", source: "author", first_seen_chapter: null, evidence_quote: "" }; }
function lines(value: string): string[] { return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean); }
function split(value: string): string[] { return value.split(/[,，]/).map((item) => item.trim()).filter(Boolean); }
function message(value: unknown): string { return value instanceof Error ? value.message : "操作失败"; }

