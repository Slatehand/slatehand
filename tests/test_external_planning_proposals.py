"""规划生命周期复用 Core，断连/取消/范围/已生成锁定由持久事实决定。"""

import asyncio
import copy
from dataclasses import replace

import pytest
from mcp import Client
from test_director_commands import _visual_project
from test_external_reference_proposals import restart
from test_external_script_proposals import agent, http
from test_prompt_worksets import fixture
from test_slatehand_mcp import endpoint

from core.director_commands import DirectorCommand


def request(document, kind, target_ids=None):
    scenes = document.director_script.scenes
    args = {
        "raw_user_instruction": "按当前完整上下文规划指定范围",
        "scope": {"scene_ids": [s.id for s in scenes]},
    }
    args["target_ids"] = target_ids or [scenes[0].segments[0].id]
    return DirectorCommand(f"{kind}.request_proposal", document.revision, "request", args)


def proposal_for(document, kind, target_ids=None):
    scenes = document.director_script.production_baseline_scenes
    writable = set(target_ids or [scenes[0].segments[0].id])
    return {
        "segments": [
            {
                "segment_id": segment.id,
                "continues_from_previous": False,
                "frame_strategy": "both",
                "start_prompt_text": "完整静态场景。",
                "start_visible_character_ids": [],
                "start_character_references": [],
                "end_prompt_text": "静态终点。",
                "end_visible_character_ids": [],
                "end_character_references": [],
            }
            for scene in scenes
            for segment in scene.segments
            if segment.id in writable
        ]
    }


def test_planning_restart_scope_atomic_submit_and_no_builtin(tmp_path):
    kind = "keyframe"

    async def run():
        store, _, doc, _ = await _visual_project(str(tmp_path))
        req = request(doc, kind)
        caller = agent(doc, req)
        before = store.path.read_bytes()
        for args in ({**req.arguments, "scope": {}}, {**req.arguments, "extra": True}):
            malformed = replace(req, arguments=args)
            assert (await http(restart(store), agent(doc, malformed), malformed)).status == 400
            assert store.path.read_bytes() == before
        start = await http(restart(store), caller, req)
        assert start.status == 200, start.body
        rid = start.body["data"]["request_id"]
        observation = start.body["data"]["proposal_observation"]
        assert observation["phase"] == "awaiting_agent_submission"
        assert observation["actions"][0]["action"] == f"{kind}.submit_proposal"
        assert start.body["workflow"]["required_next_actions"] == [observation["actions"][0]]
        assert start.body["data"]["request_arguments"] == req.arguments
        assert (await http(restart(store), caller, req)).body["replay"]
        doc = store.read()
        get = DirectorCommand(f"{kind}.get_proposal", doc.revision, "get", {"request_id": rid})
        before = store.path.read_bytes()
        observed = await http(restart(store), caller, get)
        assert observed.body["data"]["status"] == "running"
        assert observed.body["data"]["proposal_observation"]["phase"] == (
            "awaiting_agent_submission"
        )
        assert store.path.read_bytes() == before
        assert (await http(restart(store), replace(caller, id="another"), get)).status == 403
        assert (await http(restart(store), replace(caller, grants=()), get)).status == 403
        good = proposal_for(doc, kind)
        bads = [{**good, "extra": True}, {"segments": []}, {"segments": good["segments"] * 2}]
        wrong = copy.deepcopy(good)
        wrong["segments"][0]["segment_id"] = "f" * 32
        bads.append(wrong)
        missing_segment = copy.deepcopy(good)
        missing_segment["segments"][0]["frame_strategy"] = "invalid"
        bads.append(missing_segment)
        extra_segment = proposal_for(
            doc,
            kind,
            [
                doc.director_script.scenes[0].segments[0].id,
                doc.director_script.scenes[0].segments[1].id,
            ],
        )
        bads.append(extra_segment)
        for index, bad in enumerate(bads):
            submit = DirectorCommand(
                f"{kind}.submit_proposal",
                doc.revision,
                f"bad-{index}",
                {"request_id": rid, "proposal": bad},
            )
            result = await http(restart(store), caller, submit)
            assert result.status == 400, result.body
            assert store.path.read_bytes() == before
        submit = DirectorCommand(
            f"{kind}.submit_proposal", doc.revision, "submit", {"request_id": rid, "proposal": good}
        )
        assert (
            await http(restart(store), caller, replace(submit, expected_revision=1))
        ).status == 409
        result = await http(restart(store), caller, submit)
        assert result.status == 200, result.body
        finished = store.read()
        assert finished.active_director_run is None
        completed_get = DirectorCommand(
            f"{kind}.get_proposal",
            finished.revision,
            "get-completed",
            {"request_id": rid},
        )
        completed = await http(restart(store), caller, completed_get)
        assert completed.body["data"]["proposal_observation"]["phase"] == "completed"
        # Same-Scene context Shot is not writable; original slots and empty fields stay intact.
        untouched = doc.director_script.scenes[0].segments[1].id
        before_binding = next(b for b in doc.keyframe_plan.segments if b.segment_id == untouched)
        assert before_binding in finished.keyframe_plan.segments
        ids = {before_binding.start_keyframe_id, before_binding.end_keyframe_id}
        assert [f for f in finished.keyframe_plan.keyframes if f.id in ids] == [
            f for f in doc.keyframe_plan.keyframes if f.id in ids
        ]
        saved = store.path.read_bytes()
        assert (await http(restart(store), caller, submit)).body["replay"]
        assert store.path.read_bytes() == saved
        assert not finished.generations

    asyncio.run(run())


