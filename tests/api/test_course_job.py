import pytest
from fastapi.testclient import TestClient

from backend.api import course as course_api
from backend.main import app
from backend.models.job import GenerationJob, JobStatus
from backend.schemas.course import CourseRequest
from backend.services.job_store import job_store
from backend.workflow import runner as runner_module
from backend.workflow.state import STEP_WEIGHTS

client = TestClient(app)


@pytest.fixture(autouse=True)
def no_live_workflow(monkeypatch):
    """Endpoint tests must not reach the model; the runner is covered separately below."""

    async def noop(job_id: str, request: CourseRequest) -> None:
        return None

    monkeypatch.setattr(course_api, "run_generation", noop)


def test_create_course_returns_job_and_progress_is_pollable():
    response = client.post(
        "/courses",
        json={"user_id": "priya@contoso.com", "prompt": "Teach me Azure AI Search"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    progress = client.get(f"/courses/{job_id}/progress")
    assert progress.status_code == 200
    assert progress.json()["job_id"] == job_id


def test_progress_404_for_unknown_job():
    assert client.get("/courses/does-not-exist/progress").status_code == 404


@pytest.mark.asyncio
async def test_runner_records_failure_on_the_job(monkeypatch):
    def boom() -> None:
        raise RuntimeError("workflow exploded")

    monkeypatch.setattr(runner_module, "build_workflow", boom)
    await job_store.create(GenerationJob(id="job-fail", user_id="u1", prompt="p"))

    await runner_module.run_generation("job-fail", CourseRequest(user_id="u1", prompt="teach me x"))

    job = await job_store.get("job-fail")
    assert job.status == JobStatus.FAILED
    assert "workflow exploded" in job.error


def test_step_weights_sum_to_100():
    assert sum(STEP_WEIGHTS.values()) == 100
