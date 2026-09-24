import { useEffect, useState } from "react";

import { api, commandHeaders, jsonBody } from "./api";
import { ReferenceStylePanel } from "./ReferenceStylePanel";
import { cardCategory, isGenreCard, CreativeCardOptions, CreativeCardPool, type CreativeCardOption } from "./CreativeCards";

type GenreCard = CreativeCardOption & { tagline: string; reader_contract: string[]; writing_guidance?: string[]; combination_guidance?: string[]; quality_checks: string[]; failure_modes: string[]; source_file: string; match_score?: number; match_evidence?: string[] };
type StyleAsset = { name: string; purpose: string; strengthen: string[]; avoid: string[] };
type Profile = {
  selection_mode: "automatic" | "unselected" | "specified";
  genre_card_id: string | null;
  secondary_genre_card_ids: string[];
  matched_cards: GenreCard[];
  matched_mechanisms: GenreCard[];
  assets: StyleAsset[];
  policy: string;
};
export function StyleWorkspace({ projectId }: { projectId: string }) {
  const [cards, setCards] = useState<GenreCard[]>([]);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [assets, setAssets] = useState<StyleAsset[]>([]);
  const [selectionMode, setSelectionMode] = useState<"unselected" | "specified">("unselected");
  const [genreCardId, setGenreCardId] = useState<string | null>(null);
  const [secondaryGenreCardIds, setSecondaryGenreCardIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setProfile(null); setError(null);
    Promise.all([api<GenreCard[]>("/api/genre-quality-cards"), api<Profile>(`/api/projects/${projectId}/style-profile`)]).then(([catalog, current]) => {
      setCards(catalog); setProfile(current); setAssets(current.assets);
      setSelectionMode(current.selection_mode === "specified" ? "specified" : "unselected"); setGenreCardId(current.genre_card_id);
      setSecondaryGenreCardIds(current.secondary_genre_card_ids);
    }).catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "载入失败"));
  }, [projectId]);

  async function save() {
    setBusy(true); setError(null); setMessage(null);
    try {
      const saved = await api<Profile>(`/api/projects/${projectId}/style-profile`, {
        method: "PUT",
        headers: commandHeaders(),
        body: jsonBody({
          assets,
          selection_mode: selectionMode,
          genre_card_id: selectionMode === "specified" ? genreCardId : null,
          secondary_genre_card_ids: selectionMode === "specified" ? secondaryGenreCardIds : [],
          confirmed: true,
        }),
      });
      setProfile(saved); setSelectionMode(saved.selection_mode === "specified" ? "specified" : "unselected"); setGenreCardId(saved.genre_card_id);
      setSecondaryGenreCardIds(saved.secondary_genre_card_ids);
      setMessage("题材卡与风格资料已保存。正式章节没有改变。");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "保存失败"); }
    finally { setBusy(false); }
  }
  function addAsset() { setAssets((items) => [...items, { name: "新风格资产", purpose: "说明它希望改善什么阅读体验", strengthen: [], avoid: [] }]); }
  function updateAsset(index: number, key: keyof StyleAsset, value: string | string[]) { setAssets((items) => items.map((item, current) => current === index ? { ...item, [key]: value } : item)); }

  if (!profile) return error ? <div className="error-banner">{error}</div> : <div className="management-empty">正在载入质量偏好</div>;
  return <div className="style-workspace">
    <header><span className="eyebrow">作者偏好资料</span><h2>题材与叙事</h2><p>题材提供世界背景与基础设定；叙事引入具体的人物关系、职业事件、故事机制和经典桥段。题材选择一张主卡、最多一张副卡；新阶段可随机抽取两张叙事卡，也可在创作页手动多选。</p></header>
    {error && <div className="error-banner">{error}</div>}{message && <div className="success-banner">{message}</div>}
    <ReferenceStylePanel projectId={projectId} />
    <section className="style-assets">
      <div className="section-title"><div><h3>项目创作卡</h3><p>主副题材提供基础背景；这里保留项目叙事选择，新阶段的随机或手动选卡请在创作页设置。</p></div></div>
      <div className="field-grid">
        <label>创作卡模式<select aria-label="题材卡模式" value={selectionMode} onChange={(event) => { const next = event.target.value as "unselected" | "specified"; setSelectionMode(next); if (next === "unselected") { setGenreCardId(null); setSecondaryGenreCardIds([]); } }}><option value="unselected">暂不选择</option><option value="specified">作者指定</option></select></label>
        {selectionMode === "specified" && <label>主题材卡<select aria-label="主题材卡" value={genreCardId ?? ""} onChange={(event) => { const next = event.target.value || null; setGenreCardId(next); setSecondaryGenreCardIds((items) => [...new Set([...items, ...(cards.some((card) => card.id === genreCardId && card.layer === "narrative") ? [genreCardId!] : [])])].filter((id) => id !== next)); }}><option value="">请选择主题材卡</option><CreativeCardOptions cards={cards} layer="genre" /></select></label>}
      </div>
      {selectionMode === "specified" && genreCardId && !cards.some((card) => card.id === genreCardId && isGenreCard(card)) && <p role="alert">原主卡属于叙事，请选择一张主题材；原卡会保留为默认叙事选择。</p>}
      {selectionMode === "specified" && <CreativeCardPool cards={cards} primaryId={genreCardId} selected={secondaryGenreCardIds} update={setSecondaryGenreCardIds} />}
    </section>
    <section className="style-assets">
      <div className="section-title"><div><h3>{profile.selection_mode === "specified" ? "已保存的创作卡" : "尚未选择创作卡"}</h3><p>查看选卡带入的故事内容与展开方式；新预览读取新版，已保存批次沿用当时的卡文。</p></div><span>{profile.matched_cards.length} / {cards.length} 张</span></div>
      {profile.matched_cards.length ? profile.matched_cards.map((card, index) => <article className="genre-detail creative-card-detail" key={card.id}>
        <div><span className="card-label">{cardCategory(card)} · {index === 0 ? "主卡" : "可选卡"}</span><h3>{card.name}</h3><p>{card.tagline}</p>
          <span className="card-label">故事内容与体验</span><ul>{card.reader_contract.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
        <div><span className="card-label">情节展开与写法</span>{card.writing_guidance?.length ? <ul>{card.writing_guidance.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="muted-copy">这张历史卡尚未提供独立写法说明。</p>}
          {!!card.combination_guidance?.length && <><span className="card-label">怎样搭配</span><ul>{card.combination_guidance.map((item) => <li key={item}>{item}</li>)}</ul></>}
          <details><summary>回看提示（按需取用）</summary><ul>{card.quality_checks.map((item) => <li key={item}>{item}</li>)}</ul></details>
        </div>
      </article>) : <div className="longform-empty">选择“作者指定”后，按需要搭配题材与叙事；也可以保持未选。</div>}
    </section>
    <section className="style-assets"><div className="section-title"><div><h3>可复用风格资产</h3><p>把希望加强的动作、语感和观察方式写在这里，供创作时参考。</p></div><button type="button" disabled={assets.length >= 12} onClick={addAsset}>新增资产</button></div>{assets.length ? <div className="asset-list">{assets.map((asset, index) => <article key={index}><header><input value={asset.name} onChange={(event) => updateAsset(index, "name", event.target.value)} /><button type="button" onClick={() => setAssets((items) => items.filter((_, current) => current !== index))}>移除</button></header><textarea value={asset.purpose} onChange={(event) => updateAsset(index, "purpose", event.target.value)} /><RuleEditor label="希望强化" values={asset.strengthen} update={(values) => updateAsset(index, "strengthen", values)} /><RuleEditor label="补充边界（可选）" values={asset.avoid} update={(values) => updateAsset(index, "avoid", values)} /></article>)}</div> : <div className="longform-empty">尚未配置风格资产。已手选的创作卡仍可提供写法参考。</div>}</section>
    <aside className="style-policy"><strong>卡库说明</strong><span>{profile.policy}</span></aside>
    <div className="style-save"><button className="primary-button" disabled={busy || (selectionMode === "specified" && (!cards.some((card) => card.id === genreCardId && isGenreCard(card)) || secondaryGenreCardIds.filter((id) => cards.some((card) => card.id === id && isGenreCard(card))).length > 1))} onClick={() => void save()}>{busy ? "正在保存" : "保存质量偏好"}</button><span>保存资料不会修改正式正文。</span></div>
  </div>;
}

function RuleEditor({ label, values, update }: { label: string; values: string[]; update: (values: string[]) => void }) { return <label className="rule-editor"><span>{label}（每行一条）</span><textarea value={values.join("\n")} onChange={(event) => update(event.target.value.split("\n").map((item) => item.trim()).filter(Boolean))} /></label>; }
