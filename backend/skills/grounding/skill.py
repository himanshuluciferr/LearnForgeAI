"""Finds import lines a chapter teaches that appear in none of its sources.

An import is an exact string, so this needs no model and no judgement: either the module path
is in the retrieved text or it is not. That is worth having because it is the failure that has
survived every other check — a course scored 81 and was asked for no rewrite while teaching
`from agent_framework.clients import FoundryChatClient`, which raises ModuleNotFoundError on
the learner's first line, and while importing the same class correctly one chapter earlier.

Deliberately NOT done by importing the path to see whether it resolves: the path comes from
model output, and importing it would run that package's __init__.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterator

from backend.workflow.state import ResearchSource

FENCE = re.compile(r"```[\w+#-]*\n(.*?)```", re.DOTALL)
# Unanchored, because the instruction is as harmful in a comment as in code: one course told the
# reader "Replace shim with `from agent_framework.workflows import Workflow`", which no fenced
# import statement would have caught. English does not otherwise produce this shape.
# A parenthesised multi-line import yields no names and is skipped: a miss is safe here, a
# false accusation sends a sound chapter back to be rewritten.
FROM_IMPORT = re.compile(r"\bfrom[ \t]+([\w.]+)[ \t]+import[ \t]+([^\n#]+)")
PLAIN_IMPORT = re.compile(r"^[ \t]*import[ \t]+([\w.]+)", re.MULTILINE)

# Below this a package is mentioned rather than documented, and every path under it would look
# invented. Degraded rather than wrong.
MIN_SOURCE_IMPORTS = 3


@dataclass(frozen=True)
class Import:
    module: str
    name: str = ""


def names_in(clause: str) -> list[str]:
    return [
        bare
        for part in clause.replace("(", " ").replace(")", " ").split(",")
        if (bare := part.strip().split(" as ")[0].strip()) and bare != "*"
    ]


def imports_in(text: str) -> Iterator[Import]:
    for module, clause in FROM_IMPORT.findall(text):
        for name in names_in(clause):
            yield Import(module, name)
    for module in PLAIN_IMPORT.findall(text):
        yield Import(module)


def taught_imports(markdown: str) -> list[Import]:
    return list(imports_in(markdown))


def documented_packages(sources: list[ResearchSource]) -> set[str]:
    """Roots the sources import often enough to be judged against.

    Every such root, not the most common one: a corpus about Microsoft Agent Framework carries
    the AutoGen migration guide, and its `autogen_core` examples outnumber the framework's own,
    so picking a single winner named the wrong package on two of five stored runs.
    """
    roots = Counter(
        item.module.split(".")[0] for source in sources for item in imports_in(source.text)
    )
    return {root for root, count in roots.items() if count >= MIN_SOURCE_IMPORTS}


def module_holding(name: str, corpus: str, package: str) -> str:
    """Where the sources do import this name, so the fault can say what to write instead."""
    for module, clause in FROM_IMPORT.findall(corpus):
        if module.split(".")[0] == package and name in names_in(clause):
            return module
    return ""


def describe(item: Import, corpus: str, package: str) -> str:
    known = module_holding(item.name, corpus, package) if item.name else ""
    if known:
        return (
            f"`from {item.module} import {item.name}` — no source contains `{item.module}`. "
            f"The sources import {item.name} from `{known}`."
        )
    return f"`{item.module}` appears in no source. Do not teach an import you were not shown."


def unknown_imports(markdown: str, sources: list[ResearchSource]) -> list[str]:
    """Faults for every import under a documented package that the sources never show."""
    packages = documented_packages(sources)
    if not packages:
        return []
    corpus = "\n".join(source.text for source in sources)
    faults: dict[str, None] = {}
    for item in taught_imports(markdown):
        root = item.module.split(".")[0]
        if root not in packages or item.module in corpus:
            continue
        faults.setdefault(describe(item, corpus, root))
    return list(faults)
