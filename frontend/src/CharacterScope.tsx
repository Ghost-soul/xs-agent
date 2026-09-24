import type { GenerationDetail, GenerationSpec } from "./api";

type Character = { id: string; name: string; library_status?: string };
type Props = { spec: GenerationSpec; characters: Character[]; update: (patch: Partial<GenerationSpec>) => void };

export function CharacterScope({ spec, characters, update }: Props) {
  const automatic = spec.character_selection === "chief-auto-v1";
  const eligible = automatic ? characters.filter((c) => c.library_status !== "retired") : characters;
  function toggle(id: string) {
    const ids = spec.character_ids ?? [];
    update({ character_ids: ids.includes(id) ? ids.filter((i) => i !== id) : [...ids, id] });
  }
  const choices = <fieldset><legend>{automatic ? "固定出场人物（可不选）" : "本章人物范围"}</legend>{eligible.map((c) => <label className="check" key={c.id}><input type="checkbox" checked={(spec.character_ids ?? []).includes(c.id)} onChange={() => toggle(c.id)} />{c.name}</label>)}{!eligible.length && <p>请先在故事资料中添加可用正式人物。</p>}</fieldset>;
  return <>
    <label>人物选择方式<select value={spec.character_selection ?? "manual"} onChange={(e) => update({ character_selection: e.target.value as GenerationSpec["character_selection"], character_ids: [...new Set([...(spec.character_ids ?? []), ...(spec.relationship_character_ids ?? [])])] })}><option value="chief-auto-v1">自动：Chief 根据题材和剧情选角</option><option value="manual">手动：只在我勾选的人物中设计</option></select></label>
    {automatic ? <><p>无需逐个勾选。系统从正式人物、当前现场和剧情关联整理候选，Chief 在设计事件时选定出场者。预览展示候选范围；最多选用 12 人，不新增调用。</p><details><summary>固定必须出场的人物（可选）</summary>{choices}</details></> : choices}
  </>;
}

export function CastPreview({ batch }: { batch: GenerationDetail }) {
  const selection = batch.snapshot.cast_selection as { candidates: { id: string; name: string; reasons: string[] }[]; omitted_ids: string[]; required_ids: string[] } | undefined;
  if (!selection) return null;
  const plan = batch.artifacts.find((a) => a.id === batch.state.plan_id)?.payload as { scenes?: { character_ids: string[]; event: string }[] } | undefined;
  const scenes = plan?.scenes ?? [];
  const chosen = new Set(scenes.flatMap((s) => s.character_ids));
  return <section aria-label="自动选角范围">
    <p>{scenes.length ? "Chief 已按有效方案选定本章人物：" : "自动选角候选已冻结，Chief 将在本次设计调用中选择。"}{selection.candidates.filter((c) => chosen.has(c.id)).map((c) => c.name).join("、")}</p>
    {scenes.map((s, i) => <p key={i}>{s.event}：{selection.candidates.filter((c) => s.character_ids.includes(c.id)).map((c) => c.name).join("、")}</p>)}
    <details><summary>核对候选人物与召回依据（{selection.candidates.length} 人）</summary><ul>{selection.candidates.map((c) => <li key={c.id}>{c.name}{selection.required_ids.includes(c.id) ? "（固定）" : ""}：{c.reasons.join("；")}</li>)}</ul><p>名单是候选范围，不代表所有人都会出场或组成关系。{selection.omitted_ids.length > 0 && `另有 ${selection.omitted_ids.length} 位人物未进入候选，包含退役或本次关联较弱的人物；需要时可调整固定人物或改为手选。`}</p></details>
  </section>;
}
