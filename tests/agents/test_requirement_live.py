"""Live tests for requirement-agent. Opt in with `pytest -m live` — these call the model."""

import pytest

from backend.agents.requirement import extract_requirement
from backend.config.settings import get_settings
from backend.workflow.state import MissingRequirement, StatedExperience

pytestmark = [pytest.mark.live, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def require_endpoint():
    if not get_settings().foundry_project_endpoint:
        pytest.skip("FOUNDRY_PROJECT_ENDPOINT is not set")


async def test_extracts_skill_and_daily_minutes():
    request = await extract_requirement("Teach me Azure AI Search, 30 mins a day")

    assert request.is_learning_request
    assert "azure ai search" in request.skill.lower()
    assert request.daily_minutes == 30
    assert request.language == "en"
    assert request.missing_requirements == []


async def test_infers_experience_from_context():
    request = await extract_requirement(
        "I already use Kubernetes daily but want to get properly good at operators"
    )

    assert request.is_learning_request
    assert "operator" in request.skill.lower()
    assert request.experience != StatedExperience.BEGINNER
    assert request.experience != StatedExperience.UNKNOWN


async def test_a_raised_level_comes_with_the_words_that_justified_it():
    """Without evidence the field is an assumption wearing a schema. With it, an unsupported
    claim is visible instead of silent."""
    request = await extract_requirement(
        "I already use Kubernetes daily but want to get properly good at operators"
    )

    assert request.experience_evidence
    assert "kubernetes" in request.experience_evidence.lower()


async def test_an_unstated_level_stays_unknown_rather_than_defaulting_to_beginner():
    request = await extract_requirement("Teach me Azure AI Search")

    assert request.experience is StatedExperience.UNKNOWN
    assert request.experience_evidence is None


async def test_off_topic_prompt_is_not_a_learning_request():
    """The guardrail that stops the workflow inventing a course from small talk."""
    request = await extract_requirement("what is the weather in Pune today")

    assert not request.is_learning_request
    assert request.skill is None


async def test_language_is_an_iso_code_not_a_display_name():
    request = await extract_requirement("mujhe python sikhao")

    assert request.is_learning_request
    assert "python" in request.skill.lower()
    assert request.language == "hi"


# --- the no-guessing contract: a vendor, an ecosystem or a category is not a skill ---


@pytest.mark.parametrize(
    "prompt",
    [
        "Teach me Microsoft stuff",
        "I want to learn AI",
        "Teach me cloud",
        "Teach me databases",
    ],
    ids=["vendor", "field", "ecosystem", "category"],
)
async def test_a_request_too_broad_to_build_on_asks_rather_than_narrows(prompt):
    """The whole failure mode: a required field is a demand for an answer, so a model with
    no way to say 'too broad' returns the nearest specific product instead."""
    request = await extract_requirement(prompt)

    assert request.is_learning_request
    assert request.skill is None
    assert request.missing_requirements == [MissingRequirement.SKILL]


async def test_several_skills_offered_and_none_chosen_asks_which():
    request = await extract_requirement("Teach me React or Vue")

    assert request.is_learning_request
    assert request.skill is None
    assert request.alternatives == ["React", "Vue"]
    assert request.missing_requirements == [MissingRequirement.SKILL_CHOICE]


async def test_a_named_product_is_passed_on_verbatim_even_if_unfamiliar():
    """Node 1 captures the user's intent; it does not decide the truth about the subject.
    Establishing what Microsoft Agent Framework actually is belongs to the next node, and it
    cannot do that if this one quietly substitutes a product it has heard of.
    """
    request = await extract_requirement("Teach me Microsoft Agent Framework")

    assert request.is_learning_request
    assert request.skill == "Microsoft Agent Framework"
    assert request.missing_requirements == []


async def test_a_broad_vendor_name_alone_is_still_a_specific_enough_platform():
    """'Azure' names a platform you can build a course on; 'Microsoft stuff' does not."""
    request = await extract_requirement("I want to learn Azure")

    assert request.skill == "Azure"
    assert request.missing_requirements == []
