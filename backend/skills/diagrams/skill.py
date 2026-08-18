"""Entry point for the diagram skill: turns a chapter's diagram data into Mermaid.

Pure and deterministic — no model, no I/O. The model supplies the nodes and the edges between
them; the syntax is written here, for the same reason the project folder tree is rendered from
a list of paths. Aligning arrows, quoting labels and keeping participant ids consistent is the
mechanical part, and one stray character turns the whole block into a parse error rather than a
diagram — a failure the learner sees and the model never does.
"""

from __future__ import annotations

from backend.workflow.state import ChapterDiagram, DiagramEdge, DiagramKind

# A diagram the eye cannot follow teaches less than the prose beside it, so this is a
# legibility cap in the same spirit as MAX_CHAPTERS.
MAX_NODES = 12
MAX_LABEL = 60
# Below this a left-to-right flow reads as a pipeline; above it, it runs off the page.
WIDE_LAYOUT_LIMIT = 4
INDENT = "    "


def clean(label: str) -> str:
    """Mermaid labels are single-line and quote-delimited, so anything that would end the
    label early is removed rather than escaped.

    `#` becomes its own entity code because Mermaid reads `#nnn;` as an escape sequence, and
    this writes courses about C# and F#. A pipe would close a flowchart edge label.
    """
    collapsed = " ".join(label.split()).replace('"', "'").replace("|", "/")
    return collapsed[:MAX_LABEL].strip().replace("#", "#35;")


def node_ids(nodes: list[str]) -> dict[str, str]:
    """Ids are ours, not the model's. Two nodes given the same label are one node — keeping
    both would draw an edge to whichever happened to be declared last."""
    ids: dict[str, str] = {}
    for node in nodes:
        cleaned = clean(node)
        if cleaned and cleaned not in ids and len(ids) < MAX_NODES:
            ids[cleaned] = f"N{len(ids)}"
    return ids


def usable_edges(edges: list[DiagramEdge], ids: dict[str, str]) -> list[DiagramEdge]:
    """Drops any edge naming a node that was never declared.

    Same filter as the orphaned practice items in the exporter: the alternative is a Mermaid
    node that springs into existence with a raw id for a name.
    """
    return [edge for edge in edges if clean(edge.source) in ids and clean(edge.target) in ids]


def render_flow(ids: dict[str, str], edges: list[DiagramEdge]) -> str:
    direction = "LR" if len(ids) <= WIDE_LAYOUT_LIMIT else "TD"
    lines = [f"flowchart {direction}"]
    lines += [f'{INDENT}{node_id}["{label}"]' for label, node_id in ids.items()]

    for edge in edges:
        source, target = ids[clean(edge.source)], ids[clean(edge.target)]
        label = clean(edge.label)
        arrow = f'-->|"{label}"|' if label else "-->"
        lines.append(f"{INDENT}{source} {arrow} {target}")

    return "\n".join(lines)


def render_sequence(ids: dict[str, str], edges: list[DiagramEdge]) -> str:
    lines = ["sequenceDiagram"]
    lines += [f"{INDENT}participant {node_id} as {label}" for label, node_id in ids.items()]

    for edge in edges:
        source, target = ids[clean(edge.source)], ids[clean(edge.target)]
        # Message text runs to the end of the line, so an empty one would swallow the arrow.
        lines.append(f"{INDENT}{source}->>{target}: {clean(edge.label) or 'calls'}")

    return "\n".join(lines)


def render_diagram(diagram: ChapterDiagram | None) -> str:
    """The fenced Mermaid block, or "" when there is nothing worth drawing.

    Nodes with no edges between them are a list, and a list is what the prose already does
    well. Returning "" lets the caller drop the figure rather than print an empty frame.
    """
    if diagram is None:
        return ""

    ids = node_ids(diagram.nodes)
    edges = usable_edges(diagram.edges, ids)
    if len(ids) < 2 or not edges:
        return ""

    body = (
        render_sequence(ids, edges)
        if diagram.kind is DiagramKind.SEQUENCE
        else render_flow(ids, edges)
    )
    return f"```mermaid\n{body}\n```"
