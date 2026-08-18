"""Tests for the import check: an import is an exact string, so this needs no judgement.

The failure it exists to catch, measured across five stored runs: a course scored 81 and was
asked for no rewrite while teaching `from agent_framework.clients import FoundryChatClient`,
which raises ModuleNotFoundError on the learner's first line — and while importing the same
class from the correct module one chapter earlier.
"""

from __future__ import annotations

from backend.skills.grounding.skill import (
    MIN_SOURCE_IMPORTS,
    documented_packages,
    module_holding,
    taught_imports,
    unknown_imports,
)
from backend.workflow.state import ResearchSource, ResourceKind


def source(text: str, url: str = "https://x.example/a") -> ResearchSource:
    return ResearchSource(title="t", url=url, kind=ResourceKind.DOCS, text=text)


def documented(*modules: str) -> list[ResearchSource]:
    """A corpus importing each module often enough to be judged against."""
    lines = [f"from {module} import Thing{n}" for n, module in enumerate(modules)]
    return [source("\n".join(lines * MIN_SOURCE_IMPORTS))]


# --- reading the imports out ---------------------------------------------------------


def test_a_from_import_yields_one_entry_per_name():
    found = taught_imports("```python\nfrom pkg.sub import One, Two\n```")

    assert [(item.module, item.name) for item in found] == [("pkg.sub", "One"), ("pkg.sub", "Two")]


def test_an_alias_is_recorded_under_its_real_name():
    found = taught_imports("```python\nfrom pkg import Thing as T\n```")

    assert found[0].name == "Thing"


def test_a_star_import_names_nothing():
    found = taught_imports("```python\nfrom pkg import *\n```")

    assert [item.name for item in found] == []


def test_a_plain_import_is_read_too():
    found = taught_imports("```python\nimport pkg.sub\n```")

    assert found[0].module == "pkg.sub"


def test_an_import_inside_a_comment_is_read():
    """A course told the reader 'Replace shim with `from agent_framework.workflows import
    Workflow`'. No fenced import statement would have caught that, and the learner types it
    just the same."""
    found = taught_imports("```python\n# Replace shim with `from pkg.made_up import Thing`\n```")

    assert found[0].module == "pkg.made_up"


# --- which packages can be judged ----------------------------------------------------


def test_a_package_the_sources_use_is_judged():
    assert documented_packages(documented("pkg")) == {"pkg"}


def test_a_package_mentioned_once_is_not_judged():
    """Everything under it would look invented, so it would accuse a sound chapter."""
    assert documented_packages([source("from rare import Thing")]) == set()


def test_every_documented_package_is_judged_not_only_the_commonest():
    """A corpus about one framework carries a migration guide full of another's imports;
    picking a single winner named the wrong package on two of five stored runs."""
    corpus = documented("agent_framework") + documented("autogen_core", "autogen_core")

    assert documented_packages(corpus) == {"agent_framework", "autogen_core"}


def test_sources_with_no_code_leave_every_import_alone():
    prose = [source("Rebasing replays commits onto a new base." * 40)]

    assert unknown_imports("```python\nfrom anything import Thing\n```", prose) == []


# --- the fault itself ----------------------------------------------------------------


def test_an_import_the_sources_never_show_is_reported():
    faults = unknown_imports("```python\nfrom pkg.invented import Thing\n```", documented("pkg"))

    assert len(faults) == 1 and "pkg.invented" in faults[0]


def test_an_import_the_sources_do_show_is_left_alone():
    assert unknown_imports("```python\nfrom pkg import Thing0\n```", documented("pkg")) == []


def test_a_package_the_sources_never_mention_is_not_judged():
    """stdlib and third-party imports are legitimately absent from a corpus about one
    framework, so their absence says nothing."""
    code = "```python\nimport os\nfrom dataclasses import dataclass\n```"

    assert unknown_imports(code, documented("pkg")) == []


def test_the_fault_names_the_module_the_sources_actually_use():
    """Without it the rewrite knows the line is wrong and not what to write instead."""
    corpus = documented("pkg.right")

    faults = unknown_imports("```python\nfrom pkg.wrong import Thing0\n```", corpus)

    assert "pkg.right" in faults[0]


def test_the_same_wrong_import_in_two_topics_is_reported_once():
    code = "```python\nfrom pkg.invented import Thing\n```\n```python\nfrom pkg.invented import Thing\n```"

    assert len(unknown_imports(code, documented("pkg"))) == 1


def test_module_holding_finds_where_a_name_really_lives():
    assert module_holding("Thing0", "from pkg.deep import Thing0", "pkg") == "pkg.deep"


def test_module_holding_will_not_cross_packages():
    assert module_holding("Thing0", "from other.deep import Thing0", "pkg") == ""
