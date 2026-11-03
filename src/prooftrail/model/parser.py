"""Reading specification files (YAML or JSON) into validated models.

The pipeline is deliberately strict because specification files are untrusted
input:

* the file size, nesting depth and element counts are capped;
* YAML is read with a ``SafeLoader`` subclass that also rejects duplicate keys
  and YAML aliases/anchors (so there is no "billion laughs" expansion);
* JSON rejects duplicate keys and ``NaN``/``Infinity``;
* unknown keys are errors, never silently ignored;
* every expression is compiled by the restricted evaluator in
  :mod:`prooftrail.model.expressions` - nothing is ever passed to ``eval``.

All problems raise :class:`~prooftrail.errors.SpecError` with a message that
names the offending section.
"""

from __future__ import annotations

import hashlib
import json
import keyword
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from prooftrail.errors import SpecError
from prooftrail.model.expressions import BOOL, Expression, compile_expression
from prooftrail.model.system import (
    BOOLEAN_KIND,
    ENUM_KIND,
    INTEGER_KIND,
    TransitionSystem,
    VariableDecl,
)
from prooftrail.model.transition import Transition
from prooftrail.properties.base import Property
from prooftrail.properties.ctl import (
    CTLProperty,
    ResponseProperty,
    parse_ctl,
    validate_labels,
)
from prooftrail.properties.deadlock import DeadlockFreedom
from prooftrail.properties.invariant import InvariantProperty
from prooftrail.properties.reachability import ReachabilityProperty

SUPPORTED_SCHEMA_VERSIONS = ("1.0",)

MAX_SPEC_BYTES = 1_000_000
MAX_NESTING_DEPTH = 24
MAX_NODES = 50_000
MAX_VARIABLES = 64
MAX_TRANSITIONS = 512
MAX_PROPERTIES = 256
MAX_LABELS = 128
MAX_INITIAL_VALUES = 1_000
MAX_ENUM_VALUES = 64
MAX_ID_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1_000

_ID_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_RESERVED_NAMES = frozenset({"true", "false", "True", "False", "None"})

_TOP_KEYS = {"schema_version", "model", "properties"}
_MODEL_KEYS = {"id", "description", "variables", "transitions", "labels"}
_VARIABLE_KEYS = {"type", "initial", "min", "max", "values"}
_TRANSITION_KEYS = {"id", "description", "when", "updates"}
_PROPERTY_KEYS: dict[str, set[str]] = {
    "invariant": {"expression"},
    "reachability": {"target"},
    "deadlock_free": {"unless"},
    "ctl": {"formula"},
    "response": {"trigger", "response"},
}
_PROPERTY_COMMON_KEYS = {"id", "type", "description"}


@dataclass(frozen=True)
class Specification:
    """A parsed specification: a transition system plus properties to check."""

    system: TransitionSystem
    properties: tuple[Property, ...]
    schema_version: str
    source: str | None = None
    sha256: str | None = None

    def property(self, property_id: str) -> Property:
        for prop in self.properties:
            if prop.id == property_id:
                return prop
        known = ", ".join(p.id for p in self.properties) or "none"
        raise SpecError(f"Unknown property {property_id!r} (declared: {known}).")


# ------------------------------------------------------------------ loading


