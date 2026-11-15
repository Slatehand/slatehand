"""引用库编辑命令：Agent 与人类经 reference.apply_operations 全量修改已建立的引用库。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from conftest import make_service
from structured_director_fixtures import RecordingProvider, wav_bytes
from test_canvas_image_prompts import canvas_image_service as _qwen_service
from test_character_looks import _sheets, _succeed
from test_external_script_proposals import agent, http
from test_keyframe_character_references import _apply, _project_with_costumes

from core.director_commands import DirectorCommand, DirectorCommandRegistry
from core.structured_director import StructuredDirectorStore
from core.structured_director.http_api import StructuredDirectorHttpApi
from core.structured_director.models import (
    CharacterCard,
    NarratorProfile,
    NarratorVoice,
    ReferenceGenerationKind,
)
from core.structured_director.reads.workflow_state import references_reviewed, workflow_state
from integrations.slatehand_mcp.production_tools import Generate, ReferenceEdit, translate

A = "a" * 32


def _ops(store, operations, key="ref"):
    document = store.read()
    return DirectorCommandRegistry(store).execute(
        DirectorCommand(
            "reference.apply_operations",
            document.revision,
            f"{key}-{document.revision}",
            {"operations": operations},
        )
    )


def _costumes(tmp_path):
    store, service, document, keyframe_id = asyncio.run(_project_with_costumes(tmp_path))
    return store, service, document, keyframe_id


def _image(store, service, label: bytes) -> str:
    from core.structured_director.models import ImageAssetCategory

    document = asyncio.run(
        service.import_image_asset(
            media_type="image/png",
            content=b"\x89PNG\r\n\x1a\n" + label,
            category=ImageAssetCategory.CHARACTER_REFERENCE,
            expected_revision=store.read().revision,
        )
    )
    return document.assets[-1].id


def _wav(store, service, seconds=1.0) -> str:
    document = asyncio.run(
        service.import_media_asset(
            media_type="audio/wav",
            content=wav_bytes(duration_seconds=seconds),
            expected_revision=store.read().revision,
        )
    )
    return document.assets[-1].id


def test_add_character_with_looks_and_select_image_in_one_patch(tmp_path) -> None:
    store, service, _document, _keyframe_id = _costumes(tmp_path)
    image = _image(store, service, b"new-character")
    execution = _ops(
        store,
        [
            {
                "op": "add_character",
                "key": "ling",
                "name": "林",
                "visual_description": "二十岁女性，短黑发，瘦高",
                "voice_description": "清亮、语速快",
                "looks": [
                    {
                        "key": "ling-school",
                        "name": "校服",
                        "appearance": "蓝白校服",
                        "usage": "第一幕",
                    }
                ],
            },
            {
                "op": "add_look",
                "character_id": "ling",
                "key": "ling-rain",
                "name": "雨衣",
                "appearance": "黄色雨衣",
                "usage": "暴雨夜",
            },
            {
                "op": "select_look_image",
                "look_id": "ling-school",
                "source": "asset",
                "source_id": image,
            },
        ],
    )
    created = execution.data["created_ids"]
    assert set(created) == {"ling", "ling-school", "ling-rain"}
    card = next(c for c in store.read().reference_library.characters if c.id == created["ling"])
    assert [look.name for look in card.looks] == ["校服", "雨衣"]
    assert card.look(created["ling-school"]).selected_asset_id == image
    assert execution.receipt.changed_ids[0] == created["ling"]


def test_unknown_key_and_voice_only_looks_rejected_atomically(tmp_path) -> None:
    store, _service, document, _keyframe_id = _costumes(tmp_path)
    with pytest.raises(ValueError, match="不存在的新建 key"):
        _ops(
            store,
            [
                {
                    "op": "add_look",
                    "character_id": "ghost",
                    "key": "x",
                    "name": "x",
                    "appearance": "y",
                    "usage": "z",
                }
            ],
        )
    with pytest.raises(ValueError, match="非出镜声音身份"):
        _ops(
            store,
            [
                {
                    "op": "add_character",
                    "key": "radio",
                    "name": "广播",
                    "visual_description": None,
                    "voice_description": "失真的男声",
                    "looks": [{"key": "r", "name": "x", "appearance": "y", "usage": "z"}],
                }
            ],
        )
    assert store.read() == document


def test_update_character_fields_and_noop(tmp_path) -> None:
    store, _service, document, _keyframe_id = _costumes(tmp_path)
    card = document.reference_library.characters[0]
    updated = _ops(
        store,
        [
            {
                "op": "update_character",
                "character_id": card.id,
                "fields": {"name": "甲（成年）", "visual_description": "三十岁，短发，眼角有疤"},
            }
        ],
    )
    saved = updated.document.reference_library.characters[0]
    assert (saved.name, saved.visual_description) == ("甲（成年）", "三十岁，短发，眼角有疤")
    # 造型与造型图不受卡片文字修改影响。
    assert saved.looks == card.looks
    noop = _ops(
        store,
        [{"op": "update_character", "character_id": card.id, "fields": {"name": "甲（成年）"}}],
    )
    assert noop.receipt.status == "noop" and noop.document.revision == updated.document.revision


def test_voice_input_change_reports_cleared_dialogue_and_video_selection(tmp_path) -> None:
    from test_prompt_worksets import fixture

    store, _service, document, _ = asyncio.run(fixture(tmp_path))
    segment = document.director_script.scenes[0].segments[0]
    speaker = segment.dialogue_lines[0].character_id
    card = next(c for c in document.reference_library.characters if c.id == speaker)
    assert card.voice_reference_asset_id is not None
    affected = {
        line.id
        for scene in document.director_script.scenes
        for item in scene.segments
        if any(line.character_id == speaker for line in item.dialogue_lines)
        for line in item.dialogue_lines
    }
    selected_before = {
        item.dialogue_line_id
        for item in document.dialogue_plan.items
        if item.selected_audio_asset_id is not None
    }
    assert affected & selected_before
    # 只改描述不影响克隆输入：不清除任何选择。
    described = _ops(
        store,
        [
            {
                "op": "update_character",
                "character_id": card.id,
                "fields": {"voice_description": "沙哑"},
            }
        ],
    )
    assert described.receipt.invalidated == ()
    cleared = _ops(store, [{"op": "clear_voice", "character_id": card.id, "language": None}])
    assert set(cleared.receipt.invalidated) >= affected & selected_before
    items = {item.dialogue_line_id: item for item in cleared.document.dialogue_plan.items}
    assert all(items[line_id].selected_audio_asset_id is None for line_id in affected)


def test_delete_look_prunes_keyframe_choice(tmp_path) -> None:
    store, _service, document, keyframe_id = _costumes(tmp_path)
    card = document.reference_library.characters[0]
    look = card.looks[1]
    _apply(
        store,
        document,
        [
            {
                "op": "update_frame_fields",
                "keyframe_id": keyframe_id,
                "fields": {"character_references": [{"character_id": card.id, "look_id": look.id}]},
            }
        ],
        key="pick",
    )
    after = _ops(store, [{"op": "delete_look", "look_id": look.id}]).document
    assert after.reference_library.look_owner(look.id) is None
    frame = next(item for item in after.keyframe_plan.keyframes if item.id == keyframe_id)
    assert all(ref.look_id != look.id for ref in frame.character_references)


def test_delete_character_used_by_script_is_rejected_with_places(tmp_path) -> None:
    store, service, document, _keyframe_id = _costumes(tmp_path)
    card = document.reference_library.characters[0]
    with pytest.raises(
        ValueError, match=r"「甲」仍被脚本使用：场景 1－镜头 1（可见角色）.*片段 1（对白）"
    ):
        _ops(store, [{"op": "delete_character", "character_id": card.id}])
    api = StructuredDirectorHttpApi(service, resource_path="/structured-director")
    response = asyncio.run(
        api.handle(
            "DELETE",
            f"/structured-director/characters/{card.id}",
            json.dumps({"expected_revision": store.read().revision}).encode(),
        )
    )
    assert response.status != 200
    assert "仍被脚本使用" in json.dumps(response.body, ensure_ascii=False)
    assert store.read() == document


def test_delete_unused_character_keeps_voice_candidates_as_history(tmp_path) -> None:
    store, _service, _document, _keyframe_id = _costumes(tmp_path)
    added = _ops(
        store,
        [
            {
                "op": "add_character",
                "key": "extra",
                "name": "路人",
                "visual_description": "穿灰色风衣的中年人",
                "voice_description": "低沉",
                "looks": [],
            }
        ],
    )
    character_id = added.data["created_ids"]["extra"]
    voice = make_service(
        store, voice_provider=RecordingProvider(media_type="audio/wav", content=wav_bytes())
    )
    submitted = asyncio.run(
        voice._media.design_voice(
            card_id=character_id,
            voice_description="低沉",
            sample_text="你好。",
            seed=42,
            expected_revision=store.read().revision,
        )
    )
    assert submitted.generations[-1].target_id == character_id
    after = _ops(store, [{"op": "delete_character", "character_id": character_id}]).document
    assert all(card.id != character_id for card in after.reference_library.characters)
    assert any(item.target_id == character_id for item in after.generations)


def test_select_and_clear_look_image_from_sheet_candidate(tmp_path) -> None:
    store, _service, _document, _keyframe_id = _costumes(tmp_path)
    service, _provider, _registry = _qwen_service(store)
    card = store.read().reference_library.characters[0]
    look = card.looks[0]
    asyncio.run(
        service.generate_look_sheet(
            look_id=look.id, identity_asset_ids=(), expected_revision=store.read().revision
        )
    )
    sheet = _sheets(store.read())[-1]
    asset_id = _succeed(store, service, sheet, b"agent-sheet")
    other_look = card.looks[1]
    with pytest.raises(ValueError, match="不是这套造型的设定图候选"):
        _ops(
            store,
            [
                {
                    "op": "select_look_image",
                    "look_id": other_look.id,
                    "source": "generation",
                    "source_id": sheet.id,
                }
            ],
        )
    selected = _ops(
        store,
        [
            {
                "op": "select_look_image",
                "look_id": look.id,
                "source": "generation",
                "source_id": sheet.id,
            }
        ],
    )
    assert selected.document.reference_library.look_owner(look.id)[1].selected_asset_id == asset_id
    again = _ops(
        store,
        [
            {
                "op": "select_look_image",
                "look_id": look.id,
                "source": "generation",
                "source_id": sheet.id,
            }
        ],
    )
    assert again.receipt.status == "noop"
    cleared = _ops(store, [{"op": "clear_look_image", "look_id": look.id}]).document
    assert cleared.reference_library.look_owner(look.id)[1].selected_asset_id is None


def _with_narrator(store):
    document = store.read()
    narrator = NarratorProfile(
        "9" * 32, "旁白", NarratorVoice("温和女声"), NarratorVoice("Warm voice")
    )
    store._atomic_write(
        replace(
            document,
            revision=document.revision + 1,
            reference_library=replace(document.reference_library, narrator=narrator),
        )
    )
    return narrator


def test_character_and_narrator_voice_selection(tmp_path) -> None:
    store, service, _document, _keyframe_id = _costumes(tmp_path)
    narrator = _with_narrator(store)
    card = store.read().reference_library.characters[0]
    wav = _wav(store, service)
    with pytest.raises(ValueError, match="完整文字稿"):
        _ops(
            store,
            [
                {
                    "op": "select_voice",
                    "character_id": card.id,
                    "language": None,
                    "source": "asset",
                    "source_id": wav,
                    "transcript": None,
                }
            ],
        )
    selected = _ops(
        store,
        [
            {
                "op": "select_voice",
                "character_id": card.id,
                "language": None,
                "source": "asset",
                "source_id": wav,
                "transcript": "你好，我是甲。",
            },
            {
                "op": "select_voice",
                "character_id": None,
                "language": "en",
                "source": "asset",
                "source_id": wav,
                "transcript": "Hello there.",
            },
            {
                "op": "update_narrator",
                "fields": {"name": "说书人", "zh": {"voice_description": "沉稳男声"}},
            },
        ],
    ).document
    saved = selected.reference_library.characters[0]
    assert (saved.voice_reference_asset_id, saved.voice_reference_transcript) == (
        wav,
        "你好，我是甲。",
    )
    profile = selected.reference_library.narrator
    assert profile.id == narrator.id and profile.name == "说书人"
    assert profile.zh == NarratorVoice("沉稳男声")
    assert profile.en == NarratorVoice("Warm voice", wav, "Hello there.")
    cleared = _ops(
        store,
        [
            {"op": "clear_voice", "character_id": card.id, "language": None},
            {"op": "clear_voice", "character_id": None, "language": "en"},
        ],
    ).document
    assert cleared.reference_library.characters[0].voice_reference_asset_id is None
    assert cleared.reference_library.narrator.en == NarratorVoice("Warm voice")


def test_reference_edits_after_script_do_not_pull_workflow_back_to_review(tmp_path) -> None:
    store, _service, document, _keyframe_id = _costumes(tmp_path)
    before = workflow_state(document)["current_stage"]
    card = document.reference_library.characters[0]
    after = _ops(
        store,
        [{"op": "update_look", "look_id": card.looks[0].id, "fields": {"usage": "全片"}}],
    ).document
    assert not references_reviewed(after)
    assert workflow_state(after)["current_stage"] == before != "reference_library"


def test_agent_edits_library_and_designs_voice_through_application_commands(tmp_path) -> None:
    async def run():
        store, _service, document, _keyframe_id = await _project_with_costumes(tmp_path)
        card = document.reference_library.characters[1]
        provider = RecordingProvider(media_type="audio/wav", content=wav_bytes())
        agent_service = make_service(
            StructuredDirectorStore(store.path.parent, document.project_id),
            voice_provider=provider,
        )

        async def call(name, key, arguments):
            command = DirectorCommand(name, store.read().revision, key, arguments)
            return await http(agent_service, agent(store.read(), command), command)

        edited = await call(
            "reference.apply_operations",
            "rename",
            {
                "operations": [
                    {"op": "update_character", "character_id": card.id, "fields": {"name": "乙'"}}
                ]
            },
        )
        assert edited.status == 200, edited.body
        designed = await call(
            "reference.design_voice",
            "voice",
            {
                "target_id": card.id,
                "language": None,
                "voice_description": "温柔、略带鼻音",
                "sample_text": "今天的雨好大。",
            },
        )
        assert designed.status == 200, designed.body
        generation = store.read().generations[-1]
        assert (generation.kind, generation.target_id, generation.prompt_text) == (
            ReferenceGenerationKind.CHARACTER_VOICE,
            card.id,
            "温柔、略带鼻音",
        )
        await agent_service._generation_observer.refresh_generation(generation.id)
        await agent_service._generation_observer.refresh_generation(generation.id)
        attached = await call(
            "reference.apply_operations",
            "attach",
            {
                "operations": [
                    {
                        "op": "select_voice",
                        "character_id": card.id,
                        "language": None,
                        "source": "generation",
                        "source_id": generation.id,
                        "transcript": None,
                    }
                ]
            },
        )
        assert attached.status == 200, attached.body
        saved = next(c for c in store.read().reference_library.characters if c.id == card.id)
        assert saved.voice_description == "温柔、略带鼻音"
        assert saved.voice_reference_transcript == "今天的雨好大。"
        missing = await call(
            "reference.design_voice",
            "missing",
            {"target_id": A, "language": None, "voice_description": "x", "sample_text": "y"},
        )
        assert missing.status != 200 and len(provider.requests) == 1

    asyncio.run(run())


def test_mcp_reference_tools_translate_to_core_commands() -> None:
    edit = ReferenceEdit(
        operations=[
            {"op": "delete_look", "look_id": A},
            {
                "op": "select_voice",
                "character_id": None,
                "language": "zh",
                "source": "generation",
                "source_id": A,
                "transcript": None,
            },
        ],
        project_id=A,
        expected_revision=3,
        idempotency_key="k",
    )
    name, arguments = translate("edit_references", edit)
    assert name == "reference.apply_operations" and len(arguments["operations"]) == 2
    voice = Generate(
        kind="voice",
        target_id=A,
        language="en",
        voice_description="calm",
        sample_text="Hello.",
        project_id=A,
        expected_revision=3,
        idempotency_key="k",
    )
    assert translate("generate_media", voice) == (
        "reference.design_voice",
        {"target_id": A, "language": "en", "voice_description": "calm", "sample_text": "Hello."},
    )
    sheet = Generate(
        kind="look_sheet",
        target_id=A,
        identity_asset_ids=[],
        prompt_text="完整提示词",
        project_id=A,
        expected_revision=3,
        idempotency_key="k",
    )
    assert translate("generate_media", sheet)[1]["prompt_text"] == "完整提示词"
    for bad in (
        {"kind": "voice", "voice_description": "x"},
        {"kind": "keyframe", "prompt_text": "x"},
        {"kind": "keyframe", "language": "zh"},
    ):
        with pytest.raises(ValueError):
            Generate(**bad, target_id=A, project_id=A, expected_revision=3, idempotency_key="k")


def test_voice_only_card_is_reference_library_member(tmp_path) -> None:
    store, _service, _document, _keyframe_id = _costumes(tmp_path)
    added = _ops(
        store,
        [
            {
                "op": "add_character",
                "key": "radio",
                "name": "广播",
                "visual_description": None,
                "voice_description": "失真的男声",
                "looks": [],
            }
        ],
    )
    card_id = added.data["created_ids"]["radio"]
    card = next(c for c in added.document.reference_library.characters if c.id == card_id)
    assert card == CharacterCard(card_id, "广播", None, (), "失真的男声")