def test_keyframe_partial_scope_schema_and_second_shot_submit(tmp_path):
    async def run():
        store, _, doc, _ = await _visual_project(str(tmp_path))
        scene = doc.director_script.scenes[0]
        target = scene.segments[1].id
        req = request(doc, "keyframe", [target])
        caller = agent(doc, req)
        started = await http(restart(store), caller, req)
        assert started.status == 200, started.body
        data = started.body["data"]
        facts = data["core_facts"]
        assert facts["context_segment_ids"] == [segment.id for segment in scene.segments]
        assert facts["writable_segment_ids"] == [target]
        # 输出 schema 是通用格式，不逐段展开；授权范围由 writable_segment_ids 给出、Core 校验。
        assert "prefixItems" not in data["output_schema"]["properties"]["segments"]

        before = store.read()
        untouched_segment = scene.segments[0].id
        before_binding = next(
            item for item in before.keyframe_plan.segments if item.segment_id == untouched_segment
        )
        before_frames = {
            item.id: item
            for item in before.keyframe_plan.keyframes
            if item.id in {before_binding.start_keyframe_id, before_binding.end_keyframe_id}
        }
        submit = DirectorCommand(
            "keyframe.submit_proposal",
            before.revision,
            "submit-second",
            {
                "request_id": data["request_id"],
                "proposal": proposal_for(before, "keyframe", [target]),
            },
        )
        result = await http(restart(store), caller, submit)
        assert result.status == 200, result.body
        finished = store.read()
        assert (
            next(
                item
                for item in finished.keyframe_plan.segments
                if item.segment_id == untouched_segment
            )
            == before_binding
        )
        assert {
            item.id: item for item in finished.keyframe_plan.keyframes if item.id in before_frames
        } == before_frames
        assert not finished.generations

    asyncio.run(run())


