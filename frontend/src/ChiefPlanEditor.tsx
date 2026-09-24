import { useState } from "react";
import type { GenerationDetail, StoryPlan } from "./api";

type Quotas = "focus_percent" | "transition_percent" | "other_percent";
type Scene = Omit<StoryPlan["scenes"][number], Quotas> & Partial<Pick<StoryPlan["scenes"][number], Quotas>>;
export type Plan = Omit<StoryPlan, "scenes" | "genre_causal_role" | "opening_focus_percent"> & {
  scenes: Scene[]; genre_causal_role?: string; opening_focus_percent?: number;
  world_context?: string; future_proposal?: string; story_questions?: string[];
};
const textArray = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");
function readablePlan(value: unknown): value is Plan {
  if (!value || typeof value !== "object") return false;
  const plan = value as Record<string, unknown>;
  const background = typeof plan.world_context === "string";
  return ["chapter_goal", "bridge", "major_turn"].every((key) => typeof plan[key] === "string")
    && (background || (typeof plan.genre_causal_role === "string" && typeof plan.opening_focus_percent === "number")) && textArray(plan.questions)
    && (plan.story_questions === undefined || textArray(plan.story_questions))
    && Array.isArray(plan.scenes) && plan.scenes.every((item: unknown) => {
      if (!item || typeof item !== "object") return false;
      const scene = item as Record<string, unknown>;
      return ["event", "choice_and_response", "consequence"].every((key) => typeof scene[key] === "string")
        && (background || ["focus_percent", "transition_percent", "other_percent"].every((key) => typeof scene[key] === "number")) && textArray(scene.character_ids);
    });
}
export function planMarkdown(plan: Plan): string {
  return ["# Chief 故事方案", "", "## 阶段目标", plan.chapter_goal, "", "## 与前文衔接", plan.bridge,
    "", "## 主要转折", plan.major_turn, "", ...(plan.world_context !== undefined ? ["## 世界观与背景依据", plan.world_context || "按正式设定自然展开"] : ["## 题材如何改变结果", plan.genre_causal_role ?? ""]),
    ...plan.scenes.flatMap((scene, i) => ["", `## 单元 ${i + 1}`, scene.event,
      `人物：${scene.character_ids.join("、")}`, "", `选择与回应：${scene.choice_and_response}`,
      `后果：${scene.consequence}`, ...(scene.focus_percent === undefined ? [] : [`篇幅：主导 ${scene.focus_percent}% / 过渡 ${scene.transition_percent}% / 其他 ${scene.other_percent}%`])]),
    "", "## 后续设想（尚未发生）", plan.future_proposal || "无",
    "", "## 剧情悬念", ...(plan.story_questions ?? []).map((q) => `- ${q}`),
    "", "## 作者待决问题", ...(plan.questions ?? []).map((q) => `- ${q}`), ""].join("\n");
}

function download(content: string, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type: `${type};charset=utf-8` }));
  const link = document.createElement("a"); link.href = url; link.download = filename; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ChiefPlanFiles({ batch }: { batch: GenerationDetail }) {
  const versions = batch.artifacts.filter((a) => a.kind === "plan");
  const [selected, setSelected] = useState("");
  const version = versions.find((a) => a.id === selected) ?? versions.find((a) => a.id === batch.state.plan_id);
  if (!version) return null;
  const plan = version.payload as Plan;
  const filename = `Chief方案-${batch.id.slice(0, 8)}-${String(version.sha256).slice(0, 8)}`;
  return <section className="generation-panel chief-plan-files"><h3>已保存方案与文件</h3>
    <p>Chief 原稿和每次保存的方案均保留。下载的是所选已保存版本；修改后先保存，后续写作才会使用。</p>
    <label>查看方案版本<select value={String(version.id)} onChange={(e) => setSelected(e.target.value)}>{versions.map((v, i) => <option value={String(v.id)} key={String(v.id)}>版本 {i + 1}{v.id === batch.state.plan_id ? " · 当前有效" : " · 历史记录"}{i === 0 ? " · Chief 初稿" : ""}</option>)}</select></label>
    <div className="button-row"><button onClick={() => download(planMarkdown(plan), `${filename}.md`, "text/markdown")}>下载方案文档</button><button onClick={() => download(JSON.stringify(plan, null, 2), `${filename}.json`, "application/json")}>下载完整方案 JSON</button></div>
    {version.id !== batch.state.plan_id && <details open><summary>所选历史版本（只读）</summary><pre>{planMarkdown(plan)}</pre></details>}
  </section>;
}

