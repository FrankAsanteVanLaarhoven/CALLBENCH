"""Contract tests for the tool surface.

Every tool is checked for the properties the benchmark relies on. These are
cheap and there are a lot of them on purpose: a schema registry that silently
drifts from its handlers invalidates every result computed against it.
"""

from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from callbench.contracts import SideEffect
from callbench.schemas import CANONICAL_TOOLS, get_catalogue, schema_errors
from callbench.simulator.tools import HANDLERS

CATALOGUES = ("catalogue_v1", "catalogue_v4")
TOOL_NAMES = [spec.name for spec in CANONICAL_TOOLS]


@pytest.mark.contract
@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_schema_is_valid_draft_2020_12(tool: str) -> None:
    spec = get_catalogue("catalogue_v1").spec(tool)
    Draft202012Validator.check_schema(spec.input_schema)


@pytest.mark.contract
@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_schema_forbids_undeclared_properties(tool: str) -> None:
    """Without this, T04 is unobservable and every payload looks valid."""
    schema = get_catalogue("catalogue_v1").spec(tool).input_schema
    assert schema["additionalProperties"] is False


@pytest.mark.contract
@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_required_fields_are_declared_properties(tool: str) -> None:
    schema = get_catalogue("catalogue_v1").spec(tool).input_schema
    assert set(schema.get("required", [])) <= set(schema["properties"])


@pytest.mark.contract
@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_every_tool_has_a_handler(tool: str) -> None:
    assert tool in HANDLERS


@pytest.mark.contract
@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_side_effect_is_declared_consistently(tool: str) -> None:
    spec = get_catalogue("catalogue_v1").spec(tool)
    if spec.side_effect is SideEffect.NONE:
        assert spec.writes == ()
        assert spec.idempotent
    else:
        assert spec.writes, f"{tool} declares a side effect but writes nothing"


@pytest.mark.contract
@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_undeclared_property_is_reported_as_t04(tool: str) -> None:
    payload = _minimal_payload(tool)
    payload["definitely_not_a_field"] = 1
    codes = {code for code, _ in schema_errors(tool, payload, "catalogue_v1")}
    assert "T04_UNDECLARED_ARGUMENT" in codes


@pytest.mark.contract
@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_missing_required_is_reported_as_t02(tool: str) -> None:
    schema = get_catalogue("catalogue_v1").spec(tool).input_schema
    required = schema.get("required", [])
    if not required:
        pytest.skip(f"{tool} has no required fields")
    payload = _minimal_payload(tool)
    payload.pop(required[0])
    codes = {code for code, _ in schema_errors(tool, payload, "catalogue_v1")}
    assert "T02_MISSING_REQUIRED_ARGUMENT" in codes


@pytest.mark.contract
@pytest.mark.parametrize("catalogue_name", CATALOGUES)
def test_catalogue_covers_every_tool(catalogue_name: str) -> None:
    assert len(get_catalogue(catalogue_name)) == len(CANONICAL_TOOLS)


@pytest.mark.contract
@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_v4_renames_but_preserves_the_schema(tool: str) -> None:
    """The hidden partition must change names only.

    If v4 also changed schemas, a drop in hidden-partition score would be
    ambiguous between "learned tool names" and "cannot read a new schema".
    """
    v1 = get_catalogue("catalogue_v1")
    v4 = get_catalogue("catalogue_v4")
    renamed = v4.presented(tool)
    assert renamed != tool, f"{tool} was not renamed in catalogue_v4"
    assert v4.spec(renamed).input_schema == v1.spec(tool).input_schema
    assert v4.canonical(renamed) == tool


def _minimal_payload(tool: str) -> dict:
    schema = get_catalogue("catalogue_v1").spec(tool).input_schema
    payload: dict = {}
    for field in schema.get("required", []):
        payload[field] = _example(schema["properties"][field])
    return payload


def _example(field_schema: dict):
    declared = field_schema.get("type")
    if declared == "array":
        return [_example(field_schema.get("items", {"type": "string"}))]
    if declared == "integer":
        return 1
    if declared == "boolean":
        return True
    if declared == "object":
        return {}
    if field_schema.get("format") == "email":
        return "someone@company.test"
    if field_schema.get("format") == "date-time":
        return "2026-08-05T09:00:00+00:00"
    if (enum := field_schema.get("enum")):
        return enum[0]
    return "msg_101"