def test_keyframe_non_contiguous_targets_keep_context_shots_unchanged(tmp_path):
    from keyframe_wave_fixtures import FiveShotScript

    async def run():
        store, _, doc, _ = await _visual_project(str(tmp_path), script_proposer=FiveShotScript())
        scene = doc.director_script.scenes[0]
        target_ids = [scene.segments[0].id, scene.segments[2].id]
        req = request(doc, "keyframe", target_ids)
        caller = agent(doc, req)
        started = await http(restart(store), caller, req)
        assert started.status == 200, started.body
        assert started.body["data"]["core_facts"]["writable_segment_ids"] == target_ids
        before = store.read()
        submit = DirectorCommand(
            "keyframe.submit_proposal",
            before.revision,
            "submit-non-contiguous",
            {
                "request_id": started.body["data"]["request_id"],
                "proposal": proposal_for(before, "keyframe", target_ids),
            },
        )
        result = await http(restart(store), caller, submit)
        assert result.status == 200, result.body
        finished = store.read()
        untouched_segments = {
            segment.id for segment in scene.segments if segment.id not in target_ids
        }
        assert [
            item
            for item in finished.keyframe_plan.segments
            if item.segment_id in untouched_segments
        ] == [
            item for item in before.keyframe_plan.segments if item.segment_id in untouched_segments
        ]
        untouched_frame_ids = {
            frame_id
            for item in before.keyframe_plan.segments
            if item.segment_id in untouched_segments
            for frame_id in (item.start_keyframe_id, item.end_keyframe_id)
        }
        assert [
            item for item in finished.keyframe_plan.keyframes if item.id in untouched_frame_ids
        ] == [item for item in before.keyframe_plan.keyframes if item.id in untouched_frame_ids]

    asyncio.run(run())


@pytest.mark.parametrize("action", ["cancel", "reject"])
def test_cancel_reject_cannot_accept_late_proposal(tmp_path, action):
    kind = "keyframe"

    async def run():
        store, _, doc, _ = await _visual_project(str(tmp_path))
        req = request(doc, kind)
        caller = agent(doc, req)
        start = await http(restart(store), caller, req)
        rid = start.body["data"]["request_id"]
        args = {"request_id": rid, **({"reason": "需要重新定义范围"} if action == "reject" else {})}
        end = DirectorCommand(f"{kind}.{action}_proposal", store.read().revision, "end", args)
        assert (await http(restart(store), caller, end)).status == 200
        assert (await http(restart(store), caller, end)).body["replay"]
        terminal = await http(
            restart(store),
            caller,
            DirectorCommand(
                f"{kind}.get_proposal",
                store.read().revision,
                "get-terminal",
                {"request_id": rid},
            ),
        )
        assert terminal.body["data"]["proposal_observation"]["phase"] == (
            "rejected" if action == "reject" else "cancelled"
        )
        late = DirectorCommand(
            f"{kind}.submit_proposal",
            store.read().revision,
            "late",
            {"request_id": rid, "proposal": proposal_for(store.read(), kind)},
        )
        before = store.path.read_bytes()
        late_response = await http(restart(store), caller, late)
        assert late_response.status == 409
        assert late_response.body["error"]["code"] == "business_rule_rejected"
        assert store.path.read_bytes() == before

    asyncio.run(run())


def test_generated_keyframe_nodes_and_aux_are_preserved(tmp_path):
    async def run():
        store, _, doc, _ = await fixture(tmp_path)
        plan, generations = doc.keyframe_plan, doc.generations
        req = request(doc, "keyframe")
        caller = agent(doc, req)
        start = await http(restart(store), caller, req)
        rid = start.body["data"]["request_id"]
        submit = DirectorCommand(
            "keyframe.submit_proposal",
            store.read().revision,
            "submit",
            {"request_id": rid, "proposal": proposal_for(store.read(), "keyframe")},
        )
        result = await http(restart(store), caller, submit)
        assert result.status == 200, result.body
        assert store.read().keyframe_plan == plan
        assert store.read().generations == generations

    asyncio.run(run())


