from typing import Any

from novel_writer.generation.schemas import BackgroundPlan, NovelStoryPlan, StagePlan


def reconcile_questions(
    previous: list[dict[str, Any]],
    plan: NovelStoryPlan | StagePlan | BackgroundPlan,
    answers: dict[str, str],
    deferred: list[str],
    note: str,
) -> list[dict[str, Any]]:
    items = {q["question"]: dict(q) for q in previous}
    for q in plan.questions:
        items.setdefault(
            q,
            {
                "question": q,
                "status": "pending",
                "scope": plan.question_scopes.get(q, "current_unit"),
            },
        )
    if (set(answers) | set(deferred)) - items.keys() or set(answers) & set(deferred):
        raise ValueError("答复/延期必须对应已有问题，且不能同时标记")
    for question, answer in answers.items():
        if not answer.strip() or len(answer) > 2000:
            raise ValueError("每项答复须有明确内容，最多2000字")
        items[question].update(status="answered", author_answer=answer)
    for question in deferred:
        items[question].update(status="deferred", author_reason=note)
    if any(q["status"] == "pending" and q["scope"] == "current_unit" for q in items.values()):
        raise ValueError("当前单元仍有未答问题；删除问题不等于答复，请逐项答复或明确延期并调整设计")
    return list(items.values())
