"""Small, read-only classifications from saved provider evidence."""

import json
from typing import Any

from novel_writer.db.models import GenerationCallRecord


def diagnostic(call: GenerationCallRecord) -> dict[str, Any] | None:
    raw = call.response or {}
    terminal = raw.get("terminal") or {}
    status = terminal.get("terminal_status")
    body = raw.get("raw_response")
    error: dict[str, Any] = {}
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
                error = parsed["error"]
        except ValueError:
            pass
    if status in {"http_401", "http_403"} or (
        isinstance(status, str) and status.startswith("http_")
        and "upstream access forbidden" in str(error.get("message", "")).casefold()
    ):
        return {
            "code": "provider_access_denied",
            "message": f"供应商返回 HTTP {str(status).removeprefix('http_')}，"
            "接口报告访问被拒绝；请联系接口管理员核查上游权限。"
            "这不是输出格式错误，反复原样重发不能修复上游权限；请先处理访问问题。"
            "原调用状态、响应与费用记录保留；未知结果仍须核查。",
        }
    if status in {"http_502", "http_503", "http_504"}:
        return {
            "code": "provider_gateway_error",
            "message": f"供应商网关返回 HTTP {str(status).removeprefix('http_')}；"
            "本地未收到完整结果，不能通过格式重验补齐。"
            "原响应已保存；调用结果及费用可能未知，请核查后再确认恢复。",
        }
    if status in {"http_400", "http_422"} or error.get("code") in (400, 422):
        return {
            "code": "provider_parameter_invalid",
            "message": "供应商拒绝请求参数（HTTP 400/422），原样重发不能修复。"
            "请核对模型容量与接口格式，修正后重新预览。 "
            + str(error.get("message", ""))[:1000],
            "provider_message": str(error.get("message", ""))[:1000],
        }
    code = terminal.get("incomplete_reason") or getattr(call, "error_code", None)
    if code in {
        "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout", "TimeoutError",
        "CancelledError",
    }:
        phase = {
            "ReadTimeout": "等待响应头或后续数据超时",
            "ConnectTimeout": "建立连接超时",
            "WriteTimeout": "发送请求超时",
            "PoolTimeout": "等待可用连接超时",
            "TimeoutError": "达到本次授权的总等待时间",
            "CancelledError": "响应接收被取消（可能为总期限到达或服务停止）",
        }[code]
        return {
            "code": "transport_cancelled" if code == "CancelledError" else "transport_timeout",
            "message": phase + "；供应商是否完成及费用可能未知。"
            "请核查原调用后再确认恢复，不会自动重发。",
        }
    return None
