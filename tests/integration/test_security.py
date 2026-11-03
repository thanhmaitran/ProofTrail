"""Specification files are untrusted: nothing in them may run as code."""

from __future__ import annotations

from pathlib import Path

import pytest

from prooftrail.errors import ModelError, SpecError
from prooftrail.model.expressions import compile_expression
from prooftrail.model.parser import load_specification, parse_specification_text

SCOPE = {"x": "int", "b": "bool", "color": "str"}

ATTACKS = [
    "__import__('os').system('touch {marker}')",
    "open('{marker}', 'w')",
    "x.__class__",
    "().__class__.__bases__[0].__subclasses__()",
    "(lambda: 1)()",
    "[i for i in range(3)]",
    "{i: i for i in range(3)}",
    "x ** 99999999",
    "x / 2",
    "x << 3",
    "x & 1",
    "~x",
    "x if x else b",
    "f'{x}'",
    "x := 3",
    "print(x)",
    "eval('1')",
    "exec('pass')",
    "x[0]",
    "x.real",
    "await x",
    "yield x",
    "*x",
    "x is x",
    "x is not None",
    "1 if 2 else 3 if",
    "'a' + 'b'",
    "color + 'x'",
    "b + 1",
    "not x",
    "-b",
    "x < b",
    "x in [y for y in (1,)]",
    "x in (x,)",
    "x in ()",
    "x in 3",
    "None",
    "1.5",
    "1j",
    "b'bytes'",
    "...",
]


@pytest.mark.parametrize("attack", ATTACKS)
def test_hostile_expressions_are_rejected_and_never_executed(
    attack: str, tmp_path: Path
) -> None:
    marker = tmp_path / "pwned"
    source = attack.replace("{marker}", str(marker))
    with pytest.raises(SpecError):
        compile_expression(source, SCOPE)
    assert not marker.exists()


def test_a_hostile_specification_file_is_rejected_end_to_end(
    tmp_path: Path, spec_file
) -> None:
    marker = tmp_path / "pwned"
    path = spec_file(
        f"""
schema_version: "1.0"
model:
  id: evil
  variables:
    x: {{type: integer, min: 0, max: 3, initial: 0}}
  transitions:
    - id: boom
      when: "__import__('pathlib').Path('{marker}').write_text('x')"
properties: []
"""
    )
    with pytest.raises(SpecError, match="unsupported construct"):
        load_specification(path)
    assert not marker.exists()


@pytest.mark.parametrize(
    "document",
    [
        "!!python/object/apply:os.system ['true']",
        "model: !!python/name:os.system",
        'schema_version: "1.0"\nmodel: !!python/object:builtins.object {}',
    ],
)
def test_python_yaml_tags_are_rejected(document: str) -> None:
    with pytest.raises(SpecError, match="invalid YAML"):
        parse_specification_text(document)


def test_yaml_billion_laughs_is_rejected_without_expansion() -> None:
    document = (
        'a: &a ["lol","lol","lol","lol","lol","lol","lol","lol","lol"]\n'
        "b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]\n"
        "c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]\n"
    )
    with pytest.raises(SpecError, match="aliases are not allowed"):
        parse_specification_text(document)


def test_huge_literals_and_overflows_are_bounded() -> None:
    with pytest.raises(SpecError, match="exceeds 64 bits"):
        compile_expression(str(2**70), SCOPE)
    big = compile_expression("x * x * x * x", SCOPE)
    with pytest.raises(ModelError, match="overflow"):
        big.evaluate({"x": 2**20, "b": True, "color": "c"})


def test_expression_size_and_nesting_limits() -> None:
    with pytest.raises(SpecError, match="longer than"):
        compile_expression("x + " * 400 + "x", SCOPE)
    with pytest.raises(SpecError, match="more than 300 nodes|cannot be parsed|nested"):
        compile_expression("+".join(["x"] * 200), SCOPE)
    with pytest.raises(SpecError):
        compile_expression("(" * 500 + "x" + ")" * 500, SCOPE)
    with pytest.raises(SpecError, match="nested deeper|cannot be parsed"):
        compile_expression("-" * 60 + "x", SCOPE)


def test_expressions_cannot_reach_python_builtins_or_globals() -> None:
    # A name that is not a model variable is never resolved against Python.
    for name in ("len", "__builtins__", "os", "globals"):
        with pytest.raises(SpecError, match="unknown variable"):
            compile_expression(name, SCOPE)


def test_spec_errors_do_not_leak_tracebacks_through_the_cli(capsys, spec_file) -> None:
    from prooftrail.cli import main

    path = spec_file(
        'schema_version: "1.0"\nmodel:\n  id: m\n  variables:\n'
        "    x: {type: integer, initial: 0}\n  transitions:\n"
        '    - {id: t, when: "x.__class__"}\n'
    )
    assert main(["validate", str(path)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "Traceback" not in err
