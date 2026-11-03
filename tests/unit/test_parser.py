"""Specification loading: valid files, every kind of rejection, safety limits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prooftrail.errors import SpecError
from prooftrail.model import parser
from prooftrail.model.parser import (
    load_specification,
    parse_specification,
    parse_specification_text,
)

MINIMAL = """
schema_version: "1.0"
model:
  id: m
  variables:
    x: {type: integer, min: 0, max: 3, initial: 0}
  transitions:
    - id: inc
      when: "x < 3"
      updates: {x: "x + 1"}
properties:
  - {id: p, type: invariant, expression: "x <= 3"}
"""


def minimal(**changes: object) -> dict:
    data = {
        "schema_version": "1.0",
        "model": {
            "id": "m",
            "variables": {"x": {"type": "integer", "min": 0, "max": 3, "initial": 0}},
            "transitions": [{"id": "inc", "when": "x < 3", "updates": {"x": "x + 1"}}],
        },
        "properties": [{"id": "p", "type": "invariant", "expression": "x <= 3"}],
    }
    data.update(changes)
    return data


# ----------------------------------------------------------- valid input


def test_yaml_and_json_give_the_same_model(examples_dir: Path) -> None:
    yaml_spec = load_specification(examples_dir / "counter" / "unsafe_counter.yaml")
    json_spec = load_specification(examples_dir / "counter" / "unsafe_counter.json")
    assert yaml_spec.system.variable_names() == json_spec.system.variable_names()
    assert yaml_spec.system.initial_states() == json_spec.system.initial_states()
    assert [p.id for p in yaml_spec.properties] == [p.id for p in json_spec.properties]


def test_load_records_source_and_hash(examples_dir: Path) -> None:
    spec = load_specification(examples_dir / "counter" / "safe_counter.yaml")
    assert spec.source is not None and spec.source.endswith("safe_counter.yaml")
    assert spec.sha256 is not None and len(spec.sha256) == 64


def test_every_valid_example_loads(examples_dir: Path) -> None:
    invalid = {p.name for p in (examples_dir / "invalid").iterdir()}
    paths = [
        p
        for p in examples_dir.rglob("*")
        if p.suffix in {".yaml", ".yml", ".json"}
        and p.name not in invalid
        and p.name != "manifest.json"
        and p.name != "domain_error.yaml"
    ]
    assert len(paths) >= 10
    for path in paths:
        spec = load_specification(path)
        assert spec.system.id


def test_enum_value_shorthand_in_updates() -> None:
    text = """
schema_version: "1.0"
model:
  id: m
  variables:
    light: {type: enum, values: [red, green], initial: red}
  transitions:
    - {id: go, when: 'light == "red"', updates: {light: green}}