def test_mcp_planning_request_get_submit_reaches_real_core(tmp_path):
    kind = "keyframe"

    async def prepare():
        store, _, document, _ = await _visual_project(str(tmp_path))
        request_command = request(document, kind)
        return store, document, request_command, agent(document, request_command)

    store, document, request_command, caller = asyncio.run(prepare())

    async def run(params):
        arguments = {
            "project_id": document.project_id,
            "expected_revision": document.revision,
            "idempotency_key": request_command.idempotency_key,
            "action": "request",
            "raw_user_instruction": request_command.arguments["raw_user_instruction"],
        }
        arguments["scene_ids"] = request_command.arguments["scope"]["scene_ids"]
        arguments["target_ids"] = request_command.arguments["target_ids"]
        tool = f"{kind}_proposal"
        async with Client(params) as client:
            started = await client.call_tool(tool, arguments)
            assert not started.is_error, started
            request_id = started.structured_content["data"]["request_id"]
            observed = await client.call_tool(
                tool,
                {
                    "project_id": document.project_id,
                    "expected_revision": store.read().revision,
                    "idempotency_key": "stdio-get",
                    "action": "get",
                    "request_id": request_id,
                },
            )
            assert not observed.is_error
            assert observed.structured_content["data"]["status"] == "running"
            submitted = await client.call_tool(
                tool,
                {
                    "project_id": document.project_id,
                    "expected_revision": store.read().revision,
                    "idempotency_key": "stdio-submit",
                    "action": "submit",
                    "request_id": request_id,
                    "proposal": proposal_for(store.read(), kind),
                },
            )
            assert not submitted.is_error, submitted
        assert store.read().active_director_run is None
        assert not store.read().generations

    with endpoint(tmp_path, store, restart(store), caller) as (params, _):
        asyncio.run(run(params))


def test_missing_persisted_scope_and_changed_workset_cannot_resume(tmp_path):
    from core.structured_director.errors import RevisionConflictError
    from core.structured_director.planning_proposals import PlanningProposalHandler

    async def run():
        store, _, doc, _ = await _visual_project(str(tmp_path))
        req = request(doc, "keyframe")
        caller = agent(doc, req)
        start = await http(restart(store), caller, req)
        data = start.body["data"]
        handler = PlanningProposalHandler("keyframe")
        import json

        parsed = handler.parse_proposal(json.dumps(proposal_for(doc, "keyframe")))
        before = store.path.read_bytes()
        missing = {k: v for k, v in data.items() if k != "request_arguments"}
        for saved, error in (
            (missing, ValueError),
            ({**data, "source_revision": 1}, RevisionConflictError),
        ):
            with pytest.raises(error):
                handler.submit(
                    store,
                    parsed,
                    request_id=data["request_id"],
                    expected_revision=store.read().revision,
                    request_data=saved,
                )
            assert store.path.read_bytes() == before

    asyncio.run(run())


