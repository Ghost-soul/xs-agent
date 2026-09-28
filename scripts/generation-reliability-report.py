"""Read-only operational counts. Never load prompts, prose, credentials or call models."""

import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, text

from novel_writer.core.config import Settings


def report(days: int) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    engine = create_engine(Settings().database_url)
    with engine.connect() as conn, conn.begin():
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '30s'"))
        calls = conn.execute(text("""
            SELECT c.id, c.batch_id, c.action, c.provider, c.model, c.status, c.error_code,
                   c.started_at, c.finished_at,
                   c.request->>'retry_of_call_id' AS retry_of,
                   c.request->'reliability_contract'->>'revision' AS contract,
                   c.request->'transport_observation' AS transport,
                   c.request->'writer_scale'->'current_unit_reference' AS writer_target,
                   length(regexp_replace(coalesce(c.response->>'text',''), '\\s', '', 'g'))
                     AS visible_characters
            FROM generation_calls c WHERE c.started_at >= :since
            ORDER BY c.started_at
        """), {"since": since}).mappings().all()
        batches = conn.execute(text("""
            SELECT b.status, b.next_action, b.state->>'units_finished' AS units_finished,
                   (SELECT c.status FROM generation_calls c WHERE c.batch_id=b.id
                    ORDER BY c.started_at DESC, c.slot DESC LIMIT 1) AS last_call
            FROM generation_batches b
            WHERE b.created_at >= :since AND b.authorized
        """), {"since": since}).mappings().all()
        repaired = conn.scalar(text("""
            SELECT count(DISTINCT c.id) FROM generation_calls c
            JOIN generation_artifacts a ON a.batch_id=c.batch_id
            WHERE c.started_at >= :since AND c.status='completed' AND a.kind='compilation'
              AND a.payload->>'call_id'=c.id::text
              AND (a.payload->'format_compatibility' IS NOT NULL OR EXISTS (
                  SELECT 1 FROM generation_artifacts failed
                  WHERE failed.batch_id=c.batch_id AND failed.kind='compilation'
                    AND failed.payload->>'call_id'=c.id::text
                    AND failed.payload->>'status'='local_failure'))
        """), {"since": since})
    engine.dispose()
    groups: dict[str, Counter[str]] = defaultdict(Counter)
    elapsed, first_bytes, header_waits, stream_idle = [], [], [], []
    writer_below = 0
    for row in calls:
        group = "/".join((row["provider"], row["model"], row["contract"] or "older",
                          row["action"].split(":")[0], "retry" if row["retry_of"] else "initial"))
        groups[group][row["status"]] += 1
        if row["finished_at"]:
            elapsed.append((row["finished_at"] - row["started_at"]).total_seconds())
        transport = row["transport"] or {}
        if transport.get("first_received_at") and transport.get("started_at"):
            first_bytes.append((datetime.fromisoformat(transport["first_received_at"])
                                - datetime.fromisoformat(transport["started_at"])).total_seconds())
        if transport.get("headers_received_at") and transport.get("started_at"):
            header_waits.append((datetime.fromisoformat(transport["headers_received_at"])
                                 - datetime.fromisoformat(transport["started_at"])).total_seconds())
        if isinstance(transport.get("max_stream_idle_seconds"), int | float):
            stream_idle.append(transport["max_stream_idle_seconds"])
        target = row["writer_target"] or {}
        if row["status"] == "completed" and target.get("min_characters"):
            writer_below += row["visible_characters"] < target["min_characters"]
    return {
        "since_utc": since.isoformat(), "calls": len(calls),
        "status_counts": dict(Counter(r["status"] for r in calls)),
        "initial_calls": sum(not r["retry_of"] for r in calls),
        "initial_successes": sum(not r["retry_of"] and r["status"] == "completed" for r in calls),
        "retry_calls": sum(bool(r["retry_of"]) for r in calls),
        "local_repair_or_revalidation_successes": repaired,
        "local_repair_count_note": "包括编译转换收据及原失败后本地成功；不含未记转换收据的兼容",
        "authorized_stages_created": len(batches),
        "completed_stages": sum(
            r["units_finished"] == "true" and not r["next_action"]
            and r["last_call"] == "completed" and r["status"] in {"needs_attention", "adopted"}
            for r in batches
        ),
        "stage_count_note": ("只统计窗口内建立并授权的阶段；"
                             "完成指单元已结束且末次调用成功，无后续动作"),
        "writer_completed_below_reference": writer_below,
        "call_seconds": {"samples": len(elapsed), "max": max(elapsed, default=None)},
        "first_byte_seconds": {"samples": len(first_bytes), "max": max(first_bytes, default=None)},
        "response_header_seconds": {
            "samples": len(header_waits), "max": max(header_waits, default=None),
        },
        "max_stream_idle_seconds": {
            "samples": len(stream_idle), "max": max(stream_idle, default=None),
        },
        "by_provider_model_contract_role_attempt": {k: dict(v) for k, v in sorted(groups.items())},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.days <= 366:
        parser.error("--days must be between 1 and 366")
    print(json.dumps(report(args.days), ensure_ascii=False, indent=2))
