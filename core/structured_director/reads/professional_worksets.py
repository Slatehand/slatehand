"""当前 revision 的专业读取入口；没有执行器、业务写入或任意文件访问。"""

import hashlib
import json

from core.director_proposals.keyframe_codec import output_schema as keyframe_schema
from core.director_proposals.location_codec import output_schema as location_schema
from core.director_proposals.narration_codec import _OUTPUT_SCHEMA as NARRATION_SCHEMA
from core.director_proposals.professional_contracts import CONTRACT_ONLY, CONTRACTS, read_contract
from core.director_proposals.reference_library_codec import REFERENCE_LIBRARY_OUTPUT_SCHEMA
from core.director_proposals.schema_gen import narration_language_schema
from core.director_proposals.script_codec import _OUTPUT_SCHEMA as SCRIPT_SCHEMA
from core.director_proposals.video_prompt import OUTPUT_SCHEMA as VIDEO_PROMPT_SCHEMA
from core.model_providers.image_prompt_skill import prompt_skill_index

from ..planning_inputs import (
    location_inputs,
    narration_inputs,
    reference_characters,
)
from ..prompt_worksets import (
    image_prompt_guide,
    video_prompt_workset,
)
from .common import _MAX_RESULT_OBJECTS, _exact_arguments, _ids, _json_value, _object_count
from .keyframe_worksets import keyframe_workset_facts
from .proposal_scope import active_task_blocks_scenes, scene_ids_for_targets
from .semantic_projection import build_semantic_projection, build_semantic_tree
from .workflow_state import workflow_state

_CAPABILITY_ACTION_NAMESPACE = {
    "reference_library": "reference",
    "script": "script",
    "narration": "narration",
    "keyframe": "keyframe",
    "location": "location",
    "video_prompt": "video",
}

# 工作集按范围裁剪后通常一次返回；分页只兜底多 Scene 读取，
# 页大小仍低于常见 Agent 的单次工具输出上限。
_AGENT_PAGE_BYTES = 60 * 1024
_AGENT_MIN_PAGE_BYTES = 4 * 1024


# revision 字段只说明观察时点，不属于内容：if_hash 与分页 cursor 只绑定内容，
# 无关写入推进 revision 时内容不变就仍然命中 / 可续读。
_REVISION_KEYS = frozenset({"observation_revision", "source_revision"})


def _without_revision(value: dict) -> dict:
    return {key: item for key, item in value.items() if key not in _REVISION_KEYS}


