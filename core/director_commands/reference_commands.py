"""引用库编辑命令：Agent 与人类共用的角色卡、造型、造型图、参考音色与旁白身份原子操作。

ID 由 Core 分配；同一 patch 新建的角色卡与造型用局部 `key` 互相引用，回执 `data.created_ids`
返回 key → 正式 ID。删除角色卡、删除造型、选择造型图与参考音色都在这里完成，与人类入口写同一字段。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeAlias

from slatehand_contracts.fields import require_fields

_ID = re.compile(r"[0-9a-f]{32}")
_KEY = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_MAX_OPERATIONS = 64
LOOK_TEXT_FIELDS = frozenset({"name", "appearance", "usage"})
CHARACTER_FIELDS = frozenset(
    {"name", "visual_description", "voice_description", "voice_reference_transcript"}
)
NARRATOR_VOICE_FIELDS = frozenset({"voice_description", "voice_reference_transcript"})
LANGUAGES = frozenset({"zh", "en"})


@dataclass(frozen=True, slots=True)
class LookDraft:
    key: str
    name: str
    appearance: str
    usage: str


@dataclass(frozen=True, slots=True)
class AddCharacter:
    key: str
    name: str
    visual_description: str | None
    voice_description: str
    looks: tuple[LookDraft, ...]


@dataclass(frozen=True, slots=True)
class UpdateCharacter:
    character_id: str
    fields: dict[str, str | None]


@dataclass(frozen=True, slots=True)
class DeleteCharacter:
    character_id: str


@dataclass(frozen=True, slots=True)
class AddLook:
    character_id: str
    key: str
    name: str
    appearance: str
    usage: str


@dataclass(frozen=True, slots=True)
class UpdateLook:
    look_id: str
    fields: dict[str, str]


@dataclass(frozen=True, slots=True)
class DeleteLook:
    look_id: str


@dataclass(frozen=True, slots=True)
class SelectLookImage:
    """generation_id（该造型的成功设定图候选）与 asset_id（任一项目图片）二选一。"""

    look_id: str
    generation_id: str | None
    asset_id: str | None


@dataclass(frozen=True, slots=True)
class ClearLookImage:
    look_id: str


@dataclass(frozen=True, slots=True)
class SelectVoice:
    """为角色（character_id）或旁白某语言（language）选定参考音色。

    generation_id 采用该目标的成功音色候选（描述与文字稿随候选）；asset_id 采用任一项目 WAV，
    此时必须给出完整文字稿。
    """

    character_id: str | None
    language: str | None
    generation_id: str | None
    asset_id: str | None
    transcript: str | None


@dataclass(frozen=True, slots=True)
class ClearVoice:
    character_id: str | None
    language: str | None


@dataclass(frozen=True, slots=True)
class UpdateNarrator:
    name: str | None
    voices: dict[str, dict[str, str | None]]


ReferenceOperation: TypeAlias = (
    AddCharacter
    | UpdateCharacter
    | DeleteCharacter
    | AddLook
    | UpdateLook
    | DeleteLook
    | SelectLookImage
    | ClearLookImage
    | SelectVoice
    | ClearVoice
    | UpdateNarrator
)


@dataclass(frozen=True, slots=True)
class ReferenceOperationsPatch:
    operations: tuple[ReferenceOperation, ...]


def is_reference_key(value: str) -> bool:
    return _KEY.fullmatch(value) is not None and _ID.fullmatch(value) is None


def parse_reference_operations_patch(value: object) -> ReferenceOperationsPatch:
    root = _exact(value, {"operations"}, "reference.apply_operations.arguments")
    raw = root["operations"]
    if not isinstance(raw, list) or not raw or len(raw) > _MAX_OPERATIONS:
        raise ValueError(f"operations 必须是 1–{_MAX_OPERATIONS} 个操作。")
    operations = tuple(_operation(item) for item in raw)
    keys = [
        key
        for item in operations
        for key in (
            (item.key, *(look.key for look in item.looks))
            if isinstance(item, AddCharacter)
            else (item.key,)
            if isinstance(item, AddLook)
            else ()
        )
    ]
    if len(keys) != len(set(keys)):
        raise ValueError("同一 patch 内新建项的 key 不能重复。")
    return ReferenceOperationsPatch(operations)


def _operation(item: object) -> ReferenceOperation:
    if not isinstance(item, dict):
        raise ValueError("operations 每项必须是 object。")
    op = item.get("op")
    if op == "add_character":
        fields = _exact(
            item,
            {"op", "key", "name", "visual_description", "voice_description", "looks"},
            "add_character",
        )
        looks = fields["looks"]
        if not isinstance(looks, list):
            raise ValueError("add_character.looks 必须是数组。")
        return AddCharacter(
            _key(fields["key"]),
            _text(fields["name"], "name"),
            _optional_text(fields["visual_description"], "visual_description"),
            _text(fields["voice_description"], "voice_description", allow_empty=True),
            tuple(_look_draft(look) for look in looks),
        )
    if op == "update_character":
        fields = _exact(item, {"op", "character_id", "fields"}, "update_character")
        changes = fields["fields"]
        if not isinstance(changes, dict) or not changes or not set(changes) <= CHARACTER_FIELDS:
            raise ValueError(
                "update_character.fields 必须是 name / visual_description / voice_description / "
                "voice_reference_transcript 的非空子集。"
            )
        parsed: dict[str, str | None] = {}
        for name, text in changes.items():
            if name == "name":
                parsed[name] = _text(text, name)
            elif name == "voice_description":
                parsed[name] = _text(text, name, allow_empty=True)
            else:
                parsed[name] = _optional_text(text, name)
        return UpdateCharacter(_ref(fields["character_id"], "character_id"), parsed)
    if op == "delete_character":
        fields = _exact(item, {"op", "character_id"}, "delete_character")
        return DeleteCharacter(_id(fields["character_id"], "character_id"))
    if op == "add_look":
        fields = _exact(item, {"op", "character_id", "key", *LOOK_TEXT_FIELDS}, "add_look")
        return AddLook(
            _ref(fields["character_id"], "character_id"),
            _key(fields["key"]),
            *(_text(fields[name], name) for name in ("name", "appearance", "usage")),
        )
    if op == "update_look":
        fields = _exact(item, {"op", "look_id", "fields"}, "update_look")
        changes = fields["fields"]
        if not isinstance(changes, dict) or not changes or not set(changes) <= LOOK_TEXT_FIELDS:
            raise ValueError("update_look.fields 必须是 name/appearance/usage 的非空子集。")
        return UpdateLook(
            _ref(fields["look_id"], "look_id"),
            {name: _text(text, name) for name, text in changes.items()},
        )
    if op == "delete_look":
        fields = _exact(item, {"op", "look_id"}, "delete_look")
        return DeleteLook(_id(fields["look_id"], "look_id"))
    if op == "select_look_image":
        fields = _exact(item, {"op", "look_id", "source", "source_id"}, "select_look_image")
        source = fields["source"]
        if source not in {"generation", "asset"}:
            raise ValueError("select_look_image.source 只能是 generation 或 asset。")
        source_id = _id(fields["source_id"], "source_id")
        return SelectLookImage(
            _ref(fields["look_id"], "look_id"),
            source_id if source == "generation" else None,
            source_id if source == "asset" else None,
        )
    if op == "clear_look_image":
        fields = _exact(item, {"op", "look_id"}, "clear_look_image")
        return ClearLookImage(_id(fields["look_id"], "look_id"))
    if op == "select_voice":
        fields = _exact(
            item,
            {"op", "character_id", "language", "source", "source_id", "transcript"},
            "select_voice",
        )
        character_id, language = _voice_target(fields)
        source = fields["source"]
        if source not in {"generation", "asset"}:
            raise ValueError("select_voice.source 只能是 generation 或 asset。")
        transcript = _optional_text(fields["transcript"], "transcript")
        if (source == "asset") != (transcript is not None):
            raise ValueError(
                "source=asset 必须给出完整文字稿 transcript；generation 沿用候选文字稿。"
            )
        source_id = _id(fields["source_id"], "source_id")
        return SelectVoice(
            character_id,
            language,
            source_id if source == "generation" else None,
            source_id if source == "asset" else None,
            transcript,
        )
    if op == "clear_voice":
        fields = _exact(item, {"op", "character_id", "language"}, "clear_voice")
        return ClearVoice(*_voice_target(fields))
    if op == "update_narrator":
        fields = _exact(item, {"op", "fields"}, "update_narrator")
        changes = fields["fields"]
        if not isinstance(changes, dict) or not changes or not set(changes) <= {"name", *LANGUAGES}:
            raise ValueError("update_narrator.fields 必须是 name / zh / en 的非空子集。")
        voices: dict[str, dict[str, str | None]] = {}
        for language in LANGUAGES & set(changes):
            voice = changes[language]
            if not isinstance(voice, dict) or not voice or not set(voice) <= NARRATOR_VOICE_FIELDS:
                raise ValueError(
                    f"update_narrator.fields.{language} 必须是 voice_description / "
                    "voice_reference_transcript 的非空子集。"
                )
            voices[language] = {
                name: _text(text, name)
                if name == "voice_description"
                else _optional_text(text, name)
                for name, text in voice.items()
            }
        return UpdateNarrator(_text(changes["name"], "name") if "name" in changes else None, voices)
    raise ValueError(
        "op 只能是 add_character / update_character / delete_character / add_look / update_look / "
        "delete_look / select_look_image / clear_look_image / select_voice / clear_voice / "
        "update_narrator。"
    )


def _look_draft(value: object) -> LookDraft:
    fields = _exact(value, {"key", *LOOK_TEXT_FIELDS}, "add_character.looks[]")
    return LookDraft(
        _key(fields["key"]),
        *(_text(fields[name], name) for name in ("name", "appearance", "usage")),
    )


def _voice_target(fields: dict) -> tuple[str | None, str | None]:
    character_id, language = fields["character_id"], fields["language"]
    if (character_id is None) == (language is None):
        raise ValueError("角色音色只给 character_id（language 为 null），旁白音色只给 language。")
    if language is not None and language not in LANGUAGES:
        raise ValueError("language 只能是 zh 或 en。")
    return (None if character_id is None else _ref(character_id, "character_id")), language


def _exact(value: object, fields: set[str], label: str) -> dict:
    require_fields(label, value, fields)
    return value


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{label} 不合法。")
    return value


def _key(value: object) -> str:
    if not isinstance(value, str) or not is_reference_key(value):
        raise ValueError("key 必须是小写字母开头的 [a-z0-9_-]，且不能是 32 位 ID。")
    return value


def _ref(value: object, label: str) -> str:
    if isinstance(value, str) and (_ID.fullmatch(value) or is_reference_key(value)):
        return value
    raise ValueError(f"{label} 必须是正式 ID 或同一 patch 内的新建 key。")


def _text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{label} 必须是非空字符串。")
    return value.strip()


def _optional_text(value: object, label: str) -> str | None:
    return None if value is None else _text(value, label)
