"""Keyframe planning facts shared by workset reads and submitted proposal validation."""

from ..codec.keyframe import serialize_character_references
from ..models import LocationImageKind
from ..planning_inputs import keyframe_inputs
from ..prompt_worksets import image_prompt_guide, keyframe_prompt_problems
from ..script_index import scene_segments
from .common import _json_value
from .video_recipes import keyframe_video_context


def _saved_keyframe_plan(document, scene_ids, *, segment_ids=None, provider_catalog=None):
    plan = document.keyframe_plan
    if plan is None:
        return {
            "status": "not_established",
            "source_script_revision": None,
            "segments": [],
            "keyframes": [],
        }
    script = document.director_script
    if script is None:
        raise ValueError("Keyframe 计划缺少当前导演脚本。")
    scoped_segment_ids = {
        segment.id
        for scene in script.scenes
        if scene.id in scene_ids
        for segment in scene_segments(scene)
    }
    bindings = [item for item in plan.segments if item.segment_id in scoped_segment_ids]
    frame_bindings: dict[str, list[dict[str, str]]] = {}
    if segment_ids is not None:
        bindings = [item for item in bindings if item.segment_id in segment_ids]
    frame_ids = {fid for item in bindings for fid in (item.start_keyframe_id, item.end_keyframe_id)}
    for binding in plan.segments if segment_ids is not None else bindings:
        frame_bindings.setdefault(binding.start_keyframe_id, []).append(
            {"segment_id": binding.segment_id, "slot": "start"}
        )
        frame_bindings.setdefault(binding.end_keyframe_id, []).append(
            {"segment_id": binding.segment_id, "slot": "end"}
        )
    return {
        "status": "established",
        "source_script_revision": plan.source_script_revision,
        "segments": [
            {
                "segment_id": item.segment_id,
                "start_keyframe_id": item.start_keyframe_id,
                "end_keyframe_id": item.end_keyframe_id,
                "start_enabled": item.start_enabled,
                "end_enabled": item.end_enabled,
                "reference_asset_ids": list(item.reference_asset_ids),
            }
            for item in bindings
        ],
        "keyframes": [
            {
                "id": item.id,
                "owner_segment_id": item.owner_segment_id,
                "predecessor_keyframe_id": item.predecessor_keyframe_id,
                "prompt_text": item.prompt_text,
                "prompt_problems": keyframe_prompt_problems(
                    document, item, provider_catalog=provider_catalog
                ),
                "visible_character_ids": list(item.visible_character_ids),
                "character_references": serialize_character_references(item),
                "reference_keyframe_ids": list(item.reference_keyframe_ids),
                "reference_location_image_ids": list(item.reference_location_image_ids),
                "extraction_suggested": item.extraction_suggested,
                "reuse_keyframe_id": item.reuse_keyframe_id,
                "selected_asset_id": item.selected_asset_id,
                "bindings": frame_bindings[item.id],
            }
            for item in plan.keyframes
            if item.id in frame_ids
        ],
    }


def _referenceable_keyframes(document, frame_ids=None):
    """全项目已有选中图的 Keyframe 索引：可作为其它帧的参考，提交时用其当前选中图。

    只给定位信息；画面正文等完整详情经 ``project.get_details(kind="keyframe")`` 按需读取。
    """
    plan = document.keyframe_plan
    script = document.director_script
    if plan is None or script is None:
        return []
    slots: dict[str, list[dict[str, str]]] = {}
    bindings = {item.segment_id: item for item in plan.segments}
    for scene in script.scenes:
        for segment in scene_segments(scene):
            binding = bindings.get(segment.id)
            if binding is None:
                continue
            for slot, keyframe_id in (
                ("start", binding.start_keyframe_id),
                ("end", binding.end_keyframe_id),
            ):
                if frame_ids is not None and keyframe_id not in frame_ids:
                    continue
                slots.setdefault(keyframe_id, []).append(
                    {"scene_id": scene.id, "segment_id": segment.id, "slot": slot}
                )
    return [
        {
            "keyframe_id": item.id,
            "bindings": slots[item.id],
            "visible_character_ids": list(item.visible_character_ids),
        }
        for item in plan.keyframes
        if item.selected_asset_id is not None and item.id in slots
    ]


def _keyframe_location_images(document, location_ids=None, image_ids=()):
    """全项目地点图片索引；referenceable 的条目可由本帧显式参考，提交其当前选中图。

    本 Scene 地点的图片强制说明在 ``location_library`` 中；其它地点图片的说明经
    ``project.get_details(kind="location_image")`` 按需读取。
    """
    return [
        {
            "image_id": image.id,
            "name": image.name,
            "kind": image.kind.value,
            "location_id": location.id,
            "location_name": location.name,
            "referenceable": image.kind is LocationImageKind.IMAGE
            and image.selected_asset_id is not None,
        }
        for location in document.location_library.locations
        for image in location.images
        if location_ids is None or location.id in location_ids or image.id in image_ids
    ]


def _keyframe_location_library(document, scenes):
    """只投影当前 Scene 使用的地点语义与图片强制说明；不含图片生成正文，不构成自动输入。"""
    location_ids = {scene.location_id for scene in scenes if scene.location_id is not None}
    result = []
    for location in document.location_library.locations:
        if location.id not in location_ids:
            continue
        value = _json_value(location)
        value["images"] = [
            {
                "id": image.id,
                "name": image.name,
                "kind": image.kind.value,
                "description": image.description,
                "has_selected_image": image.selected_asset_id is not None,
            }
            for image in location.images
        ]
        result.append(value)
    return result


def keyframe_workset_facts(
    document,
    scene_ids,
    targets,
    *,
    provider_recipes=(),
    provider_catalog=None,
    instruction="",
    focused=False,
):
    scenes, segments, boundaries = keyframe_inputs(
        document, tuple(targets or scene_ids), instruction, focused=focused
    )
    saved = _saved_keyframe_plan(
        document,
        scene_ids,
        segment_ids=set(segments) if focused else None,
        provider_catalog=provider_catalog,
    )
    nearby_frames = None
    location_ids = None
    referenced_images = set()
    if focused:
        nearby_frames = set()
        location_ids = {scene.location_id for scene in scenes}
        for frame in saved["keyframes"]:
            nearby_frames.update(
                (frame["id"], frame["predecessor_keyframe_id"], frame["reuse_keyframe_id"])
            )
            nearby_frames.update(frame["reference_keyframe_ids"])
            referenced_images.update(frame["reference_location_image_ids"])
    return {
        "video_inputs": [
            keyframe_video_context(document, segment.id, provider_recipes, provider_catalog)
            for scene in scenes
            for segment in scene.segments
        ],
        "scenes": _json_value(scenes),
        "context_segment_ids": [segment.id for scene in scenes for segment in scene.segments],
        "writable_segment_ids": list(segments),
        "target_segment_ids": list(segments),
        "required_shared_boundaries": _json_value(boundaries),
        "aspect_ratio": document.aspect_ratio.value,
        "saved_keyframe_plan": saved,
        "location_library": _keyframe_location_library(document, scenes),
        "location_images": _keyframe_location_images(document, location_ids, referenced_images),
        "image_prompt": image_prompt_guide(provider_catalog, skill_text=False),
        "referenceable_keyframes": _referenceable_keyframes(document, nearby_frames),
    }
