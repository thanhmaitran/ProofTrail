"""Rendering explored state spaces as DOT, Mermaid or (optionally) PNG.

This module only *reads* an :class:`Exploration`; it never decides anything
about a property. A rendering is limited to ``max_nodes`` states so a huge
state space cannot produce an unreadable (or enormous) picture; a truncated
graph says so explicitly.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from prooftrail.errors import ProofTrailError
from prooftrail.exploration.bfs import Limits, explore
from prooftrail.model.system import TransitionSystem
from prooftrail.verification.results import Trace

DEFAULT_MAX_NODES = 200


@dataclass
class StateGraph:
    """A bounded snapshot of the reachable state graph."""

    labels: list[str]
    edges: list[tuple[int, str, int]]
    initial: list[int]
    deadlocks: list[int]
    highlight_nodes: set[int]
    highlight_edges: set[tuple[int, str, int]]
    truncated: bool
    total_states: int


def build_state_graph(
    system: TransitionSystem,
    *,
    max_nodes: int = DEFAULT_MAX_NODES,
    trace: Trace | None = None,
) -> StateGraph:
    """Explore ``system`` (at most ``max_nodes`` states) and snapshot the graph."""
    if max_nodes < 1:
        raise ValueError("max_nodes must be positive")
    exploration = explore(
        system,
        limits=Limits(max_states=max_nodes),
        store_edges=True,
        track_parents=False,
    )
    if exploration.error is not None:
        raise ProofTrailError(f"Cannot draw the model: {exploration.error}")
    assert exploration.edges is not None

    n = len(exploration.index)
    labels = [str(exploration.state(i)) for i in range(n)]
    edges: list[tuple[int, str, int]] = []
    deadlocks: list[int] = []
    for i in range(n):
        out = exploration.edges[i]
        if not out and system.is_deadlock(exploration.state(i)):
            deadlocks.append(i)
        edges.extend((i, action, j) for action, j in out)

    highlight_nodes: set[int] = set()
    highlight_edges: set[tuple[int, str, int]] = set()
    if trace is not None:
        ids = [exploration.index.lookup(step.state) for step in trace.steps]
        highlight_nodes = {i for i in ids if i is not None}
        for step, a, b in zip(trace.steps[1:], ids, ids[1:], strict=False):
            if a is not None and b is not None and step.action is not None:
                highlight_edges.add((a, step.action, b))

    return StateGraph(
        labels=labels,
        edges=edges,
        initial=list(exploration.initial),
        deadlocks=deadlocks,
        highlight_nodes=highlight_nodes,
        highlight_edges=highlight_edges,
        truncated=not exploration.complete,
        total_states=n,
    )


def _escape_dot(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def to_dot(graph: StateGraph, *, title: str = "state space") -> str:
    """Graphviz DOT source for ``graph``."""
    lines = [
        "digraph prooftrail {",
        "  rankdir=LR;",
        '  node [shape=box, style="rounded,filled", fillcolor="#e8f4f2", '
        'fontname="monospace", color="#0f766e"];',
        '  edge [fontname="monospace", color="#475569"];',
        f'  label="{_escape_dot(title)}'
        + (' (truncated)"' if graph.truncated else '"')
        + ";",
    ]
    initial = set(graph.initial)
    deadlocks = set(graph.deadlocks)
    for i, label in enumerate(graph.labels):
        attrs = [f'label="{_escape_dot(label)}"']
        if i in initial:
            attrs.append("penwidth=3")
        if i in deadlocks:
            attrs.append('fillcolor="#fde8e8"')
            attrs.append('color="#b91c1c"')
        if i in graph.highlight_nodes:
            attrs.append('fillcolor="#fecaca"')
            attrs.append('color="#b91c1c"')
        lines.append(f"  s{i} [{', '.join(attrs)}];")
    for src, action, dst in graph.edges:
        attrs = [f'label="{_escape_dot(action)}"']
        if (src, action, dst) in graph.highlight_edges:
            attrs += ['color="#b91c1c"', "penwidth=2.5"]
        lines.append(f"  s{src} -> s{dst} [{', '.join(attrs)}];")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _mermaid_text(text: str) -> str:
    return text.replace('"', "'").replace("<", "&lt;").replace(">", "&gt;")


def to_mermaid(graph: StateGraph) -> str:
    """Mermaid ``flowchart`` source for ``graph`` (renders on GitHub)."""
    lines = ["flowchart LR"]
    for i, label in enumerate(graph.labels):
        lines.append(f'  s{i}["{_mermaid_text(label)}"]')
    for src, action, dst in graph.edges:
        lines.append(f'  s{src} -->|"{_mermaid_text(action)}"| s{dst}')
    lines.append("  classDef initial stroke-width:4px")
    lines.append("  classDef bad fill:#fecaca,stroke:#b91c1c")
    if graph.initial:
        lines.append("  class " + ",".join(f"s{i}" for i in graph.initial) + " initial")
    bad = sorted(set(graph.deadlocks) | graph.highlight_nodes)
    if bad:
        lines.append("  class " + ",".join(f"s{i}" for i in bad) + " bad")
    if graph.truncated:
        lines.append("  %% truncated: not every reachable state is drawn")
    return "\n".join(lines) + "\n"


def render_png(
    graph: StateGraph,
    path: str | Path,
    *,
    title: str = "state space",
    highlight: Collection[int] = (),
) -> Path:
    """Draw ``graph`` to a PNG file (needs the optional ``visualization`` extra)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import networkx as nx
    except ImportError:
        raise ProofTrailError(
            "PNG rendering needs the optional dependencies: "
            'pip install "prooftrail-model-checker[visualization]"'
        ) from None

    g = nx.MultiDiGraph()
    g.add_nodes_from(range(len(graph.labels)))
    for src, action, dst in graph.edges:
        g.add_edge(src, dst, label=action)

    count = max(len(graph.labels), 1)
    side = min(4 + count**0.5 * 1.6, 24)
    fig, ax = plt.subplots(figsize=(side * 1.3, side))
    pos = (
        nx.spring_layout(g, seed=7, k=1.8 / count**0.5)
        if count > 2
        else nx.circular_layout(g)
    )

    bad = set(graph.deadlocks) | graph.highlight_nodes | set(highlight)
    colors = ["#fecaca" if i in bad else "#ccebe6" for i in g.nodes]
    widths = [3.0 if i in graph.initial else 1.0 for i in g.nodes]
    nx.draw_networkx_nodes(
        g,
        pos,
        ax=ax,
        node_color=colors,
        edgecolors="#0f766e",
        linewidths=widths,
        node_size=2600,
    )
    nx.draw_networkx_labels(
        g,
        pos,
        ax=ax,
        font_size=7,
        font_family="monospace",
        labels={i: graph.labels[i].replace(", ", "\n") for i in g.nodes},
    )
    edge_colors = [
        "#b91c1c" if (a, d["label"], b) in graph.highlight_edges else "#64748b"
        for a, b, d in g.edges(data=True)
    ]
    nx.draw_networkx_edges(
        g,
        pos,
        ax=ax,
        edge_color=edge_colors,
        arrows=True,
        arrowsize=14,
        node_size=2600,
        connectionstyle="arc3,rad=0.15",
    )
    if count <= 40:
        edge_labels = {(a, b): d["label"] for a, b, d in g.edges(data=True)}
        nx.draw_networkx_edge_labels(
            g, pos, edge_labels=edge_labels, ax=ax, font_size=6, rotate=False
        )
    ax.set_title(title + (" (truncated)" if graph.truncated else ""))
    ax.axis("off")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return target