def _content_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _validate_if_hash(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        raise ValueError("if_hash 必须是 64 位小写十六进制内容 hash。")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError("if_hash 必须是 64 位小写十六进制内容 hash。") from exc
    return value


def _location_planning_library(document):
    """地点规划所需的已有地点语义；图片只给索引，说明与生成正文经 project.get_details 读取。"""
    result = []
    for location in document.location_library.locations:
        value = _json_value(location)
        value["images"] = [
            {
                "id": image.id,
                "name": image.name,
                "kind": image.kind.value,
                "has_selected_image": image.selected_asset_id is not None,
            }
            for image in location.images
        ]
        result.append(value)
    return result


def contract_read(document, arguments, *, provider_catalog=None):
    _exact_arguments(
        arguments,
        required={"capability"},
        optional={"if_hash", "include", "resource_names"},
    )
    include = arguments.get("include", [])
    if not isinstance(include, list) or any(item != "consistency-review" for item in include):
        raise ValueError("include 只接受 consistency-review。")
    if len(include) != len(set(include)):
        raise ValueError("include 不能重复。")
    body = read_contract(
        arguments["capability"], include=tuple(include), provider_catalog=provider_catalog
    )
    names = arguments.get("resource_names")
    if names is not None:
        resources = body["resources"]
        known = {item["name"] for item in resources}
        if (
            not isinstance(names, list)
            or any(not isinstance(name, str) or name not in known for name in names)
            or len(names) != len(set(names))
        ):
            raise ValueError("resource_names 必须是当前契约目录中不重复的资源名。")
        selected = set(names)
        if "consistency-review" in include:
            selected.add("shared/slatehand-consistency-review/SKILL.md")
        body = {
            **body,
            "resource_index": [
                {"name": r["name"], "sha256": r["sha256"], "bytes": len(r["text"].encode())}
                for r in resources
            ],
            "resources": [r for r in resources if r["name"] in selected],
        }
    digest = _content_hash(body)
    if_hash = _validate_if_hash(arguments.get("if_hash"))
    if if_hash is not None and if_hash == digest:
        return {"unchanged": True, "hash": digest}
    return {
        "project_id": document.project_id,
        "hash": digest,
        **body,
    }


def contract_page_read(document, arguments, *, provider_catalog=None):
    _exact_arguments(
        arguments,
        required={"capability"},
        optional={"if_hash", "include", "cursor", "page_bytes", "resource_names"},
    )
    query = {"capability": arguments["capability"]}
    if "if_hash" in arguments:
        query["if_hash"] = arguments["if_hash"]
    if "resource_names" in arguments:
        query["resource_names"] = arguments["resource_names"]
    if "include" in arguments:
        query["include"] = arguments["include"]
    return _agent_page(
        contract_read(document, query, provider_catalog=provider_catalog),
        query={
            "project_id": document.project_id,
            **{k: v for k, v in query.items() if k != "if_hash"},
        },
        cursor=arguments.get("cursor"),
        page_bytes=arguments.get("page_bytes", _AGENT_PAGE_BYTES),
    )


def speech_recipe(provider_recipes: list[dict]) -> dict | None:
    """旁白合成当前生效的 recipe。

    Agent 只填写其中 editable_params，按 recipe_id 写入 recipe_params。
    """
    return next(
        (item for item in provider_recipes if item["capability"] == "speech.synthesize"), None
    )


def video_packing(provider_recipes: list[dict]) -> dict | None:
    """脚本打包的软约束来源：当前视频插件每段可提交的参考音频条数与时长软提示。

    两者都只是写脚本时的提示；Core 不按它们拒绝脚本写入。没有绑定视频插件时为 None。
    """
    videos = [item for item in provider_recipes if item["capability"] == "video.generate"]
    if not videos:
        return None
    audio = [
        slot["maximum"]
        for item in videos
        for slot in item["recipe"]["slots"]
        if slot["role"] == "reference_audio" and slot["maximum"] is not None
    ]
    return {
        "plugin_label": videos[0]["plugin_label"],
        "dialogue_audio_maximum": max(audio) if audio else None,
        "soft_duration_limit_seconds": videos[0]["hints"].get("soft_duration_limit_seconds"),
    }


def workset_read(
    document,
    arguments,
    *,
    provider_recipes: list[dict] | None = None,
    provider_catalog=None,
    instruction="",
    caller_id=None,
    for_proposal_request=False,
    focused=False,
    fact_keys=None,
    for_agent_read=False,
):
    _exact_arguments(
        arguments,
        required={"capability", "scope"},
        optional={"target_ids", "if_hash"},
    )
    capability = arguments["capability"]
    if not isinstance(capability, str) or capability not in CONTRACTS:
        raise ValueError("未知专业能力。")
    if capability in CONTRACT_ONLY:
        raise ValueError(
            "该专业能力只有契约、没有工作集；项目上下文用 read_story / get_details 读取。"
        )
    scope = arguments["scope"]
    targets = _ids(arguments["target_ids"], "target_ids") if "target_ids" in arguments else None
    if capability in {"reference_library", "script"}:
        if scope != {"project_id": document.project_id} or targets is not None:
            raise ValueError("首次专业草稿必须指定当前项目 scope，不能带 target_ids。")
        facts = {"director_plan_markdown": document.director_plan_markdown}
        schema = REFERENCE_LIBRARY_OUTPUT_SCHEMA
        if capability == "script":
            facts["characters"] = _json_value(reference_characters(document))
            facts["speech_recipe"] = speech_recipe(provider_recipes or [])
            facts["video_packing"] = video_packing(provider_recipes or [])
            schema = SCRIPT_SCHEMA
        provenance = ["director_plan_markdown", "reference_library.characters"]
    elif capability == "location":
        if scope != {"project_id": document.project_id} or targets is not None:
            raise ValueError("地点规划工作集使用当前项目 scope，不能带 target_ids。")
        scenes, _existing = location_inputs(document)
        # 导演脚本是核心事实；原始导演方案全文与地点图片详情经 project.get_details 按需读取。
        facts = {
            "aspect_ratio": document.aspect_ratio.value,
            "scenes": _json_value(scenes),
            "location_library": _location_planning_library(document),
            "image_prompt": image_prompt_guide(provider_catalog, skill_text=False),
        }
        schema = location_schema()
        provenance = [
            "director_script.scenes",
            "reference_library.characters",
            "location_library",
        ]
    else:
        # 使用既有投影校验 Scene ID、重复项、scope，不能静默回退全项目。
        build_semantic_projection(document, {"level": "scene", "scope": scope})
        scene_ids = tuple(scope["scene_ids"])
        if targets is not None and len(targets) > 100:
            raise ValueError("target_ids 最多包含 100 项。")
        provenance = ["director_script", "reference_library", "assets"]
        if capability == "video_prompt":
            if targets is None or len(targets) != 1:
                raise ValueError("视频提示词工作集必须指定一个 Segment。")
            build_semantic_projection(
                document, {"level": "segment", "scope": scope, "ids": list(targets)}
            )
            facts, schema = (
                video_prompt_workset(
                    document,
                    targets[0],
                    provider_recipes=provider_recipes,
                    provider_catalog=provider_catalog,
                ),
                VIDEO_PROMPT_SCHEMA,
            )
        elif capability == "keyframe":
            if targets is not None:
                build_semantic_projection(
                    document, {"level": "segment", "scope": scope, "ids": list(targets)}
                )
            facts = keyframe_workset_facts(
                document,
                scene_ids,
                targets,
                provider_recipes=provider_recipes,
                provider_catalog=provider_catalog,
                instruction=instruction,
                focused=focused and targets is not None,
            )
            schema = keyframe_schema()
            provenance.extend(["keyframe_plan", "generations", "location_library"])
        else:
            cues = narration_inputs(document)
            scoped_ids = {
                cue.id
                for scene in document.director_script.scenes
                if scene.id in scene_ids
                for cue in scene.narration_cues
            }
            if targets is not None and set(targets) - scoped_ids:
                raise ValueError("Cue 未知或越出 scope。")
            selected_ids = set(targets) if targets is not None else scoped_ids
            facts = {
                "cues": _json_value(tuple(c for c in cues if c.id in selected_ids)),
                "story": build_semantic_tree(document, scene_ids, full_text=True),
                "speech_recipe": speech_recipe(provider_recipes or []),
            }
            schema = NARRATION_SCHEMA
    if capability in {"reference_library", "script", "narration"}:
        facts["narration_languages"] = [item.value for item in document.narration_languages]
        provenance.append("narration_languages")
    if capability in {"reference_library", "script"}:
        schema = narration_language_schema(schema, document.narration_languages)
    workflow = workflow_state(document, provider_catalog=provider_catalog, caller_id=caller_id)
    action_namespace = _CAPABILITY_ACTION_NAMESPACE[capability]
    blocked = workflow["blocked_actions"].get(action_namespace)
    write_scenes = scope.get("scene_ids")
    if targets and workflow["active_task"] is not None:
        write_scenes = scene_ids_for_targets(document, targets)
    if (
        blocked is None
        and not for_proposal_request
        and active_task_blocks_scenes(document, write_scenes)
    ):
        blocked = {"reason": "active_task_conflict"}
    not_current_stage = capability != workflow["current_stage"]
    result = {
        "workflow": workflow,
        "observation_revision": document.revision,
        "not_current_stage": not_current_stage,
        "not_current_stage_semantics": "informational_primary_workflow_relevance_only",
        "mutation_blocked": blocked is not None,
        "mutation_blocked_reason": None if blocked is None else blocked["reason"],
        "mutation_blocked_semantics": ("known_prerequisites_and_task_scope_final_checks_on_write"),
        "workset_version": 1,
        "project_id": document.project_id,
        "source_revision": document.revision,
        "capability": capability,
        "scope": scope,
        "target_ids": targets,
        "core_facts": facts,
        "output_schema": schema,
        "provenance": {"authority": "project.json", "fields": provenance},
        "contract": {"capability": capability, "read_command": "project.get_contract"},
    }
    if for_agent_read and "image_prompt" in facts:
        guide = facts["image_prompt"]
        guide["global_skills"] = prompt_skill_index(guide["global_skills"])
    if fact_keys is not None:
        if (
            not isinstance(fact_keys, list)
            or any(not isinstance(key, str) or key not in facts for key in fact_keys)
            or len(fact_keys) != len(set(fact_keys))
        ):
            raise ValueError("fact_keys 必须是当前 core_facts 中不重复的字段名。")
        result["available_fact_keys"] = list(facts)
        result["core_facts"] = {key: facts[key] for key in fact_keys}
        del result["output_schema"], result["workflow"]
    if_hash = _validate_if_hash(arguments.get("if_hash"))
    if for_agent_read or if_hash is not None:
        digest = _content_hash(_without_revision(result))
        if if_hash == digest:
            return {
                "unchanged": True,
                "hash": digest,
                "source_revision": document.revision,
            }
        result["hash"] = digest
    return result


def workset_page_read(
    document,
    arguments,
    *,
    provider_recipes: list[dict] | None = None,
    provider_catalog=None,
    caller_id=None,
):
    _exact_arguments(
        arguments,
        required={"capability", "scope"},
        optional={"target_ids", "if_hash", "cursor", "page_bytes", "keyframe_context", "fact_keys"},
    )
    context = arguments.get("keyframe_context")
    if "keyframe_context" in arguments and (
        context not in ("target", "scene")
        or arguments["capability"] != "keyframe"
        or not arguments.get("target_ids")
    ):
        raise ValueError(
            "keyframe_context 仅用于带 target_ids 的 keyframe 读取，可选 target 或 scene。"
        )
    query = {
        key: value
        for key, value in arguments.items()
        if key not in {"cursor", "page_bytes", "keyframe_context", "fact_keys"}
    }
    return _agent_page(
        workset_read(
            document,
            query,
            provider_recipes=provider_recipes,
            provider_catalog=provider_catalog,
            caller_id=caller_id,
            focused=context == "target",
            fact_keys=arguments.get("fact_keys"),
            for_agent_read=True,
        ),
        query={
            "project_id": document.project_id,
            **{k: v for k, v in arguments.items() if k not in {"cursor", "if_hash", "page_bytes"}},
        },
        cursor=arguments.get("cursor"),
        page_bytes=arguments.get("page_bytes", _AGENT_PAGE_BYTES),
    )


def _agent_page(result, *, query, cursor, page_bytes):
    if (
        isinstance(page_bytes, bool)
        or not isinstance(page_bytes, int)
        or not _AGENT_MIN_PAGE_BYTES <= page_bytes <= _AGENT_PAGE_BYTES
    ):
        raise ValueError(
            f"page_bytes 必须是 {_AGENT_MIN_PAGE_BYTES} 到 {_AGENT_PAGE_BYTES} 的整数。"
        )
    if (
        cursor is None
        and _object_count(result) <= _MAX_RESULT_OBJECTS
        and (
            len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            <= page_bytes
        )
    ):
        return result
    # 分页正文不含 revision 字段（由页头 observation_revision 给出），cursor 只绑定内容。
    encoded = json.dumps(
        _without_revision(result), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    content_sha256 = hashlib.sha256(encoded).hexdigest()
    query_digest = hashlib.sha256(
        json.dumps(
            {**query, "page_bytes": page_bytes, "content_sha256": content_sha256},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    offset = 0
    if cursor is not None:
        if not isinstance(cursor, str):
            raise ValueError("专业读取 cursor 不合法。")
        parts = cursor.split(":")
        if (
            len(parts) != 2
            or parts[0] != query_digest
            or not parts[1].isascii()
            or not parts[1].isdigit()
        ):
            raise ValueError("cursor 不属于当前专业查询，或查询内容已变化，请重新读取。")
        offset = int(parts[1])
        if offset <= 0 or offset >= len(encoded):
            raise ValueError("cursor 超出专业读取结果范围。")
    end = min(offset + page_bytes, len(encoded))
    while end > offset:
        try:
            content_chunk = encoded[offset:end].decode("utf-8")
            break
        except UnicodeDecodeError:
            end -= 1
    else:  # pragma: no cover - page minimum ensures at least one full codepoint
        raise RuntimeError("专业读取分页失败。")
    next_cursor = f"{query_digest}:{end}" if end < len(encoded) else None
    header = {
        key: result.get(key)
        for key in ("capability", "kind", "scope", "target_ids")
        if key in result or key in {"scope", "target_ids"}
    }
    return {
        "project_id": result["project_id"],
        "observation_revision": result.get("source_revision"),
        **header,
        "pagination": {
            "mode": "json_utf8_chunks",
            "content_sha256": content_sha256,
            "total_bytes": len(encoded),
            "offset": offset,
            "byte_count": end - offset,
            "next_cursor": next_cursor,
            "is_last": next_cursor is None,
            "instruction": (
                "使用与首次读取相同的参数和 next_cursor 续读；"
                "按 offset 拼接 content_chunk 后校验 SHA-256 并解析 JSON。"
            ),
        },
        "content_chunk": content_chunk,
    }
