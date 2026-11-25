"""Scope of ordinary typed edits while an external proposal holds the workflow slot."""

import json

from ..models import DirectorRunKind, DirectorRunStatus

_TARGET_FIELDS = {
    "video.set_prompt": "segment_id",
    "video.update_recipe_inputs": "segment_id",
    "video.set_recipe": "segment_id",
    "video.save_reference_image_plan": "segment_id",
    "video.select_reference_image": "segment_id",
    "video.clear_selection": "segment_id",
    "video.clear_upscale": "segment_id",
    "narration.update_cue": "cue_id",
    "narration.clear_selection": "cue_id",
    "dialogue.set_timing": "segment_id",
    "dialogue.select_candidate": "dialogue_line_id",
    "keyframe.select_candidate": "keyframe_id",
}

SCENE_EDIT_COMMANDS = frozenset(_TARGET_FIELDS) | {
    "keyframe.apply_operations",
    "script.apply_operations",
}
_PROJECT_SCRIPT_OPERATIONS = {"insert_scene", "move_scene", "delete_scene", "merge_adjacent_scenes"}


class ActiveTaskConflict(ValueError):
    def __init__(self, document):
        self.document = document
        super().__init__("请求与当前 Agent 任务的写入范围冲突。")


def active_write_run(document):
    run = document.active_director_run
    if run is not None and run.status not in {
        DirectorRunStatus.WAITING_USER,
        DirectorRunStatus.WAITING_JOBS,
    }:
        return run
    return None


def proposal_request_record(document, run):
    for record in document.application_requests:
        if not record.operation.endswith(".request_proposal") or record.receipt_json is None:
            continue
        data = json.loads(record.receipt_json).get("data")
        if isinstance(data, dict) and data.get("request_id") == run.id:
            return record, data
    return None, None


def _scene_owners(document):
    owners = {}

    def add(entity_id, scene_id):
        owners.setdefault(entity_id, set()).add(scene_id)

    for scene in document.director_script.scenes:
        add(scene.id, scene.id)
        for item in (*scene.shots, *scene.narration_cues):
            add(item.id, scene.id)
        for segment in scene.segments:
            add(segment.id, scene.id)
            for line in segment.dialogue_lines:
                add(line.id, scene.id)
    if document.keyframe_plan is not None:
        for binding in document.keyframe_plan.segments:
            for scene_id in owners.get(binding.segment_id, ()):
                add(binding.start_keyframe_id, scene_id)
                add(binding.end_keyframe_id, scene_id)
    return owners


def _keyframe_lock_scenes(document, run):
    if run.kind is not DirectorRunKind.KEYFRAME_PLAN or document.director_script is None:
        return set()
    record, data = proposal_request_record(document, run)
    if record is None:
        return set()
    arguments = data.get("request_arguments")
    if not isinstance(arguments, dict) or not isinstance(arguments.get("scope"), dict):
        return set()
    scene_ids = arguments["scope"].get("scene_ids")
    if not isinstance(scene_ids, list) or any(not isinstance(item, str) for item in scene_ids):
        return set()
    locked = set(scene_ids)
    owners = _scene_owners(document)
    if (
        not locked <= {scene.id for scene in document.director_script.scenes}
        or not run.cursor.pending_target_ids
        or any(
            not owners.get(target, set()) or not owners[target] <= locked
            for target in run.cursor.pending_target_ids
        )
    ):
        return set()
    return locked


def scene_ids_for_targets(document, targets):
    if not targets or document.director_script is None:
        return set()
    owners = _scene_owners(document)
    scenes = set()
    for entity_id in targets:
        if not isinstance(entity_id, str) or entity_id not in owners:
            return set()
        scenes.update(owners[entity_id])
    return scenes


def active_task_blocks_scenes(document, scene_ids):
    """An unknown or project-wide scope cannot prove independence."""
    run = active_write_run(document)
    if run is None:
        return False
    locked = _keyframe_lock_scenes(document, run)
    return not locked or not scene_ids or not locked.isdisjoint(scene_ids)


def _edit_target_ids(command):
    arguments = command.arguments
    if command.name in _TARGET_FIELDS:
        return (arguments.get(_TARGET_FIELDS[command.name]),)
    operations = arguments.get("operations")
    if not isinstance(operations, list):
        return ()
    targets = []
    for operation in operations:
        if not isinstance(operation, dict):
            return ()
        if command.name == "script.apply_operations":
            if operation.get("op") in _PROJECT_SCRIPT_OPERATIONS:
                return ()
            target_keys = (
                "scene_id",
                "shot_id",
                "segment_id",
                "right_scene_id",
                "right_segment_id",
            )
        else:
            target_keys = (
                "segment_id",
                "keyframe_id",
                "left_segment_id",
                "right_segment_id",
                "source_keyframe_id",
            )
        operation_targets = [
            operation[key] for key in target_keys if operation.get(key) is not None
        ]
        if not operation_targets:
            return ()
        targets.extend(operation_targets)
    return targets


def require_scene_edit_allowed(document, command):
    if command.name not in SCENE_EDIT_COMMANDS or active_write_run(document) is None:
        return
    scenes = scene_ids_for_targets(document, _edit_target_ids(command))
    if active_task_blocks_scenes(document, scenes):
        raise ActiveTaskConflict(document)