"""
    spec = parse_specification_text(text)
    state = spec.system.initial_states()[0]
    assert spec.system.successors(state)[0][1]["light"] == "green"


def test_enum_shorthand_that_clashes_with_a_variable_is_rejected() -> None:
    data = minimal()
    data["model"]["variables"] = {
        "red": {"type": "boolean", "initial": False},
        "light": {"type": "enum", "values": ["red", "green"], "initial": "red"},
    }
    data["model"]["transitions"] = [{"id": "t", "updates": {"light": "red"}}]
    data["properties"] = []
    with pytest.raises(SpecError, match="both a variable"):
        parse_specification(data)


def test_scalar_initial_and_list_initial_are_both_accepted() -> None:
    data = minimal()
    data["model"]["variables"]["x"]["initial"] = [0, 1]
    spec = parse_specification(data)
    assert len(spec.system.initial_states()) == 2


def test_variable_declaration_order_does_not_matter() -> None:
    first = minimal()
    first["model"]["variables"]["y"] = {"type": "boolean", "initial": True}
    second = minimal()
    second["model"]["variables"] = {
        "y": {"type": "boolean", "initial": True},
        "x": {"type": "integer", "min": 0, "max": 3, "initial": 0},
    }
    a, b = parse_specification(first), parse_specification(second)
    assert a.system.initial_states() == b.system.initial_states()
    state = a.system.initial_states()[0]
    assert a.system.successors(state) == b.system.successors(state)


def test_properties_are_optional() -> None:
    data = minimal()
    del data["properties"]
    assert parse_specification(data).properties == ()


def test_property_lookup() -> None:
    spec = parse_specification(minimal())
    assert spec.property("p").id == "p"
    with pytest.raises(SpecError, match="Unknown property 'nope'"):
        spec.property("nope")


# ----------------------------------------------------------- rejection

INVALID = [
    ("syntax_error.yaml", "syntax error"),
    ("unknown_variable.yaml", "unknown variable"),
    ("type_error.yaml", "must be int"),
    ("code_injection.yaml", "unsupported construct"),
    ("duplicate_property.yaml", "duplicate property id"),
    ("bad_schema_version.yaml", "unsupported schema_version"),
    ("yaml_alias.yaml", "aliases are not allowed"),
    ("unknown_key.yaml", "unknown key"),
    ("duplicate_key.yaml", "duplicate key"),
    ("bool_as_int.yaml", "not a valid integer value"),
    ("bad_ctl.yaml", "unknown identifier"),
    ("initial_out_of_domain.yaml", "outside its domain"),
]


@pytest.mark.parametrize(("name", "fragment"), INVALID)
def test_invalid_examples_are_rejected_with_a_specific_message(
    examples_dir: Path, name: str, fragment: str
) -> None:
    with pytest.raises(SpecError) as info:
        load_specification(examples_dir / "invalid" / name)
    assert fragment in str(info.value).lower()


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda d: d.pop("schema_version"), "schema_version is required"),
        (lambda d: d.update(schema_version=1), "quoted string"),
        (lambda d: d.pop("model"), "no 'model' section"),
        (lambda d: d.update(extra=1), "unknown key"),
        (lambda d: d["model"].update(id="bad id!"), "model id"),
        (lambda d: d["model"].update(variables={}), "at least one variable"),
        (lambda d: d["model"].update(variables=[]), "must be a mapping"),
        (
            lambda d: d["model"]["variables"]["x"].update(type="float"),
            "type must be one of",
        ),
        (
            lambda d: d["model"]["variables"]["x"].update(min="low"),
            "min must be an integer",
        ),
        (
            lambda d: d["model"]["variables"]["x"].pop("initial"),
            "'initial' is required",
        ),
        (
            lambda d: d["model"]["variables"]["x"].update(initial=True),
            "not a valid integer value",
        ),
        (
            lambda d: d["model"]["variables"]["x"].update(min=5, max=1, initial=3),
            "min greater than max",
        ),
        (
            lambda d: d["model"]["variables"].update(
                {"if": {"type": "boolean", "initial": False}}
            ),
            "not a usable variable name",
        ),
        (
            lambda d: d["model"]["variables"].update(
                {"__x": {"type": "boolean", "initial": False}}
            ),
            "not a usable variable name",
        ),
        (
            lambda d: d["model"]["transitions"].append({"id": "inc", "updates": {}}),
            "Transition ids must be unique",
        ),
        (
            lambda d: d["model"]["transitions"][0].update(updates={"nope": "1"}),
            "unknown variable",
        ),
        (
            lambda d: d["model"]["transitions"][0].update(when="x"),
            "expected a bool",
        ),
        (
            lambda d: d["properties"].append({"id": "q", "type": "magic"}),
            "type must be one of",
        ),
        (
            lambda d: d["properties"][0].update(expression=None),
            "expected a string",
        ),
        (
            lambda d: d["properties"].append({"id": "q", "type": "reachability"}),
            "requires a 'target'",
        ),
        (
            lambda d: d["properties"].append(
                {"id": "q", "type": "invariant", "expression": "x < 1", "target": "x"}
            ),
            "unknown key",
        ),
    ],
)
def test_malformed_specifications_are_rejected(mutate, fragment: str) -> None:
    data = minimal()
    mutate(data)
    with pytest.raises(SpecError) as info:
        parse_specification(data)
    assert fragment.lower() in str(info.value).lower()


def test_a_transition_assigning_a_value_of_the_wrong_type_is_rejected() -> None:
    data = minimal()
    data["model"]["transitions"][0]["updates"] = {"x": "true"}
    with pytest.raises(SpecError, match="expected a int"):
        parse_specification(data)


def test_initial_state_explosion_is_rejected() -> None:
    values = list(range(400))
    data = minimal()
    data["model"]["variables"] = {
        name: {"type": "integer", "min": 0, "max": 399, "initial": values}
        for name in ("a", "b", "c")
    }
    data["model"]["transitions"] = []
    data["properties"] = []
    with pytest.raises(SpecError, match="initial states"):
        parse_specification(data)


# --------------------------------------------------------- file handling


def test_unsupported_extension(spec_file) -> None:
    path = spec_file(MINIMAL, "model.txt")
    with pytest.raises(SpecError, match="unsupported file type"):
        load_specification(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match="not a file"):
        load_specification(tmp_path / "nothing.yaml")


def test_directory_is_not_a_file(tmp_path: Path) -> None:
    directory = tmp_path / "dir.yaml"
    directory.mkdir()
    with pytest.raises(SpecError, match="not a file"):
        load_specification(directory)


def test_oversized_file_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(parser, "MAX_SPEC_BYTES", 100)
    path = tmp_path / "big.yaml"
    path.write_text(MINIMAL, encoding="utf-8")
    with pytest.raises(SpecError, match="byte limit"):
        load_specification(path)


def test_non_utf8_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(SpecError, match="UTF-8"):
        load_specification(path)


def test_invalid_yaml_reports_a_position() -> None:
    with pytest.raises(SpecError, match=r"invalid YAML \(line"):
        parse_specification_text("model: [unclosed")


def test_invalid_json_reports_a_position() -> None:
    with pytest.raises(SpecError, match=r"invalid JSON \(line"):
        parse_specification_text("{not json", fmt="json")


def test_json_duplicate_keys_and_constants_are_rejected() -> None:
    with pytest.raises(SpecError, match="duplicate key"):
        parse_specification_text('{"a": 1, "a": 2}', fmt="json")
    with pytest.raises(SpecError, match="invalid JSON constant"):
        parse_specification_text('{"a": NaN}', fmt="json")


def test_unknown_format_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        parse_specification_text("{}", fmt="toml")


def test_yaml_non_scalar_values_are_rejected() -> None:
    # A YAML timestamp is not one of the allowed JSON-like value types.
    with pytest.raises(SpecError, match="Unsupported value"):
        parse_specification_text("schema_version: 2020-01-01\n")


def test_deeply_nested_input_is_rejected() -> None:
    text = "a: " + "[" * 40 + "]" * 40
    with pytest.raises(SpecError, match="nested"):
        parse_specification_text(text)


def test_too_many_elements_are_rejected(monkeypatch) -> None:
    monkeypatch.setattr(parser, "MAX_NODES", 10)
    with pytest.raises(SpecError, match="more than 10 elements"):
        parse_specification(minimal())


def test_non_string_keys_are_rejected() -> None:
    with pytest.raises(SpecError, match="non-string key"):
        parse_specification({1: "x"})


def test_json_text_roundtrip() -> None:
    text = json.dumps(minimal())
    assert parse_specification_text(text, fmt="json").system.id == "m"