export function ChiefPlanFields({ value, onChange, writtenUnits, disabled, characters = [] }: { value: string; onChange: (value: string) => void; writtenUnits: number; disabled: boolean; characters?: { id: string; name: string }[] }) {
  let plan: Plan | null = null;
  try { const parsed: unknown = JSON.parse(value); if (readablePlan(parsed)) plan = parsed; } catch { /* Advanced JSON remains editable below. */ }
  const update = (patch: Partial<Plan>) => onChange(JSON.stringify({ ...plan, ...patch }, null, 2));
  return <fieldset disabled={disabled} className="chief-plan-fields">
    <legend>修改故事方向</legend>
    <p>{writtenUnits ? "已有正文：阶段目标与已写单元保留，可调整未写单元。" : "Writer 尚未开始，可修改阶段目标、转折、人物选择与各单元事件。"}保存会生成新版本，模型调用由继续操作启动。</p>
    {plan ? <>
      {([ ["chapter_goal", "阶段目标", 1200], ["bridge", "与前文衔接", 1200], ["major_turn", "主要转折", 1200] ] as const).map(([key, label, max]) => <label key={key}>{label}<textarea aria-label={label} value={plan![key]} maxLength={max} disabled={writtenUnits > 0 && key !== "bridge"} onChange={(e) => update({ [key]: e.target.value })} /></label>)}
      {plan.world_context !== undefined ? <label>世界观与背景依据<textarea maxLength={4000} value={plan.world_context} onChange={(e) => update({ world_context: e.target.value })} /></label> : <><label>题材如何改变结果<textarea maxLength={1200} value={plan.genre_causal_role} disabled={writtenUnits > 0} onChange={(e) => update({ genre_causal_role: e.target.value })} /></label><label>开篇过渡份额（%）<input type="number" min={0} max={20} value={plan.opening_focus_percent} onChange={(e) => update({ opening_focus_percent: Number(e.target.value) })} /></label></>}
      {plan.scenes.map((scene, i) => <fieldset key={i} disabled={i < writtenUnits}><legend>单元 {i + 1}{i < writtenUnits ? " · 已写，保留原方案" : ""}</legend>
        {characters.length > 0 && <div><span>出场人物</span><div className="generation-grid">{characters.map((character) => <label className="check" key={character.id}><input type="checkbox" checked={scene.character_ids.includes(character.id)} onChange={(e) => update({ scenes: plan!.scenes.map((s, n) => n === i ? { ...s, character_ids: e.target.checked ? [...s.character_ids, character.id] : s.character_ids.filter((id) => id !== character.id) } : s) })} />{`单元 ${i + 1} · ${character.name}`}</label>)}</div></div>}
        {([ ["event", "事件", 1400], ["choice_and_response", "选择与回应", 1400], ["consequence", "后果", 1000] ] as const).map(([key, label, max]) => <label key={key}>{`单元 ${i + 1} · ${label}`}<textarea aria-label={`单元 ${i + 1} · ${label}`} maxLength={max} value={scene[key]} onChange={(e) => update({ scenes: plan!.scenes.map((s, n) => n === i ? { ...s, [key]: e.target.value } : s) })} /></label>)}
        {plan.world_context === undefined && <div className="generation-grid">{([ ["focus_percent", "主导"], ["transition_percent", "过渡"], ["other_percent", "其他"] ] as const).map(([key, label]) => <label key={key}>{`单元 ${i + 1} · ${label}份额（%）`}<input type="number" min={0} max={100} value={scene[key]} onChange={(e) => update({ scenes: plan!.scenes.map((s, n) => n === i ? { ...s, [key]: Number(e.target.value) } : s) })} /></label>)}</div>}
      </fieldset>)}
      {"future_proposal" in plan && <label>后续设想（尚未发生）<textarea maxLength={2000} value={plan.future_proposal ?? ""} onChange={(e) => update({ future_proposal: e.target.value })} /></label>}
      {plan.story_questions && <label>剧情悬念（每行一项，由角色处理）<textarea value={plan.story_questions.join("\n")} onChange={(e) => update({ story_questions: e.target.value.split("\n") })} /></label>}
    </> : <p role="alert">方案 JSON 格式无效，请在高级编辑中修正后保存。</p>}
    <details><summary>高级编辑：完整方案 JSON</summary><label>有效故事方案（JSON）<textarea className="code-input" value={value} onChange={(e) => onChange(e.target.value)} /></label></details>
  </fieldset>;
}
