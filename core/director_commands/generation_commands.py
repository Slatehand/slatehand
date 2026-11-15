from __future__ import annotations

import re
from dataclasses import dataclass

from slatehand_contracts.fields import require_fields

_ID = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class GenerationTarget:
    generation_id: str


@dataclass(frozen=True, slots=True)
class GenerationWait:
    generation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GenerationDismissal:
    generation_ids: tuple[str, ...]


DISMISS_MAX_TARGETS = 20


def parse_generation_target(value: object, command_name: str) -> GenerationTarget:
    item = _exact(value, {"generation_id"}, command_name)
    return GenerationTarget(_id(item["generation_id"], "generation_id"))


def parse_generation_wait(value: object) -> GenerationWait:
    return GenerationWait(_generation_ids(value, "generation.wait", 100))


def parse_generation_dismissal(value: object) -> GenerationDismissal:
    return GenerationDismissal(_generation_ids(value, "generation.dismiss", DISMISS_MAX_TARGETS))


def _generation_ids(value: object, label: str, maximum: int) -> tuple[str, ...]:
    item = _exact(value, {"generation_ids"}, label)
    raw_ids = item["generation_ids"]
    if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= maximum:
        raise ValueError(f"{label}.generation_ids 必须包含 1 到 {maximum} 个 ID。")
    result = tuple(_id(value, "generation_ids") for value in raw_ids)
    if len(result) != len(set(result)):
        raise ValueError(f"{label}.generation_ids 不能重复。")
    return result


def _exact(value: object, keys: set[str], label: str) -> dict[str, object]:
    require_fields(f"{label}.arguments", value, keys)
    return value


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"{label} 必须是 32 位小写十六进制 ID。")
    return value
