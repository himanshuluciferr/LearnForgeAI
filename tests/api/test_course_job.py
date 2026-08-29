import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace
from uuid import uuid4

from backend.api import course as course_api
from backend.main import app
from backend.models.course import StoredCourse
from backend.models.job import GenerationJob, JobStatus
from backend.schemas.course import CourseRequest
from backend.services.course_store import FileCourseStore
from backend.services.job_store import job_store
from backend.workflow import runner as runner_module
from backend.workflow.state import (
    STEP_WEIGHTS,
    Clarification,
    CourseState,
    Rejection,
    SubjectConfirmation,
)
from tests.conftest import as_user

client = TestClient(app)

PRIYA = as_user("priya@contoso.com")
MALLORY = as_user("mallory@contoso.com")
U1 = as_user("u1")


@pytest.fixture(autouse=True)
def no_live_workflow(monkeypatch):
    """Endpoint tests must not reach the model; the runner is covered separately below."""

    async def noop(job_id: str, request: CourseRequest, state=None) -> None:
        return None

    monkeypatch.setattr(course_api, "run_generation", noop)


def test_create_course_returns_job_and_progress_is_pollable():
    response = client.post(
        "/courses", json={"prompt": "Teach me Azure AI Search"}, headers=PRIYA
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    progress = client.get(f"/courses/{job_id}/progress", headers=PRIYA)
    assert progress.status_code == 200
    assert progress.json()["job_id"] == job_id


def test_the_status_url_handed_back_is_one_that_works():
    """The caller polls the url we give it, so it has to be one the token can open."""
    response = client.post(
        "/courses", json={"prompt": "Teach me Azure AI Search"}, headers=PRIYA
    )

    assert client.get(response.json()["status_url"], headers=PRIYA).status_code == 200


def test_progress_404_for_unknown_job():
    assert (
        client.get("/courses/does-not-exist/progress", headers=PRIYA).status_code == 404
    )


def test_course_404_for_unknown_course():
    assert client.get(f"/courses/{uuid4()}", headers=PRIYA).status_code == 404


def test_a_course_cannot_be_asked_for_on_someone_elses_behalf():
    """The body no longer carries user_id. If it were still read, this would file the job
    under Mallory's name while Priya's token paid for it."""
    job_id = client.post(
        "/courses",
        json={"prompt": "Teach me Azure AI Search", "user_id": "mallory@contoso.com"},
        headers=PRIYA,
    ).json()["job_id"]

    assert client.get(f"/courses/{job_id}/progress", headers=PRIYA).status_code == 200
    assert client.get(f"/courses/{job_id}/progress", headers=MALLORY).status_code == 404


# --- a course belongs to the learner who asked for it -------------------------------


@pytest.mark.parametrize(
    "path",
    ["/courses/{id}", "/courses/{id}/progress"],
)
def test_a_read_without_a_learner_is_refused(path):
    """The learner both routes to the partition and authorises. Left out, the stores fall back
    to a cross-partition query and hand any course to anyone holding its id."""
    assert client.get(path.format(id=uuid4())).status_code == 401


def test_confirming_someone_elses_job_is_refused():
    job_id = client.post(
        "/courses", json={"prompt": "Teach me Azure AI Search"}, headers=PRIYA
    ).json()["job_id"]

    stolen = client.post(f"/courses/{job_id}/confirm", headers=MALLORY)

    # Not 403: telling Mallory the job exists is itself the leak.
    assert stolen.status_code == 404


@pytest.mark.asyncio
async def test_reading_someone_elses_course_is_not_found(monkeypatch, tmp_path):
    store = FileCourseStore(tmp_path)
    monkeypatch.setattr(course_api, "course_store", store)
    course_id = str(uuid4())
    await store.save(
        StoredCourse(
            id=course_id,
            user_id="priya@contoso.com",
            job_id="j1",
            state=CourseState(job_id="j1", user_id="priya@contoso.com", prompt="p"),
        )
    )

    mine = client.get(f"/courses/{course_id}", headers=PRIYA)
    theirs = client.get(f"/courses/{course_id}", headers=MALLORY)

    assert mine.status_code == 200
    assert theirs.status_code == 404


@pytest.mark.asyncio
async def test_runner_saves_the_course_and_links_it_to_the_job(monkeypatch, tmp_path):
    class EmptyWorkflow:
        def run(self, state, stream=True):
            async def events():
                return
                yield

            return events()

    monkeypatch.setattr(runner_module, "build_workflow", EmptyWorkflow)
    monkeypatch.setattr(runner_module, "course_store", FileCourseStore(tmp_path))
    await job_store.create(GenerationJob(id="job-save", user_id="u1", prompt="p"))

    await runner_module.run_generation("job-save", CourseRequest(user_id="u1", prompt="teach me x"))

    job = await job_store.get("job-save")
    assert job.status == JobStatus.COMPLETED
    assert job.course_id is not None

    course = await FileCourseStore(tmp_path).get(job.course_id)
    assert course is not None
    assert course.job_id == "job-save"
    assert course.state.prompt == "teach me x"


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


def yielding(output):
    """A workflow that emits one terminal output and nothing else."""

    class OneOutput:
        def run(self, state, stream=True):
            async def events():
                yield SimpleNamespace(type="output", data=output)

            return events()

    return OneOutput


@pytest.mark.asyncio
async def test_an_unanswered_choice_is_not_recorded_as_a_failure(monkeypatch):
    """It is answerable, so it must not look like something that went wrong."""
    monkeypatch.setattr(
        runner_module,
        "build_workflow",
        yielding(Clarification(message="React or Vue?", options=["React", "Vue"])),
    )
    await job_store.create(GenerationJob(id="job-choice", user_id="u1", prompt="p"))

    await runner_module.run_generation(
        "job-choice", CourseRequest(user_id="u1", prompt="react or vue")
    )

    job = await job_store.get("job-choice")
    assert job.status == JobStatus.NEEDS_CHOICE
    assert job.error is None
    assert job.course_id is None


@pytest.mark.asyncio
async def test_the_options_reach_the_caller_as_a_list(monkeypatch):
    monkeypatch.setattr(
        runner_module,
        "build_workflow",
        yielding(Clarification(message="React or Vue?", options=["React", "Vue"])),
    )
    await job_store.create(GenerationJob(id="job-opts", user_id="u1", prompt="p"))

    await runner_module.run_generation(
        "job-opts", CourseRequest(user_id="u1", prompt="react or vue")
    )

    progress = client.get("/courses/job-opts/progress", headers=U1).json()
    assert progress["options"] == ["React", "Vue"]
    assert progress["detail"] == "React or Vue?"


@pytest.mark.asyncio
async def test_selecting_an_offered_choice_restarts_the_full_run():
    job_id = str(uuid4())
    await job_store.create(
        GenerationJob(
            id=job_id,
            user_id="u1",
            prompt="Teach me React or Vue, 20 minutes a day",
            status=JobStatus.NEEDS_CHOICE,
            options=["React", "Vue"],
        )
    )
    resumed: list[tuple[CourseRequest, CourseState | None]] = []

    async def capture(job: str, request: CourseRequest, state=None) -> None:
        resumed.append((request, state))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(course_api, "run_generation", capture)
        response = client.post(
            f"/courses/{job_id}/confirm", headers=U1, json={"choice": "React"}
        )

    assert response.status_code == 202
    assert len(resumed) == 1
    assert resumed[0][1] is None
    assert "20 minutes a day" in resumed[0][0].prompt
    assert resumed[0][0].prompt.endswith("The learner selected this subject: React")


@pytest.mark.asyncio
async def test_a_choice_must_be_supplied_and_must_be_one_of_the_options():
    job_id = str(uuid4())
    await job_store.create(
        GenerationJob(
            id=job_id,
            user_id="u1",
            prompt="Teach me React or Vue",
            status=JobStatus.NEEDS_CHOICE,
            options=["React", "Vue"],
        )
    )

    missing = client.post(f"/courses/{job_id}/confirm", headers=U1)
    unknown = client.post(
        f"/courses/{job_id}/confirm", headers=U1, json={"choice": "Angular"}
    )

    assert missing.status_code == 422
    assert unknown.status_code == 422


# --- the confirmation gate ---


CONFIRMATION = SubjectConfirmation(
    message="I'll build a course on Microsoft Agent Framework. Shall I start?",
    canonical_name="Microsoft Agent Framework",
    description="A framework for AI agents and multi-agent workflows.",
    source_urls=["https://learn.microsoft.com/agent-framework/"],
)


async def stop_at_confirmation(job_id: str, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runner_module, "build_workflow", yielding(CONFIRMATION))
    monkeypatch.setattr(runner_module, "course_store", FileCourseStore(tmp_path))
    monkeypatch.setattr(course_api, "course_store", FileCourseStore(tmp_path))
    await job_store.create(GenerationJob(id=job_id, user_id="u1", prompt="teach me MAF"))
    await runner_module.run_generation(
        job_id, CourseRequest(user_id="u1", prompt="teach me MAF")
    )


@pytest.mark.asyncio
async def test_an_identified_subject_waits_for_the_learner(monkeypatch, tmp_path):
    """Stopping here costs one round trip; being wrong costs the whole expensive half."""
    job_id = str(uuid4())

    await stop_at_confirmation(job_id, monkeypatch, tmp_path)

    job = await job_store.get(job_id)
    assert job.status == JobStatus.NEEDS_CONFIRMATION
    assert job.error is None and job.course_id is None


@pytest.mark.asyncio
async def test_the_card_gets_the_name_and_the_sources_as_data(monkeypatch, tmp_path):
    job_id = str(uuid4())

    await stop_at_confirmation(job_id, monkeypatch, tmp_path)

    progress = client.get(f"/courses/{job_id}/progress", headers=U1).json()
    assert progress["subject_name"] == "Microsoft Agent Framework"
    assert progress["subject_sources"] == ["https://learn.microsoft.com/agent-framework/"]


@pytest.mark.asyncio
async def test_confirming_starts_the_run_from_the_subject_the_learner_approved(
    monkeypatch, tmp_path
):
    """A suspended workflow would not survive the wait, so the approved run is a fresh one
    that replays the stored analysis rather than searching again."""
    job_id = str(uuid4())
    await stop_at_confirmation(job_id, monkeypatch, tmp_path)
    resumed: list[CourseState] = []

    async def capture(job: str, request: CourseRequest, state=None) -> None:
        resumed.append(state)

    monkeypatch.setattr(course_api, "run_generation", capture)

    response = client.post(f"/courses/{job_id}/confirm", headers=U1)

    assert response.status_code == 202
    assert len(resumed) == 1 and resumed[0].subject_confirmed is True


@pytest.mark.asyncio
async def test_a_job_that_was_never_asked_cannot_be_confirmed(monkeypatch, tmp_path):
    monkeypatch.setattr(course_api, "course_store", FileCourseStore(tmp_path))
    job_id = str(uuid4())
    await job_store.create(
        GenerationJob(id=job_id, user_id="u1", prompt="p", status=JobStatus.RUNNING)
    )

    assert client.post(f"/courses/{job_id}/confirm", headers=U1).status_code == 409


def test_confirming_an_unknown_job_is_a_404():
    assert client.post(f"/courses/{uuid4()}/confirm", headers=U1).status_code == 404


@pytest.mark.asyncio
async def test_an_off_topic_prompt_still_reports_no_options(monkeypatch):
    """Only a choice carries options; a rejection would make an empty picker."""
    monkeypatch.setattr(runner_module, "build_workflow", yielding(Rejection(message="nope")))
    await job_store.create(GenerationJob(id="job-rej", user_id="u1", prompt="p"))

    await runner_module.run_generation("job-rej", CourseRequest(user_id="u1", prompt="weather?"))

    job = await job_store.get("job-rej")
    assert job.status == JobStatus.REJECTED
    assert job.options == []


def test_step_weights_sum_to_100():
    assert sum(STEP_WEIGHTS.values()) == 100
