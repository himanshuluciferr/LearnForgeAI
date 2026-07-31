"""Live tests for requirement-agent. Opt in with `pytest -m live` — these call the model."""

import pytest

from backend.agents.requirement import extract_requirement
from backend.config.settings import get_settings
from backend.workflow.state import ExperienceLevel

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


async def test_infers_experience_from_context():
    request = await extract_requirement(
        "I already use Kubernetes daily but want to get properly good at operators"
    )

    assert request.is_learning_request
    assert "operator" in request.skill.lower()
    assert request.experience != ExperienceLevel.BEGINNER


async def test_off_topic_prompt_is_not_a_learning_request():
    """The guardrail that stops the workflow inventing a course from small talk."""
    request = await extract_requirement("what is the weather in Pune today")

    assert not request.is_learning_request
    assert request.skill == ""


async def test_language_is_an_iso_code_not_a_display_name():
    request = await extract_requirement("mujhe python sikhao")

    assert request.is_learning_request
    assert "python" in request.skill.lower()
    assert request.language == "hi"
