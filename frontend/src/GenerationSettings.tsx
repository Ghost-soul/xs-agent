import type { GenerationSpec, ProviderProfile } from "./api";
import { INPUT_TOKEN_LIMIT } from "./generationTokenLimits";
import { CharacterScope } from "./CharacterScope";
import { CreativeCardOptions, NarrativeCardPicker } from "./CreativeCards";
import { StageSettings } from "./LongformStage";
import { defaultDirection, eligibleProfiles, profileDefaults, type GenerationSetup, type NarrativeSelectionMode } from "./generationDefaults";

type Props = { spec: GenerationSpec; setup: GenerationSetup; profiles: ProviderProfile[]; history: { id: string; status: string; direction: string }[]; update: (patch: Partial<GenerationSpec>) => void; narrativeMode: NarrativeSelectionMode; setNarrativeMode: (mode: NarrativeSelectionMode) => void };

export function GenerationSettings({ spec, setup, profiles, history, update, narrativeMode, setNarrativeMode }: Props) {
  const available = eligibleProfiles(profiles);
  const profile = available.find((p) => p.id === spec.profile_id);
  const models = profile?.models ?? [];
  const cards = setup.available_cards ?? setup.style.matched_cards;
  return <>
    {!spec.focus_card_id && <p role="alert">请选择一张主题材。</p>}
    <div className="generation-grid">
      <label>主题材<select value={spec.focus_card_id} onChange={(e) => update({ focus_card_id: e.target.value, supporting_card_id: spec.supporting_card_id === e.target.value ? null : spec.supporting_card_id })}><option value="">请选择主题材</option><CreativeCardOptions cards={cards} layer="genre" /></select></label>
      <label>副题材（可选）<select value={spec.supporting_card_id ?? ""} onChange={(e) => update({ supporting_card_id: e.target.value || null })}><option value="">无</option><CreativeCardOptions cards={cards.filter((card) => card.id !== spec.focus_card_id)} layer="genre" /></select></label>
    </div>
    <label>叙事卡选择方式<select value={narrativeMode} onChange={(event) => setNarrativeMode(event.target.value as NarrativeSelectionMode)}><option value="random">随机两张</option><option value="manual">手动选择</option></select></label>
    {narrativeMode === "random"
      ? <p><strong>叙事卡：每次随机两张</strong>。建立新预览时，从活动叙事卡库中抽取两张不重复的卡，结果会显示在费用确认上方。同一阶段继续或恢复时沿用已抽取的卡；重新建立预览会重新抽取。</p>
      : <NarrativeCardPicker cards={cards} selected={spec.narrative_card_ids ?? []} update={(ids) => update({ narrative_card_ids: ids })} title="本次手动选择的叙事卡" description="所选卡片共同引导本阶段，不限两张；不选时由作者要求与正式故事资料引导。" />}
    <p>选卡方式与手动勾选仅用于新预览，已有阶段沿用预览中确认的卡片。</p>
    <label>本次想写什么（可选）<textarea value={spec.direction} maxLength={2000} placeholder={defaultDirection} onChange={(e) => update({ direction: e.target.value })} /></label>
    <p>题材卡提供世界背景与基础设定，选中的叙事卡引导人物关系、职业事件、故事机制与经典桥段。需要特殊世界规则的卡应与已有设定相容；可在上方补充本次想法。</p>
    <div className="generation-grid">
      <label>叙事单元上限<input type="number" min="1" max="6" value={spec.unit_limit ?? 3} onChange={(e) => update({ unit_limit: Number(e.target.value), stage_mode: "longform-v1", milestone_unit: null })} /></label>
      <label>费用上限（元）<input type="number" min="0" max="10000" step="0.1" value={String(spec.max_cost_cny)} onChange={(e) => update({ max_cost_cny: e.target.value })} /></label>
    </div>
    <p>当前安排：最多 {spec.unit_limit ?? 1} 个叙事单元，Chief 按剧情需要设计，Writer 每次完成一个单元的事件、人物选择、回应与后果，长短由故事决定。{spec.character_selection === "chief-auto-v1" ? "按剧情自动选角" : "使用指定人物范围"}，{spec.viewpoint || "Chief 安排视角"}。</p>
    {profile ? <p>模型：{profile.display_name} · Chief {spec.chief_model} · Writer {spec.writer_model}。其余角色默认跟随 Chief，已保存的独立模型设置继续保留。</p> : <p role="alert">没有可用模型配置，请到「模型配置」启用模型并补齐凭证。</p>}
    <details open={!!spec.author_boundaries}><summary>作者边界（可选）</summary><label>必须遵守的要求或禁区<textarea value={spec.author_boundaries ?? ""} maxLength={4000} placeholder="例如：仅写林青与江月的发展；本阶段不表白。无需填写常规关系许可。" onChange={(e) => update({ author_boundaries: e.target.value })} /></label></details>
    <fieldset><legend>阶段末反馈（可选）</legend>
      <p>Chief 决定故事方向，Writer 完成创作。正文完成后只做一次逻辑核对，检查事实、时间线和因果矛盾；不评价文风、节奏或题材比例，不自动改稿。</p>
      <label className="check"><input type="checkbox" checked={spec.enable_checker ?? true} onChange={(e) => update({ enable_checker: e.target.checked })} />完成正文后核对逻辑矛盾</label>
    </fieldset>
    <details><summary>调整自动配置（可选）</summary>
      <CharacterScope spec={spec} characters={setup.characters} update={update} />
      <label>指定视角（可选）<input value={spec.viewpoint ?? ""} maxLength={1000} placeholder="留空由 Chief 安排" onChange={(e) => update({ viewpoint: e.target.value })} /></label>
      <StageSettings spec={spec} update={update} history={history} />
      <div className="generation-grid">
        <label>模型配置<select value={spec.profile_id} onChange={(e) => update(profileDefaults(available.find((p) => p.id === e.target.value), spec))}><option value="">请选择</option>{available.map((p) => <option value={p.id} key={p.id}>{p.display_name}</option>)}</select></label>
        {(["chief_model", "writer_model"] as const).map((key) => <label key={key}>{key === "chief_model" ? "Chief 模型" : "Writer 模型"}<select value={spec[key]} onChange={(e) => update({ [key]: e.target.value, [key === "chief_model" ? "chief_tokenizer_id" : "writer_tokenizer_id"]: null })}><option value="">请选择</option>{models.map((m) => <option key={m.id} value={m.id}>{m.label ?? m.id}</option>)}</select></label>)}
      </div>
      <details><summary>输入、输出容量与等待时间</summary><p>输入及所有角色输出上限均默认为 100,000 tokens，实际请求仍受所选模型容量限制。推理和可见结果共用输出额度。修改额度会重新计算费用预览。</p><div className="generation-grid">
        <label>本次输入上限<input type="number" min="8000" max={INPUT_TOKEN_LIMIT} value={spec.input_limit} onChange={(e) => update({ input_limit: Number(e.target.value) })} /></label>
        <label>Chief 输出上限<input type="number" min="2000" max="100000" step="1000" value={spec.chief_output_limit ?? 100000} onChange={(e) => update({ chief_output_limit: Number(e.target.value) })} /></label>
        <label>Writer 输出上限<input type="number" min="4000" max="100000" step="1000" value={spec.writer_output_limit ?? 100000} onChange={(e) => update({ writer_output_limit: Number(e.target.value) })} /></label>
        <label>其他角色默认输出上限<input type="number" min="2000" max="100000" step="1000" value={spec.auxiliary_output_limit ?? 100000} onChange={(e) => update({ auxiliary_output_limit: Number(e.target.value) })} /></label>
        <label>单次最长等待（秒）<input type="number" min="60" max="1200" value={spec.timeout_seconds} onChange={(e) => update({ timeout_seconds: Number(e.target.value) })} /></label>
        {(["chief_tokenizer_id", "writer_tokenizer_id"] as const).map((key) => <label key={key}>{key === "chief_tokenizer_id" ? "Chief 分词配置" : "Writer 分词配置"}<input value={spec[key] ?? ""} onChange={(e) => update({ [key]: e.target.value || null })} /></label>)}
      </div></details>
      <details><summary>记忆、检查与编辑模型</summary><p>未单独选择时使用 Chief 模型，预览将冻结每个动作的模型与费用。</p><div className="generation-grid">{(["memory", "checker", "editor"] as const).map((role) => <div key={role}><label>{role} 模型<select value={spec.roles?.[role]?.model ?? ""} onChange={(e) => { const roles = { ...spec.roles }; if (e.target.value) roles[role] = { model: e.target.value, output_limit: spec.roles?.[role]?.output_limit ?? spec.auxiliary_output_limit ?? 100000 }; else delete roles[role]; update({ roles }); }}><option value="">继承 Chief</option>{models.map((m) => <option key={m.id} value={m.id}>{m.label ?? m.id}</option>)}</select></label><label>{role} 输出上限<input type="number" min={2000} max={100000} value={spec.roles?.[role]?.output_limit ?? spec.auxiliary_output_limit ?? 100000} onChange={(e) => update({ roles: { ...spec.roles, [role]: { model: spec.roles?.[role]?.model ?? spec.chief_model, tokenizer_id: spec.roles?.[role]?.tokenizer_id ?? spec.chief_tokenizer_id, output_limit: Number(e.target.value) } } })} /></label><input aria-label={`${role} 分词配置`} placeholder="本地分词配置（可选）" value={spec.roles?.[role]?.tokenizer_id ?? ""} onChange={(e) => update({ roles: { ...spec.roles, [role]: { model: spec.roles?.[role]?.model ?? spec.chief_model, output_limit: spec.roles?.[role]?.output_limit ?? spec.auxiliary_output_limit ?? 100000, tokenizer_id: e.target.value || null } } })} /></div>)}</div><p>需要改稿时，在读稿后选择范围并发起独立修订。</p><label className="check"><input type="checkbox" checked={spec.generate_title ?? false} onChange={(e) => update({ generate_title: e.target.checked })} />预留独立标题动作</label></details>
      <label className="check"><input type="checkbox" checked={spec.pause_after_plan ?? false} onChange={(e) => update({ pause_after_plan: e.target.checked })} />Chief 完成方案后先暂停，供我审阅</label>
    </details>
    {!spec.pause_after_plan && <p>确认预算后，由 Chief 设计、Writer 连续写作，Memory 保存事实接力。阶段结束后执行你选中的反馈，再由你读稿采用；普通剧情选择由创作角色推进。</p>}
  </>;
}
