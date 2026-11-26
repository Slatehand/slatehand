"""通用 recipe 线上形状、媒体校验与 Web 声明保持一致。"""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from sample_plugins import VOICE as MANIFEST

from core.model_providers.manifest import Field, Widget, recipe_from_json
from core.model_providers.recipe import (
    MediaInput,
    build_recipe_request,
    parse_recipe_wire,
    recipe_wire_schema,
)


def test_speech_web_fixture_matches_manifest():
    path = Path(__file__).parents[1] / "web/src/test/speech_recipes.json"
    assert json.loads(path.read_text()) == [recipe.to_json() for recipe in MANIFEST.recipes]


def test_clone_wire_and_request_require_voice_transcript_and_wav():
    recipe = MANIFEST.recipe("sample_voice.clone")
    values = {
        "text": "你好",
        "voice_asset_id": "a" * 32,
        "reference_transcript": "完整文字稿",
        "performance_instruction": None,
        "seed": 42,
    }
    bindings, params = parse_recipe_wire(recipe, values)
    assert bindings["voice"][0].asset_id == "a" * 32
    request = build_recipe_request(
        MANIFEST, recipe.id, inputs={"voice": [MediaInput("audio/wav", b"wav")]}, params=params
    )
    assert request.param("performance_instruction") is None
    for invalid in ("", "x" * 20_001):
        with pytest.raises(ValueError):
            parse_recipe_wire(recipe, {**values, "reference_transcript": invalid})
    with pytest.raises(ValueError, match="audio/wav"):
        build_recipe_request(
            MANIFEST, recipe.id, inputs={"voice": [MediaInput("audio/mp3", b"mp3")]}, params=params
        )
    with pytest.raises(ValueError):
        parse_recipe_wire(recipe, {**values, "extra": True})


def test_optional_recipe_param_defaults_without_weakening_other_fields():
    base = MANIFEST.recipe("sample_voice.clone")
    extra = Field("enhance", Widget.TOGGLE, "专项能力", default=False, default_when_omitted=True)
    recipe = replace(base, params=(*base.params, extra))
    manifest = replace(
        MANIFEST, recipes=tuple(recipe if r == base else r for r in MANIFEST.recipes)
    )
    assert recipe_from_json(recipe.to_json()) == recipe
    schema = recipe_wire_schema(recipe)
    assert "enhance" not in schema["required"]
    assert schema["properties"]["enhance"]["default"] is False
    wire = {
        "text": "你好",
        "voice_asset_id": "a" * 32,
        "reference_transcript": "完整文字稿",
        "performance_instruction": None,
        "seed": 42,
    }
    _, params = parse_recipe_wire(recipe, wire)
    assert params.pop("enhance") is False
    inputs = {"voice": [MediaInput("audio/wav", b"wav")]}
    assert (
        build_recipe_request(manifest, recipe.id, inputs=inputs, params=params).param("enhance")
        is False
    )
    assert parse_recipe_wire(recipe, {**wire, "enhance": True})[1]["enhance"] is True
    with pytest.raises(ValueError):
        parse_recipe_wire(recipe, {**wire, "enhance": 1})
    with pytest.raises(ValueError, match="seed"):
        parse_recipe_wire(recipe, {key: value for key, value in wire.items() if key != "seed"})
