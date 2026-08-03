"""Entry point for the ranking skill: orders sources by how much we trust them."""

from __future__ import annotations

from backend.workflow.state import ResearchSource, ResourceKind

# Primary sources age better than commentary, so the kind alone decides the base score.
# Domain-authority and freshness signals belong here too, once we have them.
KIND_SCORES: dict[ResourceKind, float] = {
    ResourceKind.DOCS: 1.0,
    ResourceKind.MICROSOFT_LEARN: 0.9,
    ResourceKind.GITHUB: 0.75,
    ResourceKind.BLOG: 0.5,
    ResourceKind.VIDEO: 0.4,
}


def rank_sources(sources: list[ResearchSource]) -> list[ResearchSource]:
    """Scores deterministically and sorts best-first, overwriting whatever the model guessed."""
    for source in sources:
        source.rank_score = KIND_SCORES[source.kind]

    # Sorted is stable, so the model's ordering survives as the tie-break within a kind.
    return sorted(sources, key=lambda source: source.rank_score, reverse=True)
