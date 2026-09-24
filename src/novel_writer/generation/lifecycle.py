"""Compile chapter ownership and lifecycle evidence locally, never from model offsets."""

from typing import Any


def compile_lifecycle(
    collection: str,
    value: dict[str, Any],
    supplied: dict[str, Any],
    prior: dict[str, Any] | None,
    chapter: dict[str, Any] | None,
    spans: list[dict[str, Any]],
    observation: str,
    promise_action: str | None,
) -> dict[str, Any]:
    if collection not in {"scenes", "reader_promises", "foreshadowings"}:
        return value
    if chapter is None:
        raise ValueError("场景或生命周期变化缺少冻结的章节归属")
    if collection == "scenes":
        if prior is not None:
            raise ValueError("本次提取不能改写旧章场景")
        for field, expected in (
            ("chapter_id", chapter["id"]),
            ("chapter_ordinal", chapter["ordinal"]),
        ):
            if field in supplied and supplied[field] != expected:
                raise ValueError("场景章节归属错误")
            value[field] = expected
        return value
    if {"history", "lifecycle_events", "fulfilled_evidence"} & supplied.keys():
        raise ValueError("生命周期历史及坐标由本地证据编译，不接收模型自行填写")
    evidence = [
        {
            "chapter_id": chapter["id"],
            "chapter_ordinal": chapter["ordinal"],
            "start": p["start"],
            "end": p["end"],
            "quote": p["text"],
        }
        for p in spans
    ]
    if collection == "reader_promises":
        if promise_action is None:
            raise ValueError("承诺需要声明 promise_action")
        if prior and prior["status"] == "fulfilled":
            raise ValueError("已兑现承诺不能由提取器重新建立或重复兑现")
        status = "fulfilled" if promise_action == "fulfilled" else "open"
        if "status" in supplied and supplied["status"] != status:
            raise ValueError("承诺动作与状态冲突")
        value["status"] = status
        value["established_chapter"] = prior["established_chapter"] if prior else chapter["ordinal"]
        value["last_updated_chapter"] = chapter["ordinal"]
        value["history"] = [
            *(prior["history"] if prior else []),
            {
                "action": promise_action,
                "chapter_ordinal": chapter["ordinal"],
                "note": observation,
                "evidence": evidence,
            },
        ]
    else:
        before = prior["status"] if prior else "candidate"
        after = value.get("status", "active")
        if prior and before in {"fulfilled", "abandoned"}:
            raise ValueError("已关闭伏笔不能由提取器重新埋设；需作者另行处理")
        value["introduced_chapter"] = prior["introduced_chapter"] if prior else chapter["ordinal"]
        value["last_advanced_chapter"] = chapter["ordinal"]
        action = {
            "active": "introduced" if not prior else "advanced",
            "candidate": "introduced",
            "ready_for_payoff": "marked_ready",
        }.get(after, after)
        value["lifecycle_events"] = [
            *(prior["lifecycle_events"] if prior else []),
            {
                "action": action,
                "chapter_id": chapter["id"],
                "chapter_ordinal": chapter["ordinal"],
                "before_status": before,
                "after_status": after,
                "note": observation,
                "evidence": evidence,
            },
        ]
        if after == "fulfilled":
            value["fulfilled_chapter"] = chapter["ordinal"]
            value["fulfilled_evidence"] = evidence
    return value
