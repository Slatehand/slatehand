"""引用库编辑：角色卡、造型、造型图、参考音色与旁白身份；纯函数，Core 分配 ID。

Agent 与人类经同一组操作修改已建立的引用库。所有操作按顺序作用在同一份引用库上，最后经
`_apply_reference_library_change` 一次性落到文档：Keyframe 对已删除角色/造型的选择被剪除，
对白音色输入变化的 Segment 清除对白选择与视频选择。改造型文字不失效已选造型图；
Keyframe 引用造型而不是图片，改选图不需要改写 Keyframe。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import uuid4

from core.director_commands import CommandReceipt
from core.director_commands.reference_commands import (
    AddCharacter,
    AddLook,
    ClearLookImage,
    ClearVoice,
    DeleteCharacter,
    DeleteLook,
    ReferenceOperationsPatch,
    SelectLookImage,
    SelectVoice,
    UpdateCharacter,
    UpdateLook,
    UpdateNarrator,
    is_reference_key,
)

from ..models import (
    CharacterCard,
    CharacterLook,
    NarrationLanguage,
    NarratorVoice,
    ReferenceGeneration,
    ReferenceGenerationKind,
    ReferenceGenerationPhase,
    ReferenceLibrary,
    StructuredDirectorDocument,
)
from ..video_mutations import _apply_reference_library_change


@dataclass(frozen=True, slots=True)
class ReferenceOperationsResult:
    document: StructuredDirectorDocument
    receipt: CommandReceipt
    created_ids: dict[str, str]
    write: bool


def apply_reference_operations(
    document: StructuredDirectorDocument, patch: ReferenceOperationsPatch, command_id: str
) -> ReferenceOperationsResult:
    library = document.reference_library
    created: dict[str, str] = {}
    changed: list[str] = []

    def resolve(value: str, label: str) -> str:
        if is_reference_key(value):
            if value not in created:
                raise ValueError(f"{label} 引用了本 patch 中不存在的新建 key：{value}。")
            return created[value]
        return value

    for operation in patch.operations:
        before = library
        if isinstance(operation, AddCharacter):
            looks = []
            for draft in operation.looks:
                created[draft.key] = uuid4().hex
                looks.append(
                    CharacterLook(created[draft.key], draft.name, draft.appearance, draft.usage)
                )
            created[operation.key] = uuid4().hex
            card = CharacterCard(
                id=created[operation.key],
                name=operation.name,
                visual_description=operation.visual_description,
                voice_description=operation.voice_description,
                looks=tuple(looks),
            )
            library = replace(library, characters=(*library.characters, card))
            changed.extend((card.id, *(look.id for look in card.looks)))
            continue
        if isinstance(operation, UpdateCharacter):
            card = _card(library, resolve(operation.character_id, "character_id"))
            library = _replace_card(library, replace(card, **operation.fields))
            target = card.id
        elif isinstance(operation, DeleteCharacter):
            card = _card(library, operation.character_id)
            reject_script_usage(document, card)
            library = replace(
                library, characters=tuple(c for c in library.characters if c.id != card.id)
            )
            target = card.id
        elif isinstance(operation, AddLook):
            card = _card(library, resolve(operation.character_id, "character_id"))
            if card.visual_description is None:
                raise ValueError(f"「{card.name}」是非出镜声音身份，不能有造型。")
            created[operation.key] = uuid4().hex
            look = CharacterLook(
                created[operation.key], operation.name, operation.appearance, operation.usage
            )
            library = _replace_card(library, replace(card, looks=(*card.looks, look)))
            target = look.id
        elif isinstance(operation, UpdateLook):
            card, look = _look(library, resolve(operation.look_id, "look_id"))
            library = _replace_look(library, card, replace(look, **operation.fields))
            target = look.id
        elif isinstance(operation, DeleteLook):
            card, look = _look(library, operation.look_id)
            library = _replace_card(
                library, replace(card, looks=tuple(i for i in card.looks if i.id != look.id))
            )
            target = look.id
        elif isinstance(operation, SelectLookImage):
            card, look = _look(library, resolve(operation.look_id, "look_id"))
            asset_id = (
                _look_sheet_output(document, look.id, operation.generation_id)
                if operation.generation_id is not None
                else _image_asset(document, operation.asset_id)
            )
            library = _replace_look(library, card, replace(look, selected_asset_id=asset_id))
            target = look.id
        elif isinstance(operation, ClearLookImage):
            card, look = _look(library, operation.look_id)
            library = _replace_look(library, card, replace(look, selected_asset_id=None))
            target = look.id
        elif isinstance(operation, SelectVoice):
            library, target = _select_voice(document, library, operation, resolve)
        elif isinstance(operation, ClearVoice):
            library, target = _clear_voice(library, operation, resolve)
        elif isinstance(operation, UpdateNarrator):
            narrator = _narrator(library)
            for language, fields in operation.voices.items():
                lang = NarrationLanguage(language)
                narrator = narrator.with_voice(lang, replace(narrator.voice(lang), **fields))
            if operation.name is not None:
                narrator = replace(narrator, name=operation.name)
            library = replace(library, narrator=narrator)
            target = narrator.id
        else:  # pragma: no cover - parser 保证穷尽
            raise ValueError("未知的引用库操作。")
        if library != before and target not in changed:
            changed.append(target)

    if library == document.reference_library:
        return ReferenceOperationsResult(
            document,
            CommandReceipt(
                command_id=command_id,
                status="noop",
                old_revision=document.revision,
                new_revision=document.revision,
                decision_summary="引用库内容未变化。",
            ),
            created,
            write=False,
        )
    updated = replace(
        _apply_reference_library_change(document, library), revision=document.revision + 1
    )
    invalidated = _invalidated(document, updated)
    return ReferenceOperationsResult(
        updated,
        CommandReceipt(
            command_id=command_id,
            status="succeeded",
            old_revision=document.revision,
            new_revision=updated.revision,
            changed_ids=tuple(changed),
            invalidated=invalidated,
            preserved=("asset_history", "generation_history"),
            decision_summary=(
                "已保存引用库修改；对白音色变化已清除受影响对白与视频选择。"
                if invalidated
                else "已保存引用库修改。"
            ),
            next_allowed_actions=("reference.generate_look_sheet", "reference.design_voice"),
        ),
        created,
        write=True,
    )


def _card(library: ReferenceLibrary, character_id: str) -> CharacterCard:
    card = next((item for item in library.characters if item.id == character_id), None)
    if card is None:
        raise ValueError("角色卡不存在。")
    return card


def _look(library: ReferenceLibrary, look_id: str) -> tuple[CharacterCard, CharacterLook]:
    owner = library.look_owner(look_id)
    if owner is None:
        raise ValueError("造型不存在。")
    return owner


def _narrator(library: ReferenceLibrary):
    if library.narrator is None:
        raise ValueError("项目没有旁白身份。")
    return library.narrator


def _replace_card(library: ReferenceLibrary, card: CharacterCard) -> ReferenceLibrary:
    return replace(
        library,
        characters=tuple(card if item.id == card.id else item for item in library.characters),
    )


def _replace_look(
    library: ReferenceLibrary, card: CharacterCard, look: CharacterLook
) -> ReferenceLibrary:
    return _replace_card(
        library,
        replace(card, looks=tuple(look if item.id == look.id else item for item in card.looks)),
    )


def reject_script_usage(document: StructuredDirectorDocument, card: CharacterCard) -> None:
    """脚本仍使用的角色不能删除：Shot 可见角色与对白说话者都是脚本权威，不静默改写。"""
    script = document.director_script
    places: list[str] = []
    for scene_number, scene in enumerate(() if script is None else script.scenes, start=1):
        for shot_number, shot in enumerate(scene.shots, start=1):
            if card.id in shot.visible_character_ids:
                places.append(f"场景 {scene_number}－镜头 {shot_number}（可见角色）")
        for segment_number, segment in enumerate(scene.segments, start=1):
            if any(line.character_id == card.id for line in segment.dialogue_lines):
                places.append(f"场景 {scene_number}－片段 {segment_number}（对白）")
    if places:
        raise ValueError(
            f"「{card.name}」仍被脚本使用：{'、'.join(places)}；先用脚本编辑移除或改为其他角色后再删除。"
        )


def _generation(document: StructuredDirectorDocument, generation_id: str) -> ReferenceGeneration:
    generation = next((item for item in document.generations if item.id == generation_id), None)
    if generation is None:
        raise ValueError("Generation 不存在。")
    if generation.dismissed:
        raise ValueError("已隐藏的候选不能被选定。")
    if (
        generation.phase is not ReferenceGenerationPhase.SUCCEEDED
        or generation.output_asset_id is None
    ):
        raise ValueError("只有成功且有产物的 Generation 可以被选定。")
    return generation


def _look_sheet_output(
    document: StructuredDirectorDocument, look_id: str, generation_id: str
) -> str:
    generation = _generation(document, generation_id)
    if (
        generation.kind is not ReferenceGenerationKind.CHARACTER_LOOK_SHEET
        or generation.target_id != look_id
    ):
        raise ValueError("该 Generation 不是这套造型的设定图候选。")
    return generation.output_asset_id


def _image_asset(document: StructuredDirectorDocument, asset_id: str) -> str:
    asset = next((item for item in document.assets if item.id == asset_id), None)
    if asset is None or not asset.media_type.startswith("image/"):
        raise ValueError("造型图必须引用已登记的图片 Asset。")
    return asset_id


def _wav_asset(document: StructuredDirectorDocument, asset_id: str) -> str:
    asset = next((item for item in document.assets if item.id == asset_id), None)
    if asset is None or asset.media_type != "audio/wav":
        raise ValueError("参考音色必须引用已登记的 WAV Asset。")
    return asset_id


def _select_voice(document, library, operation: SelectVoice, resolve):
    if operation.generation_id is not None:
        generation = _generation(document, operation.generation_id)
        asset_id = generation.output_asset_id
        transcript = generation.sample_text
        description = generation.prompt_text
    else:
        generation = None
        asset_id = _wav_asset(document, operation.asset_id)
        transcript = operation.transcript
        description = None
    if operation.character_id is not None:
        card = _card(library, resolve(operation.character_id, "character_id"))
        if generation is not None and (
            generation.kind is not ReferenceGenerationKind.CHARACTER_VOICE
            or generation.target_id != card.id
        ):
            raise ValueError("该 Generation 不是这个角色的参考音色候选。")
        return (
            _replace_card(
                library,
                replace(
                    card,
                    voice_description=card.voice_description
                    if description is None
                    else description,
                    voice_reference_asset_id=asset_id,
                    voice_reference_transcript=transcript,
                ),
            ),
            card.id,
        )
    narrator = _narrator(library)
    language = NarrationLanguage(operation.language)
    current = narrator.voice(language)
    if generation is not None and (
        generation.kind is not ReferenceGenerationKind.NARRATOR_VOICE
        or generation.target_id != narrator.id
        or generation.language is not language
    ):
        raise ValueError("该 Generation 不是这一语言的旁白参考音色候选。")
    voice = NarratorVoice(
        voice_description=current.voice_description if description is None else description,
        voice_reference_asset_id=asset_id,
        voice_reference_transcript=transcript,
    )
    return replace(library, narrator=narrator.with_voice(language, voice)), narrator.id


def _clear_voice(library, operation: ClearVoice, resolve):
    if operation.character_id is not None:
        card = _card(library, resolve(operation.character_id, "character_id"))
        return (
            _replace_card(
                library,
                replace(card, voice_reference_asset_id=None, voice_reference_transcript=None),
            ),
            card.id,
        )
    narrator = _narrator(library)
    language = NarrationLanguage(operation.language)
    voice = replace(
        narrator.voice(language), voice_reference_asset_id=None, voice_reference_transcript=None
    )
    return replace(library, narrator=narrator.with_voice(language, voice)), narrator.id


def _invalidated(
    before: StructuredDirectorDocument, after: StructuredDirectorDocument
) -> tuple[str, ...]:
    """引用库变化级联清除的对白音频选择与 Segment 视频选择。"""
    result: list[str] = []
    if before.dialogue_plan is not None and after.dialogue_plan is not None:
        kept = {
            item.dialogue_line_id
            for item in after.dialogue_plan.items
            if item.selected_audio_asset_id is not None
        }
        result.extend(
            item.dialogue_line_id
            for item in before.dialogue_plan.items
            if item.selected_audio_asset_id is not None and item.dialogue_line_id not in kept
        )
    if before.video_plan is not None and after.video_plan is not None:
        kept = {
            item.segment_id
            for item in after.video_plan.segments
            if item.selected_asset_id is not None
        }
        result.extend(
            item.segment_id
            for item in before.video_plan.segments
            if item.selected_asset_id is not None and item.segment_id not in kept
        )
    return tuple(result)


def set_look_asset(
    document: StructuredDirectorDocument, *, card_id: str, look_id: str, asset_id: str
) -> StructuredDirectorDocument:
    """把已登记图片设为造型图（不加 revision，由调用方统一 bump）。"""
    asset = next((item for item in document.assets if item.id == asset_id), None)
    if asset is None or not asset.media_type.startswith("image/"):
        raise ValueError("造型图必须引用已登记的图片 Asset。")
    card = next(
        (item for item in document.reference_library.characters if item.id == card_id), None
    )
    look = None if card is None else card.look(look_id)
    if card is None or look is None:
        raise ValueError("造型不存在。")
    if look.selected_asset_id == asset_id:
        raise ValueError("该图片已经是这套造型的造型图。")
    updated = replace(
        card,
        looks=tuple(
            replace(item, selected_asset_id=asset_id) if item.id == look_id else item
            for item in card.looks
        ),
    )
    return replace(
        document,
        reference_library=replace(
            document.reference_library,
            characters=tuple(
                updated if item.id == card_id else item
                for item in document.reference_library.characters
            ),
        ),
    )
