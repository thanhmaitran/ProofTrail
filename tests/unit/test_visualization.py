"""State-space rendering (DOT, Mermaid, PNG)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prooftrail.errors import ProofTrailError
from prooftrail.model.parser import load_specification
from prooftrail.verification.engine import verify
from prooftrail.visualization import build_state_graph, render_png, to_dot, to_mermaid


@pytest.fixture()
def unsafe(examples_dir: Path):
    spec = load_specification(examples_dir / "counter" / "unsafe_counter.yaml")
    trace = verify(spec.system, spec.properties).results[0].counterexample
    return spec, trace


def test_graph_snapshot_of_the_counter(unsafe) -> None:
    spec, _ = unsafe
    graph = build_state_graph(spec.system)
    assert graph.total_states == 5 and not graph.truncated
    assert len(graph.edges) == 4 and graph.initial == [0]
    assert graph.deadlocks == [4]  # counter=4 has no enabled transition (4 <= 3 fails)


def test_counterexample_is_highlighted(unsafe) -> None:
    spec, trace = unsafe
    graph = build_state_graph(spec.system, trace=trace)
    assert graph.highlight_nodes == {0, 1, 2, 3, 4}
    assert len(graph.highlight_edges) == 4


def test_truncation_is_reported_and_drawn_honestly(unsafe) -> None:
    spec, _ = unsafe
    graph = build_state_graph(spec.system, max_nodes=2)
    assert graph.truncated and graph.total_states == 2
    assert "(truncated)" in to_dot(graph)
    assert "truncated" in to_mermaid(graph)


def test_dot_output(unsafe) -> None:
    spec, trace = unsafe
    dot = to_dot(build_state_graph(spec.system, trace=trace), title='a "quoted" title')
    assert dot.startswith("digraph prooftrail {") and dot.rstrip().endswith("}")
    assert 's0 -> s1 [label="increment"' in dot
    assert 'a \\"quoted\\" title' in dot
    assert "penwidth=3" in dot  # initial state


def test_mermaid_output(unsafe) -> None:
    spec, _ = unsafe
    text = to_mermaid(build_state_graph(spec.system))
    assert text.startswith("flowchart LR")
    assert 's0["counter=0"]' in text
    assert 's0 -->|"increment"| s1' in text
    assert "class s0 initial" in text and "class s4 bad" in text


def test_mermaid_escapes_special_characters() -> None:
    from prooftrail.visualization.graph import StateGraph

    graph = StateGraph(['a "b" <c>'], [], [0], [], set(), set(), False, 1)
    assert (
        "&lt;c&gt;" in to_mermaid(graph)
        and '"b"' not in to_mermaid(graph).split("[")[1]
    )


def test_png_is_written(unsafe, tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    pytest.importorskip("networkx")
    spec, trace = unsafe
    graph = build_state_graph(spec.system, trace=trace)
    target = render_png(graph, tmp_path / "out" / "g.png", title="unsafe")
    assert target.read_bytes().startswith(b"\x89PNG")


def test_png_of_a_tiny_and_a_labelled_graph(tmp_path: Path, examples_dir: Path) -> None:
    pytest.importorskip("matplotlib")
    pytest.importorskip("networkx")
    spec = load_specification(examples_dir / "traffic_light" / "model.yaml")
    render_png(build_state_graph(spec.system), tmp_path / "t.png")
    assert (tmp_path / "t.png").stat().st_size > 1000


def test_invalid_node_limit(unsafe) -> None:
    spec, _ = unsafe
    with pytest.raises(ValueError):
        build_state_graph(spec.system, max_nodes=0)


def test_model_errors_surface_when_drawing(examples_dir: Path) -> None:
    spec = load_specification(examples_dir / "counter" / "domain_error.yaml")
    with pytest.raises(ProofTrailError, match="Cannot draw"):
        build_state_graph(spec.system)