def test_scene_edits_and_alarm_during_scoped_proposal(tmp_path, monkeypatch):
    from test_director_commands import _TwoSceneScriptCompiler
    from test_host_alarm import Recorder

    from core.host_alarm import HOST_ALARM
    from core.structured_director import StructuredDirectorService, StructuredDirectorStore

    async def prepare():
        return await _visual_project(str(tmp_path), script_proposer=_TwoSceneScriptCompiler())

    store, service, initial, _ = asyncio.run(prepare())
    scene_a, scene_b = initial.director_script.scenes
    project = {"project_id": initial.project_id}
    shared = replace(
        agent(initial, request(initial, "keyframe")),
        capabilities=frozenset(
            {"project.read", "project.mutate", "project.propose", "project.execute"}
        ),
    )
    spawn = Recorder()
    monkeypatch.setattr(HOST_ALARM, "_spawn", spawn)
    monkeypatch.setattr(HOST_ALARM, "_process", None)

    async def start(owner):
        result = await owner.call_tool(
            "keyframe_proposal",
            {
                **project,
                "action": "request",
                "expected_revision": initial.revision,
                "idempotency_key": "scene-a-plan",
                "raw_user_instruction": "只规划场景一。",
                "scene_ids": [scene_a.id],
                "target_ids": [scene_a.segments[0].id],
            },
        )
        assert not result.is_error, result.structured_content
        return result.structured_content["data"]

    async def run(server):
        async with Client(server) as owner, Client(server) as colleague:
            work = await start(owner)
            request_id = work["request_id"]
            source_revision = work["source_revision"]
            assert not work["proposal_observation"]["actions"][0]["requires_explicit_user_request"]

            opened = await colleague.call_tool("read_workflow", project)
            handoff = opened.structured_content["data"]
            active_task = handoff["workflow"]["active_task"]
            discovered = handoff["task_discovery"]["active"][0]
            assert active_task["request_id"] == discovered["id"] == request_id
            assert active_task["phase"] == discovered["status"] == "awaiting_agent_submission"
            assert discovered["run_status"] == "running"
            assert discovered["request_association"] == "current_caller"
            assert (
                active_task["session_association"]
                == discovered["session_association"]
                == "unverified"
            )
            assert all(
                action["requires_explicit_user_request"] for action in active_task["actions"]
            )
            assert handoff["workflow"]["required_next_actions"] == []
            assert handoff["allowed_actions"] == []
            assert handoff["allowed_actions_semantics"] == "human_ui_capabilities_not_next_steps"

            for scene, blocked in ((scene_a, True), (scene_b, False)):
                response = await colleague.call_tool(
                    "read_professional_workset",
                    {
                        **project,
                        "capability": "video_prompt",
                        "scene_ids": [scene.id],
                        "target_ids": [scene.segments[0].id],
                    },
                )
                facts = response.structured_content["data"]
                assert facts["observation_revision"] == source_revision
                assert facts["not_current_stage"] is True
                assert facts["mutation_blocked"] is blocked
                assert facts["mutation_blocked_reason"] == (
                    "active_task_conflict" if blocked else None
                )

            async def save(scene, number, key, **extra):
                return await colleague.call_tool(
                    "save_video_prompt",
                    {
                        **project,
                        "scene_ids": [scene.id],
                        "segment_id": scene.segments[number].id,
                        "expected_revision": source_revision,
                        "idempotency_key": key,
                        "prompt_text": f"保存视频正文 {number}",
                        **extra,
                    },
                )

            before = store.path.read_bytes()
            blocked = await save(scene_a, 1, "overlap")
            failure = blocked.structured_content
            assert failure["http_status"] == 409
            error = failure["error"]
            assert (error["code"], error["reason"]) == (
                "action_not_allowed",
                "active_task_conflict",
            )
            assert not error["state_changed"] and not error["automatic_retry_allowed"]
            assert error["active_task"]["request_id"] == request_id

            alarm = await colleague.call_tool(
                "ring_alarm", {**project, "message": "场景一等待提交"}
            )
            assert not alarm.is_error, alarm.structured_content
            assert alarm.structured_content["data"]["status"] == "ringing"
            assert store.path.read_bytes() == before

            first, second = await asyncio.gather(
                save(scene_b, 0, "scene-b-first"),
                save(scene_b, 1, "scene-b-second", recipe_inputs={"bindings": {}, "params": {}}),
            )
            for saved in (first, second):
                assert not saved.is_error, saved.structured_content
                assert (
                    saved.structured_content["workflow"]["stage_status"]
                    == "awaiting_agent_submission"
                )
            receipts = [item.structured_content["receipt"] for item in (first, second)]
            assert sorted(receipt["new_revision"] for receipt in receipts) == [
                source_revision + 1,
                source_revision + 2,
            ]
            assert any(
                receipt.get("rebased_from_revision") == source_revision for receipt in receipts
            )
            return request_id

    with endpoint(tmp_path, store, service, shared) as (server, _):
        request_id = asyncio.run(run(server))

    async def complete_after_restart():
        # Continue from the durable workset and the same Provider configuration.
        current = store.read()
        reopened = StructuredDirectorService(
            StructuredDirectorStore(store.path.parent, current.project_id),
            registry=service.provider_catalog,
        )
        foreign = replace(shared, id="another")
        workflow = DirectorCommand("project.get_workflow_state", current.revision, "read", {})
        result = await http(reopened, foreign, workflow)
        assert result.status == 200, result.body
        task = result.body["data"]["workflow"]["active_task"]
        assert task["phase"] == "awaiting_agent_submission"
        assert task["request_association"] == "other_caller_redacted"
        assert task["request_key"] is None and task["actions"] == []
        assert result.body["data"]["task_discovery"]["active"][0]["request_key"] is None
        cancel = DirectorCommand(
            "run.cancel", current.revision, "foreign-cancel", {"run_id": request_id}
        )
        assert (await http(reopened, foreign, cancel)).status == 403
        p = proposal_for(current, "keyframe")
        submit = DirectorCommand(
            "keyframe.submit_proposal",
            current.revision,
            "submit-a",
            {"request_id": request_id, "proposal": p},
        )
        assert (await http(reopened, foreign, submit)).status == 403
        saved = await http(reopened, shared, submit)
        assert saved.status == 200, saved.body
        assert store.read().active_director_run is None
        scene_b_ids = {s.id for s in scene_b.segments}
        assert [
            video.prompt_text
            for video in store.read().video_plan.segments
            if video.segment_id in scene_b_ids
        ] == ["保存视频正文 0", "保存视频正文 1"]

    asyncio.run(complete_after_restart())
    assert len(spawn.calls) == 1


