"""外部 proposal 请求的纯读观察（proposal_observation）。

从 proposal_requests.py 下沉：只从原 request 与当前 run 派生观察语义，
不持久化第二状态机；供 reads 包与 proposal 生命周期模块级复用，
避免 L5 生命周期模块与 reads 包互相 import 成环。
"""

from __future__ import annotations

import json

from .keyframe_worksets import keyframe_workset_facts
from .proposal_scope import proposal_request_record


def proposal_workset_is_stale(
    document, run, prefix, request_data, *, provider_recipes=(), record=None
):
    source_revision = request_data.get("source_revision")
    if source_revision == document.revision:
        return False
    if prefix != "keyframe":
        return True
    if record is None:
        record, _ = proposal_request_record(document, run)
    if record is None or source_revision != record.result_revision:
        return True
    args = request_data.get("request_arguments")
    if not isinstance(args, dict) or set(args) != {"scope", "target_ids", "raw_user_instruction"}:
        return True
    try:
        current = keyframe_workset_facts(
            document,
            args["scope"]["scene_ids"],
            args["target_ids"],
            provider_recipes=provider_recipes,
            instruction=args["raw_user_instruction"],
        )
    except (KeyError, TypeError, ValueError):
        return True
    original = request_data.get("core_facts")
    if not isinstance(original, dict) or not isinstance(original.get("saved_keyframe_plan"), dict):
        return True

    def comparable(facts):
        # 局部计划引用的全局脚本版本是展示元数据；实际 Scene/Segment 与绑定已逐项比较。
        return {
            **facts,
            "saved_keyframe_plan": {
                key: value
                for key, value in facts["saved_keyframe_plan"].items()
                if key != "source_script_revision"
            },
        }

    return comparable(current) != comparable(original)


def proposal_phase(document, run, prefix, request_data, *, provider_recipes=(), record=None):
    status = run.status.value
    if status == "cancelled":
        for item in reversed(document.application_requests):
            if item.receipt_json is None or item.operation != f"{prefix}.reject_proposal":
                continue
            data = json.loads(item.receipt_json).get("data")
            if isinstance(data, dict) and data.get("request_id") == run.id:
                return "rejected"
        return status
    if status in {"completed", "failed"}:
        return status
    if status == "running" and proposal_workset_is_stale(
        document, run, prefix, request_data, provider_recipes=provider_recipes, record=record
    ):
        return "workset_stale"
    return "awaiting_agent_submission"


def proposal_observation(
    document,
    run,
    *,
    prefix=None,
    request_data=None,
    direct_request=False,
    provider_recipes=(),
    caller_id=None,
):
    """从原 request 与当前 run 派生观察语义；不持久化第二状态机。"""
    record = None
    if prefix is None or request_data is None:
        record, request_data = proposal_request_record(document, run)
        if record is None:
            return None
        prefix = record.operation.split(".")[0]
    source_revision = request_data.get("source_revision")
    request_arguments = request_data.get("request_arguments")
    request_arguments = request_arguments if isinstance(request_arguments, dict) else {}
    phase = proposal_phase(
        document, run, prefix, request_data, provider_recipes=provider_recipes, record=record
    )
    own_record = record is not None and record.caller_id == caller_id
    tool = f"{prefix}_proposal"
    target_ids = request_arguments.get("target_ids", [])
    actions = [
        {
            "action": f"{prefix}.get_proposal",
            "tool": tool,
            "tool_action": "get",
            "request_id": run.id,
            "target_ids": target_ids,
            "human_only": False,
            "requires_explicit_user_request": False,
        }
    ]
    if phase == "awaiting_agent_submission":
        actions = [
            {
                "action": f"{prefix}.submit_proposal",
                "tool": tool,
                "tool_action": "submit",
                "request_id": run.id,
                "target_ids": target_ids,
                "human_only": False,
                "requires_explicit_user_request": False,
            },
            *actions,
            {
                "action": f"{prefix}.cancel_proposal",
                "tool": tool,
                "tool_action": "cancel",
                "request_id": run.id,
                "target_ids": target_ids,
                "human_only": False,
                "requires_explicit_user_request": True,
            },
        ]
    elif phase == "workset_stale":
        actions.append(
            {
                "action": f"{prefix}.cancel_proposal",
                "tool": tool,
                "tool_action": "cancel",
                "request_id": run.id,
                "target_ids": target_ids,
                "human_only": False,
                "requires_explicit_user_request": True,
            }
        )
    if not direct_request:
        if not own_record:
            actions = []
        else:
            actions = [{**action, "requires_explicit_user_request": True} for action in actions]
    instruction = (
        "基于原始不可变工作集编写 proposal，并显式调用 submit；Core 没有后台模型任务可轮询。"
        if phase == "awaiting_agent_submission"
        else "原工作集已过期；先取消原请求，再按最新 revision 显式重新申请。"
        if phase == "workset_stale"
        else "proposal 请求已结束；get 仍可读取原工作集与当前结果。"
    )
    if not direct_request:
        instruction = (
            "当前连接凭据与此请求匹配，无法据此确认所属 Agent 会话。"
            "只有用户明确要求恢复该请求时才继续。"
            if own_record
            else "此请求由其它调用方持有，请由发起方处理。"
        )
    return {
        "phase": phase,
        "execution_responsibility": "external_agent"
        if phase == "awaiting_agent_submission"
        else None,
        "request_id": run.id,
        "proposal_kind": prefix,
        "source_revision": source_revision,
        "observation_revision": document.revision,
        "scope": request_data.get("scope", request_arguments.get("scope")),
        "target_ids": target_ids,
        "actions": actions,
        "instruction": instruction,
        **(
            {}
            if direct_request
            else {
                "request_association": "current_caller" if own_record else "other_caller_redacted",
                "session_association": "unverified",
                "request_key": record.idempotency_key if own_record else None,
                "created_at": run.created_at,
            }
        ),
    }
