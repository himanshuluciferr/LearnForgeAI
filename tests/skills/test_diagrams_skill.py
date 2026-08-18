"""Tests for the diagram skill: turning nodes and edges into Mermaid that actually parses.

The model supplies relationships; every character of syntax is produced here. So these tests
are about what happens when the data it supplies is wrong, which is the only way this skill
can fail a learner.
"""

from __future__ import annotations

from backend.skills.diagrams.skill import (
    MAX_NODES,
    clean,
    node_ids,
    render_diagram,
    usable_edges,
)
from backend.workflow.state import ChapterDiagram, DiagramEdge, DiagramKind


def edge(source: str, target: str, label: str = "") -> DiagramEdge:
    return DiagramEdge(source=source, target=target, label=label)


def diagram(
    nodes: list[str] | None = None,
    edges: list[DiagramEdge] | None = None,
    kind: DiagramKind = DiagramKind.FLOW,
) -> ChapterDiagram:
    return ChapterDiagram(
        kind=kind,
        title="How a request reaches the model",
        nodes=["Agent", "Model Client"] if nodes is None else nodes,
        edges=[edge("Agent", "Model Client", "sends prompt")] if edges is None else edges,
    )


# --- flow ---------------------------------------------------------------------------


def test_a_flow_declares_every_node_before_drawing_its_arrows():
    rendered = render_diagram(diagram())

    assert rendered.startswith("```mermaid\nflowchart ")
    assert rendered.endswith("\n```")
    assert 'N0["Agent"]' in rendered
    assert 'N1["Model Client"]' in rendered
    assert 'N0 -->|"sends prompt"| N1' in rendered


def test_an_arrow_with_nothing_to_say_is_drawn_bare():
    rendered = render_diagram(diagram(edges=[edge("Agent", "Model Client")]))

    assert "N0 --> N1" in rendered
    assert "|" not in rendered


def test_a_wide_diagram_turns_top_down_so_it_stays_on_the_page():
    nodes = [f"Step {index}" for index in range(6)]
    edges = [edge(f"Step {index}", f"Step {index + 1}") for index in range(5)]

    assert "flowchart TD" in render_diagram(diagram(nodes=nodes, edges=edges))
    assert "flowchart LR" in render_diagram(diagram())


# --- sequence -----------------------------------------------------------------------


def test_a_sequence_names_its_participants_and_keeps_the_message_on_one_line():
    rendered = render_diagram(diagram(kind=DiagramKind.SEQUENCE))

    assert "sequenceDiagram" in rendered
    assert "participant N0 as Agent" in rendered
    assert "N0->>N1: sends prompt" in rendered


def test_a_sequence_message_is_never_left_empty():
    """Message text runs to the end of the line, so an empty one would swallow the arrow."""
    rendered = render_diagram(
        diagram(edges=[edge("Agent", "Model Client")], kind=DiagramKind.SEQUENCE)
    )

    assert "N0->>N1: calls" in rendered


# --- the ways the model's data goes wrong -------------------------------------------


def test_an_edge_naming_a_node_that_was_never_declared_is_dropped():
    """Mermaid would invent a box named `N7`; the learner would see a node called nothing."""
    edges = [edge("Agent", "Model Client"), edge("Agent", "Middleware")]

    kept = usable_edges(edges, node_ids(["Agent", "Model Client"]))

    assert len(kept) == 1
    assert kept[0].target == "Model Client"


def test_a_quote_in_a_label_cannot_end_the_label_early():
    rendered = render_diagram(diagram(nodes=['The "run" call', "Agent"], edges=[edge('The "run" call', "Agent")]))

    assert "The 'run' call" in rendered
    # One opening and one closing quote per declared node, and none from the label itself.
    assert rendered.count('"') == 4


def test_a_label_written_over_several_lines_is_collapsed():
    assert clean("sends\n  the prompt") == "sends the prompt"


def test_a_hash_becomes_an_entity_because_mermaid_reads_it_as_an_escape():
    """This writes courses about C# and F#, so the case is the norm rather than the edge."""
    assert clean("C# client") == "C#35; client"


def test_a_pipe_cannot_close_a_flowchart_edge_label():
    rendered = render_diagram(
        diagram(edges=[edge("Agent", "Model Client", "sync|async")])
    )

    assert '-->|"sync/async"|' in rendered


def test_the_same_label_twice_is_one_node():
    assert node_ids(["Agent", "Agent"]) == {"Agent": "N0"}


def test_nodes_are_capped_so_the_diagram_stays_readable():
    ids = node_ids([f"Node {index}" for index in range(MAX_NODES + 5)])

    assert len(ids) == MAX_NODES


def test_a_blank_label_is_not_a_node():
    assert node_ids(["Agent", "   ", ""]) == {"Agent": "N0"}


# --- when there is nothing worth drawing --------------------------------------------


def test_no_diagram_renders_as_nothing():
    assert render_diagram(None) == ""


def test_nodes_with_no_arrows_between_them_are_a_list_not_a_diagram():
    assert render_diagram(diagram(edges=[])) == ""


def test_a_single_node_is_not_a_relationship():
    assert render_diagram(diagram(nodes=["Agent"], edges=[edge("Agent", "Agent")])) == ""


def test_a_diagram_whose_every_edge_is_dropped_renders_as_nothing():
    """Otherwise the learner gets an empty frame where a figure was promised."""
    assert render_diagram(diagram(edges=[edge("Ghost", "Phantom")])) == ""
