"""扁平发布只改变形状：工具不丢失，条件进入说明，默认发布与运行时校验不变。"""

import json
from pathlib import Path

import pytest

from integrations.slatehand_mcp.app import (
    flat_tool_schema,
    listed_tool,
    playground_tool_schema,
    published_models,
    published_tool_schema,
)
from integrations.slatehand_mcp.playground_tools import PLAYGROUND_MODELS

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcp_project_tools.json"


def project_schemas():
    return {name: published_tool_schema(name, model) for name, model in published_models().items()}


def test_project_tools_have_no_top_level_combinators_when_flat():
    schemas = project_schemas()
    assert len(schemas) == 31
    for name, schema in schemas.items():
        flat, _rules = flat_tool_schema(schema)
        assert flat["type"] == "object", name
        assert not {"oneOf", "anyOf", "allOf"} & set(flat), name
        assert flat["required"] == schema["required"], name
        assert set(flat["properties"]) == set(schema["properties"]), name
        assert flat["additionalProperties"] is False, name


def test_playground_tools_have_no_top_level_one_of_when_flat():
    for name, model in PLAYGROUND_MODELS.items():
        flat, _rules = flat_tool_schema(playground_tool_schema(name, model))
        assert "oneOf" not in flat, name


def test_flat_schema_moves_branch_conditions_into_description():
    schemas = project_schemas()
    _flat, rules = flat_tool_schema(schemas["media_batch"])
    assert "action=start：必填 target_ids，省略 run_id" in rules
    assert "action=continue：必填 run_id，省略 target_ids" in rules
    _flat, rules = flat_tool_schema(schemas["manage_generation"])
    assert "action=wait：无附加条件" in rules
    assert "必填 expected_revision、idempotency_key，generation_ids 只给 1 项" in rules


def test_flat_schema_keeps_bare_object_fields_from_branches():
    schema = project_schemas()["submit_proposal"]
    flat, _rules = flat_tool_schema(schema)
    variants = flat["properties"]["payload"]["anyOf"]
    assert variants == [branch["properties"]["payload"] for branch in schema["oneOf"]]
    assert all("oneOf" in variant for variant in variants)


def test_flat_schema_leaves_tools_without_branches_and_the_input_untouched():
    schemas = project_schemas()
    flat, rules = flat_tool_schema(schemas["read_workflow"])
    assert flat == schemas["read_workflow"] and rules == ""
    original = json.dumps(schemas["read_story"], sort_keys=True)
    flat_tool_schema(schemas["read_story"])
    assert json.dumps(schemas["read_story"], sort_keys=True) == original


@pytest.mark.parametrize("name", ["read_story", "submit_proposal"])
def test_default_listing_stays_identical_to_the_baseline(name):
    baseline = {tool["name"]: tool for tool in json.loads(FIXTURE.read_text())}
    tool = listed_tool(name, "说明", project_schemas()[name], flat=False)
    assert tool.description == "说明"
    assert tool.input_schema == baseline[name]["inputSchema"]