def test_scoped_proposal_detects_changed_cross_scene_reference(tmp_path):
    from test_director_commands import _TwoSceneScriptCompiler

    from core.structured_director import ImageAssetCategory

    async def run():
        store, service, initial, _ = await _visual_project(
            str(tmp_path), script_proposer=_TwoSceneScriptCompiler()
        )
        scene_a, scene_b = initial.director_script.scenes
        binding = next(
            item
            for item in initial.keyframe_plan.segments
            if item.segment_id == scene_b.segments[0].id
        )
        initial = await service.import_image_asset(
            media_type="image/png",
            content=b"\x89PNG\r\n\x1a\nfixture",
            category=ImageAssetCategory.KEYFRAME,
            expected_revision=initial.revision,
        )
        req = request(initial, "keyframe")
        req = replace(req, arguments={**req.arguments, "scope": {"scene_ids": [scene_a.id]}})
        owner = agent(initial, req)
        other = replace(
            owner,
            id="other",
            capabilities=frozenset({"project.read", "project.mutate", "project.execute"}),
        )
        opened = await http(service, owner, req)
        assert opened.status == 200, opened.body
        data = opened.body["data"]
        assert data["mutation_blocked"] is False
        assert data["core_facts"]["referenceable_keyframes"] == []
        command = DirectorCommand(
            "keyframe.apply_operations",
            data["source_revision"],
            "activate-b",
            {
                "operations": [
                    {
                        "op": "set_activation",
                        "segment_id": binding.segment_id,
                        "start_enabled": True,
                        "end_enabled": False,
                    }
                ]
            },
        )
        enabled = await http(service, other, command)
        assert enabled.status == 200, enabled.body

        selection = DirectorCommand(
            "keyframe.select_candidate",
            store.read().revision,
            "select-b",
            {"keyframe_id": binding.start_keyframe_id, "asset_id": initial.assets[-1].id},
        )
        selected = await http(service, other, selection)
        assert selected.status == 200, selected.body
        current = store.read()
        observe = DirectorCommand("project.get_workflow_state", current.revision, "status", {})
        handoff = (await http(service, owner, observe)).body["data"]
        assert handoff["workflow"]["stage_status"] == "workset_stale"
        assert handoff["task_discovery"]["active"][0]["status"] == "workset_stale"
        submit = DirectorCommand(
            "keyframe.submit_proposal",
            current.revision,
            "stale-submit-a",
            {"request_id": data["request_id"], "proposal": proposal_for(current, "keyframe")},
        )
        before = store.path.read_bytes()
        response = await http(service, owner, submit)
        assert response.status == 409, response.body
        error = response.body["error"]
        assert error["conflict_reason"] == "proposal_workset_stale"
        assert error["expected_revision"] == data["source_revision"]
        assert error["current_revision"] == current.revision
        assert store.path.read_bytes() == before

    asyncio.run(run())
