import type { GenerationJourney as Journey } from "./generationFlow";

const stages = ["确认预算", "剧情设计", "正文创作", "可选反馈", "作者读稿"];
export function GenerationJourney({ journey }: { journey: Journey }) {
  return <ol className="generation-steps" aria-label="创作阶段">
    {stages.map((name, index) => <li key={name} aria-current={index === journey.phase ? "step" : undefined} className={index === journey.phase ? "current" : ""}>
      <span className="generation-step-number" aria-hidden="true">{index + 1}</span><span>{name}</span>
    </li>)}
  </ol>;
}
