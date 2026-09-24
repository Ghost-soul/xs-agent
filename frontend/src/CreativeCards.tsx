import { useState } from "react";

export type CreativeCardOption = { id: string; name: string; layer?: string };

export const isGenreCard = (card: CreativeCardOption) => (card.layer ?? "genre") === "genre";

export function cardCategory(card: CreativeCardOption): string {
  return isGenreCard(card) ? "题材卡" : "叙事卡";
}

export function CreativeCardOptions({ cards, layer }: { cards: CreativeCardOption[]; layer?: string }) {
  return <>{["genre", "narrative"].filter((value) => !layer || value === layer).map((value) => {
    const items = cards.filter((card) => (card.layer ?? "genre") === value);
    return items.length > 0 && <optgroup key={value} label={value === "genre" ? "题材卡" : "叙事卡"}>
      {items.map((card) => <option key={card.id} value={card.id}>{card.name}</option>)}
    </optgroup>;
  })}</>;
}

export function NarrativeCardPicker({ cards, selected, update, title = "叙事卡", description }: {
  cards: CreativeCardOption[]; selected: string[]; update: (ids: string[]) => void; title?: string; description?: string;
}) {
  const [query, setQuery] = useState("");
  const items = cards.filter((card) => card.layer === "narrative");
  const matches = items.filter((card) => card.name.toLowerCase().includes(query.trim().toLowerCase()));
  return <fieldset className="narrative-card-picker"><legend>{title}</legend>
    <p>已选 {selected.length} 张，可同时选择多张。{description}</p>
    <label>查找叙事卡<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：百合、种田文、先婚后爱" /></label>
    <div className="narrative-card-options">{matches.map((card) => <label className="check" key={card.id}>
      <input type="checkbox" aria-label={`叙事卡：${card.name}`} checked={selected.includes(card.id)}
        onChange={(event) => update(event.target.checked ? [...selected, card.id] : selected.filter((id) => id !== card.id))} />
      <span>{card.name}</span>
    </label>)}</div>
    {!matches.length && <p>没有匹配的叙事卡。</p>}
    {selected.length > 0 && <p className="muted-copy">已选：{selected.map((id) => items.find((card) => card.id === id)?.name ?? id).join("、")}</p>}
  </fieldset>;
}

export function CreativeCardPool({ cards, primaryId, selected, update }: {
  cards: CreativeCardOption[]; primaryId: string | null; selected: string[]; update: (ids: string[]) => void;
}) {
  const genres = cards.filter((card) => isGenreCard(card) && card.id !== primaryId);
  const genreIds = new Set(genres.map((card) => card.id));
  const secondary = selected.filter((id) => genreIds.has(id));
  const narratives = selected.filter((id) => cards.some((card) => card.id === id && card.layer === "narrative"));
  return <div className="creative-card-pool"><div className="creative-card-groups">
    <fieldset><legend>题材卡</legend><p>一张主题材，最多一张副题材，提供世界背景与基础设定。</p>
      <label>副题材（可选）<select value={secondary.length === 1 ? secondary[0] : ""} onChange={(event) => update([...(event.target.value ? [event.target.value] : []), ...narratives])}>
        <option value="">无</option><CreativeCardOptions cards={genres} layer="genre" />
      </select></label>
      {secondary.length > 1 && <p role="alert">旧资料中有多张副题材，请重新选择最多一张后保存。</p>}
    </fieldset>
    <NarrativeCardPicker cards={cards} selected={narratives} update={(ids) => update([...secondary, ...ids])} title="项目叙事选择" description="此处保存项目选择；创作页可选择随机或另行手动选卡。" />
  </div></div>;
}