def load_specification(path: str | Path) -> Specification:
    """Read and validate the specification file at ``path`` (.yaml/.yml/.json)."""
    file = Path(path)
    suffix = file.suffix.lower()
    if suffix not in (".yaml", ".yml", ".json"):
        raise SpecError(
            f"{file}: unsupported file type {suffix or '(none)'!r}; "
            "use .yaml, .yml or .json."
        )
    try:
        if not file.is_file():
            raise SpecError(f"{file}: not a file.")
        with file.open("rb") as handle:
            raw = handle.read(MAX_SPEC_BYTES + 1)
    except OSError as exc:
        raise SpecError(f"{file}: cannot read file ({exc.strerror or exc}).") from None
    if len(raw) > MAX_SPEC_BYTES:
        raise SpecError(f"{file}: larger than the {MAX_SPEC_BYTES}-byte limit.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SpecError(f"{file}: not valid UTF-8 text.") from None

    spec = parse_specification_text(
        text, fmt="json" if suffix == ".json" else "yaml", source=str(file)
    )
    return Specification(
        system=spec.system,
        properties=spec.properties,
        schema_version=spec.schema_version,
        source=str(file),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def parse_specification_text(
    text: str, *, fmt: str = "yaml", source: str | None = None
) -> Specification:
    """Parse specification ``text`` written in ``fmt`` (``"yaml"`` or ``"json"``)."""
    label = source or "<string>"
    if len(text.encode("utf-8")) > MAX_SPEC_BYTES:
        raise SpecError(f"{label}: larger than the {MAX_SPEC_BYTES}-byte limit.")
    if fmt == "yaml":
        data = _load_yaml(text, label)
    elif fmt == "json":
        data = _load_json(text, label)
    else:
        raise ValueError(f"unknown format {fmt!r}")
    return parse_specification(data, source=source)


def parse_specification(data: object, *, source: str | None = None) -> Specification:
    """Validate an already-loaded specification mapping."""
    _check_shape(data)
    top = _mapping(data, "specification")
    _only_keys(top, _TOP_KEYS, "specification")

    version = top.get("schema_version")
    if not isinstance(version, str):
        raise SpecError(
            'schema_version is required and must be a quoted string such as "1.0".'
        )
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise SpecError(
            f"Unsupported schema_version {version!r}; "
            f"supported: {', '.join(SUPPORTED_SCHEMA_VERSIONS)}."
        )
    if "model" not in top:
        raise SpecError("The specification has no 'model' section.")

    system = _parse_model(_mapping(top["model"], "model"))
    scope = {decl.name: decl.expr_type for decl in system.variables}
    properties = _parse_properties(top.get("properties", []), scope, system.labels)
    return Specification(system, tuple(properties), version, source)


# ---------------------------------------------------------- raw file readers


class _StrictYamlLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys and YAML aliases."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.events.AliasEvent):
            event = self.peek_event()
            raise yaml.constructor.ConstructorError(
                None, None, "YAML aliases are not allowed", event.start_mark
            )
        return super().compose_node(parent, index)

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in seen
            except TypeError:
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    "mapping keys must be simple values",
                    key_node.start_mark,
                ) from None
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _load_yaml(text: str, label: str) -> object:
    try:
        return yaml.load(text, Loader=_StrictYamlLoader)  # noqa: S506 - SafeLoader
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        raise SpecError(f"{label}: invalid YAML{where}: {problem}.") from None
    except RecursionError:
        raise SpecError(f"{label}: YAML is nested too deeply.") from None


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise ValueError(f"invalid JSON constant {name}")


def _load_json(text: str, label: str) -> object:
    try:
        return json.loads(
            text, object_pairs_hook=_json_object, parse_constant=_reject_constant
        )
    except json.JSONDecodeError as exc:
        raise SpecError(
            f"{label}: invalid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}."
        ) from None
    except ValueError as exc:
        raise SpecError(f"{label}: invalid JSON: {exc}.") from None
    except RecursionError:
        raise SpecError(f"{label}: JSON is nested too deeply.") from None


def _check_shape(data: object) -> None:
    """Bound nesting depth and total node count of the loaded document."""
    nodes = 0
    stack: list[tuple[object, int]] = [(data, 1)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            raise SpecError(f"The specification has more than {MAX_NODES} elements.")
        if depth > MAX_NESTING_DEPTH:
            raise SpecError(
                f"The specification is nested deeper than {MAX_NESTING_DEPTH}."
            )
        if isinstance(item, Mapping):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
        elif not isinstance(item, str | int | bool) and item is not None:
            raise SpecError(
                f"Unsupported value of type {type(item).__name__} in the specification "
                "(only strings, integers, booleans, lists and mappings are allowed)."
            )


# -------------------------------------------------------------- small helpers


def _mapping(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SpecError(f"{where} must be a mapping, got {_type_name(value)}.")
    for key in value:
        if not isinstance(key, str):
            raise SpecError(f"{where} has a non-string key {key!r}.")
    return value


def _sequence(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise SpecError(f"{where} must be a list, got {_type_name(value)}.")
    return value


def _type_name(value: object) -> str:
    return "null" if value is None else type(value).__name__


def _only_keys(mapping: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise SpecError(
            f"{where} has unknown key(s) {unknown}; allowed: {sorted(allowed)}."
        )


def _identifier(value: object, where: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.match(value):
        raise SpecError(
            f"{where} must be a string of letters, digits, '_', '-' or '.', "
            f"starting with a letter or '_' (got {value!r})."
        )
    if len(value) > MAX_ID_LENGTH:
        raise SpecError(f"{where} is longer than {MAX_ID_LENGTH} characters.")
    return value


def _description(mapping: Mapping[str, Any], where: str) -> str:
    value = mapping.get("description", "")
    if not isinstance(value, str):
        raise SpecError(f"{where} description must be a string.")
    if len(value) > MAX_DESCRIPTION_LENGTH:
        raise SpecError(f"{where} description is longer than {MAX_DESCRIPTION_LENGTH}.")
    return value


def _is_int(value: object) -> bool:
    return type(value) is int


# ------------------------------------------------------------------- model


def _parse_model(model: Mapping[str, Any]) -> TransitionSystem:
    _only_keys(model, _MODEL_KEYS, "model")
    model_id = _identifier(model.get("id"), "model id")
    description = _description(model, "model")

    raw_vars = _mapping(model.get("variables"), "model variables")
    if not raw_vars:
        raise SpecError("model variables must declare at least one variable.")
    if len(raw_vars) > MAX_VARIABLES:
        raise SpecError(f"A model may declare at most {MAX_VARIABLES} variables.")
    variables = [_parse_variable(name, spec) for name, spec in raw_vars.items()]
    scope = {decl.name: decl.expr_type for decl in variables}
    decls = {decl.name: decl for decl in variables}

    raw_transitions = _sequence(model.get("transitions", []), "model transitions")
    if len(raw_transitions) > MAX_TRANSITIONS:
        raise SpecError(f"A model may declare at most {MAX_TRANSITIONS} transitions.")
    transitions = [
        _parse_transition(item, index, scope, decls)
        for index, item in enumerate(raw_transitions)
    ]

    raw_labels = _mapping(model.get("labels", {}), "model labels")
    if len(raw_labels) > MAX_LABELS:
        raise SpecError(f"A model may declare at most {MAX_LABELS} labels.")
    labels: dict[str, Expression] = {}
    for name, source in raw_labels.items():
        _identifier(name, "label name")
        labels[name] = compile_expression(
            _expression_source(source),
            scope,
            expected=BOOL,
            context=f"label {name!r}",
        )
    validate_labels(labels)

    return TransitionSystem(model_id, variables, transitions, labels, description)


def _parse_variable(name: str, raw: object) -> VariableDecl:
    where = f"variable {name!r}"
    if (
        not name.isidentifier()
        or keyword.iskeyword(name)
        or name in _RESERVED_NAMES
        or name.startswith("__")
    ):
        raise SpecError(
            f"{where}: not a usable variable name (must be a plain identifier that "
            "is not a Python keyword, true/false/None or dunder name)."
        )
    if len(name) > MAX_ID_LENGTH:
        raise SpecError(f"{where}: name is longer than {MAX_ID_LENGTH} characters.")
    spec = _mapping(raw, where)
    _only_keys(spec, _VARIABLE_KEYS, where)

    kind = spec.get("type")
    if kind not in (INTEGER_KIND, BOOLEAN_KIND, ENUM_KIND):
        raise SpecError(
            f"{where}: type must be one of integer, boolean, enum (got {kind!r})."
        )

    minimum = spec.get("min")
    maximum = spec.get("max")
    values = spec.get("values")
    if kind != INTEGER_KIND and (minimum is not None or maximum is not None):
        raise SpecError(f"{where}: only integer variables may have min/max.")
    if kind != ENUM_KIND and values is not None:
        raise SpecError(f"{where}: only enum variables may list values.")
    for bound_name, bound in (("min", minimum), ("max", maximum)):
        if bound is not None and not _is_int(bound):
            raise SpecError(f"{where}: {bound_name} must be an integer.")

    enum_values: tuple[str, ...] = ()
    if kind == ENUM_KIND:
        items = _sequence(values, f"{where} values")
        if not items or len(items) > MAX_ENUM_VALUES:
            raise SpecError(
                f"{where}: enum needs between 1 and {MAX_ENUM_VALUES} values."
            )
        for item in items:
            if not isinstance(item, str) or not item or len(item) > 64:
                raise SpecError(
                    f"{where}: enum values must be short non-empty strings."
                )
        enum_values = tuple(items)

    if "initial" not in spec:
        raise SpecError(f"{where}: 'initial' is required.")
    raw_initial = spec["initial"]
    initial_items = raw_initial if isinstance(raw_initial, list) else [raw_initial]
    if not initial_items or len(initial_items) > MAX_INITIAL_VALUES:
        raise SpecError(
            f"{where}: initial needs between 1 and {MAX_INITIAL_VALUES} values."
        )
    for item in initial_items:
        ok = (
            (kind == INTEGER_KIND and _is_int(item))
            or (kind == BOOLEAN_KIND and type(item) is bool)
            or (kind == ENUM_KIND and isinstance(item, str))
        )
        if not ok:
            raise SpecError(
                f"{where}: initial value {item!r} is not a valid {kind} value."
            )

    return VariableDecl(
        name=name,
        kind=kind,
        initial=tuple(initial_items),
        minimum=minimum,
        maximum=maximum,
        values=enum_values,
    )


def _expression_source(value: object) -> object:
    """Accept YAML booleans/integers where an expression string is expected."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if _is_int(value):
        return str(value)
    return value


def _parse_transition(
    raw: object,
    index: int,
    scope: Mapping[str, str],
    decls: Mapping[str, VariableDecl],
) -> Transition:
    where = f"transition #{index + 1}"
    spec = _mapping(raw, where)
    _only_keys(spec, _TRANSITION_KEYS, where)
    tid = _identifier(spec.get("id"), f"{where} id")
    where = f"transition {tid!r}"
    description = _description(spec, where)

    guard = compile_expression(
        _expression_source(spec.get("when", "true")),
        scope,
        expected=BOOL,
        context=f"{where} guard",
    )

    raw_updates = _mapping(spec.get("updates", {}), f"{where} updates")
    updates: list[tuple[str, Expression]] = []
    for name, source in raw_updates.items():
        decl = decls.get(name)
        if decl is None:
            known = ", ".join(sorted(decls))
            raise SpecError(
                f"{where} updates unknown variable {name!r} (known variables: {known})."
            )
        text = _expression_source(source)
        # Convenience: a bare enum value such as ``green`` (not a variable name)
        # assigned to an enum variable means the string literal "green".
        if decl.kind == ENUM_KIND and isinstance(text, str) and text in decl.values:
            if text in scope:
                raise SpecError(
                    f"{where} update of {name!r}: {text!r} is both a variable and "
                    f"a value of {name!r}. Write the string literal with quotes "
                    f"('\"{text}\"') or rename the variable or the enum value."
                )
            text = json.dumps(text)
        updates.append(
            (
                name,
                compile_expression(
                    text,
                    scope,
                    expected=decl.expr_type,
                    context=f"{where} update of {name!r}",
                ),
            )
        )
    return Transition(tid, guard, tuple(updates), description)


# -------------------------------------------------------------- properties


def _parse_properties(
    raw: object, scope: Mapping[str, str], labels: Mapping[str, Expression]
) -> list[Property]:
    items = _sequence(raw, "properties")
    if len(items) > MAX_PROPERTIES:
        raise SpecError(
            f"A specification may declare at most {MAX_PROPERTIES} properties."
        )
    properties: list[Property] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        prop = _parse_property(item, index, scope, labels)
        if prop.id in seen:
            raise SpecError(f"Duplicate property id {prop.id!r}.")
        seen.add(prop.id)
        properties.append(prop)
    return properties


def _required(spec: Mapping[str, Any], key: str, where: str) -> object:
    if key not in spec:
        raise SpecError(f"{where} requires a {key!r} field.")
    return _expression_source(spec[key])


def _parse_property(
    raw: object,
    index: int,
    scope: Mapping[str, str],
    labels: Mapping[str, Expression],
) -> Property:
    where = f"property #{index + 1}"
    spec = _mapping(raw, where)
    kind = spec.get("type")
    if kind not in _PROPERTY_KEYS:
        raise SpecError(
            f"{where}: type must be one of {', '.join(sorted(_PROPERTY_KEYS))} "
            f"(got {kind!r})."
        )
    _only_keys(spec, _PROPERTY_COMMON_KEYS | _PROPERTY_KEYS[kind], where)
    pid = _identifier(spec.get("id"), f"{where} id")
    where = f"property {pid!r}"
    description = _description(spec, where)

    def expr(key: str) -> Expression:
        return compile_expression(
            _required(spec, key, where),
            scope,
            expected=BOOL,
            context=f"{where} {key}",
        )

    if kind == "invariant":
        return InvariantProperty(pid, description, expression=expr("expression"))
    if kind == "reachability":
        return ReachabilityProperty(pid, description, target=expr("target"))
    if kind == "deadlock_free":
        unless = expr("unless") if "unless" in spec else None
        return DeadlockFreedom(pid, description, unless=unless)
    if kind == "response":
        return ResponseProperty(
            pid, description, trigger=expr("trigger"), response=expr("response")
        )
    source = spec.get("formula")
    formula = parse_ctl(source, scope, labels, context=f"{where} formula")
    return CTLProperty(pid, description, formula=formula, source=str(source))
