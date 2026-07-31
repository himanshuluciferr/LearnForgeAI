# LearnForge AI — Architecture

Source of truth for the design: what we are building, how the pieces fit, what every
agent and skill does, and why the folders are laid out this way.

> **Status legend used throughout:** ✅ built and verified live · 🚧 stub exists, not implemented · 📋 planned, no file yet

---

## 1. What we are building

An employee types this in Microsoft Teams:

```
@LearnForge Teach me Azure AI Search, 30 minutes a day
```

A few minutes later they get back a complete, quality-reviewed course: an outline,
written chapters, practice exercises, portfolio projects, quizzes, and interview
preparation. From then on they can ask follow-up questions and an AI mentor answers
them **grounded in that specific course**, not from generic knowledge.

### Why this is not just "call an LLM in a loop"

One giant prompt asking for a whole course produces shallow, repetitive output that
drifts off-topic by chapter three. The quality comes from splitting the job into
narrow steps, each with its own instructions and its own strict output shape, and
then putting a **quality gate** at the end that can send weak chapters back to be
rewritten. That is what the workflow graph in §4 is for.

### Core user journeys

| Journey | Trigger | What happens |
|---|---|---|
| Generate a course | `@LearnForge teach me <skill>` | Full graph runs, progress card updates live |
| Check progress | `@LearnForge progress` | Reads learner progress from Cosmos |
| Take a quiz | Adaptive Card button | Quiz card → answer → scored and stored |
| Ask the mentor | `@LearnForge ask <question>` | Mentor agent answers using that user's course |

---

## 2. High-level architecture

```mermaid
flowchart LR
    U[User in Teams] --> B[teams-bot<br/>Bot Framework]
    B -->|HTTP| API[FastAPI backend]

    subgraph Backend
        API --> JOB[Job store]
        API --> WF[Agent Framework<br/>Workflow]
        WF --> AG[Agents]
        AG --> SK[Skills / tools]
    end

    AG -->|chat completions| F[Microsoft Foundry<br/>gpt-5-mini]
    SK --> SEARCH[Azure AI Search]
    SK --> BLOB[Blob Storage]
    JOB --> COSMOS[Cosmos DB]

    style F fill:#0078d4,color:#fff
    style SEARCH fill:#0078d4,color:#fff
    style BLOB fill:#0078d4,color:#fff
    style COSMOS fill:#0078d4,color:#fff
```

### Hosting model — "Option A"

There are two ways to run agents with Microsoft Foundry. We chose the first:

| | **Option A — chosen** | Option B — hosted agents |
|---|---|---|
| Where agents run | In our FastAPI process | Inside Foundry |
| Foundry's role | Model provider only | Runtime + model |
| Orchestration | Our code, fully visible | Foundry-managed |
| Debugging | Local breakpoints work | Remote traces |
| Cost | Only tokens | Tokens + hosting |

**Why:** we own the orchestration logic and can debug it locally. Agent code stays
portable — if the mentor agent later needs to be a hosted Foundry agent (so Teams can
call it directly without waking our backend), moving just that one agent is a small change.

---

## 3. Request lifecycle

Course generation takes minutes, and HTTP requests time out. So generation is
**asynchronous**: the API accepts the job, returns immediately, and the client polls.

```mermaid
sequenceDiagram
    participant T as Teams
    participant A as FastAPI
    participant J as Job store
    participant W as Workflow

    T->>A: POST /courses {user_id, prompt}
    A->>J: create job (queued)
    A-->>T: 202 {job_id, status_url}
    A->>W: run in background

    loop each executor
        W->>W: agent fills its slice of CourseState
        W->>J: update step + percent
    end

    loop until terminal
        T->>A: GET /courses/{job_id}/progress
        A-->>T: {status, step, percent}
    end
```

### Job states

| Status | Meaning |
|---|---|
| `queued` | Accepted, not started |
| `running` | Graph in flight |
| `completed` | Course published |
| `failed` | Something broke — `error` field explains |
| `rejected` | Prompt wasn't a learning request. **Not an error**, so kept separate from `failed` |

`rejected` exists because "what's the weather in Pune?" is a perfectly valid thing for a
user to type at a bot. Treating it as a failure would produce a scary error message for
ordinary small talk.

---

## 4. The workflow graph

This is the heart of the system. Each node is an **executor**; each executor wraps one
agent (or one deterministic step).

```mermaid
flowchart TD
    START([prompt]) --> REQ[requirement]
    REQ -->|not a learning request| REJ([rejected])
    REQ --> SKILL[skill-analysis]
    SKILL --> RES[research]
    RES --> CUR[curriculum]
    CUR --> CH[chapter]
    CH --> PRAC[practice]
    PRAC --> PROJ[project]
    PROJ --> QUIZ[quiz]
    QUIZ --> INT[interview]
    INT --> REV{review}
    REV -->|score below 90 and revisions left| CH
    REV -->|passed| PUB[publisher]
    PUB --> DONE([course])

    style REJ fill:#f5f5f5
    style REV fill:#fff4ce
```

### Two things worth understanding

**The review loop.** `review-agent` scores the course out of 100. Below
`PASSING_REVIEW_SCORE = 90`, the named weak chapters go back to `chapter-agent`.
`MAX_REVISIONS = 2` caps this so a stubbornly low score can't spin forever. This single
loop is the main reason output quality is decent rather than "first draft".

**The rejection exit.** `requirement-agent` sets `is_learning_request: false` for
off-topic prompts. A conditional edge routes those to a terminal node that yields a
friendly message. Verified live: when a conditional edge's condition is false, the
message is simply dropped and the workflow **terminates cleanly** — it does not hang.

### State-as-message

Every executor receives the **same** `CourseState` object, fills in its own slice, and
forwards it:

```python
@handler
async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
    state.request = await extract_requirement(state.prompt)
    state.mark(WorkflowStep.REQUIREMENT)
    await ctx.send_message(state)
```

> ⚠️ **Verified: Agent Framework passes state by reference — there is no copy.**
> Serial execution is safe. But `practice`, `project`, `quiz` and `interview` are
> logically independent and tempting to run in parallel — doing so with a shared object
> would race. Parallelising requires per-branch state objects plus a merge step. Flagged,
> not yet designed.

### Progress reporting

Each step carries a weight; the weights sum to 100 (enforced by a test). Executor ids are
the `WorkflowStep` string values, so `event.executor_id` maps straight to a progress step.

| Step | Weight | Step | Weight |
|---|---|---|---|
| requirement | 5 | project | 10 |
| skill-analysis | 5 | quiz | 8 |
| research | 10 | interview | 6 |
| curriculum | 10 | review | 5 |
| chapter | 30 | publisher | 3 |
| practice | 8 | | |

`chapter` is 30 because writing full prose for every chapter dominates the runtime.

---

## 5. Agents

An **agent** is an LLM with a job description: a name, a system prompt, a strict output
schema, and optionally some tools. Ten agents form the graph, one (`publisher`) is
deterministic and needs no LLM, and one (`mentor`) lives outside the graph entirely.

| # | Agent | Reads from state | Writes to state | Status |
|---|---|---|---|---|
| 1 | `requirement-agent` | `prompt` | `request` | ✅ |
| 2 | `skill-analysis-agent` | `request` | `skill_analysis` | 🚧 |
| 3 | `research-agent` | `request`, `skill_analysis` | `research` | 🚧 |
| 4 | `curriculum-agent` | `research`, `skill_analysis` | `curriculum` | 🚧 |
| 5 | `chapter-agent` | `curriculum`, `research` | `chapters` | 🚧 |
| 6 | `practice-agent` | `chapters` | `practice` | 🚧 |
| 7 | `project-agent` | `curriculum`, `skill_analysis` | `projects` | 🚧 |
| 8 | `quiz-agent` | `chapters` | `quizzes` | 🚧 |
| 9 | `interview-agent` | `curriculum`, `skill_analysis` | `interview` | 🚧 |
| 10 | `review-agent` | everything | `review` | 🚧 |
| — | `publisher` (no LLM) | everything | `published` | 📋 |
| — | `mentor-agent` (outside graph) | a published course | — | 🚧 |

### 1. `requirement-agent` ✅

Turns a free-text Teams message into structure. **This is the only agent that sees raw
user input**, which makes it the security and sanity boundary for everything downstream.

Output — [`LearningRequest`](../backend/workflow/state.py):

| Field | Purpose |
|---|---|
| `is_learning_request` | The guardrail. Only required field |
| `skill` | One skill, e.g. `"Azure AI Search"` |
| `experience` | beginner / intermediate / advanced |
| `goal` | What they want to be able to *do* |
| `daily_minutes` | 5–480, drives course pacing |
| `language` | ISO 639-1 code — `"hi"`, not `"Hindi"` |

Two design points that turned out to matter a lot:

- **Only `is_learning_request` is required; everything else defaults.** The model is
  never forced to invent a skill for a prompt that has none. This is what stops
  "what's the weather" becoming a weather course.
- **Pydantic `Field(description=...)` is the real prompt.** The descriptions become the
  JSON schema the model sees. The `language='English'` bug was fixed purely by writing
  *"ISO 639-1 code… not the language name"* in a field description — no prompt text changed.

Verified live on four cases including Hindi input and an off-topic prompt.

### 2. `skill-analysis-agent` 🚧 — *next to build*

Sizes the topic before any content is written: category, true difficulty,
prerequisites, estimated hours, career paths. Downstream agents need this to pitch the
material correctly — a course on Kubernetes operators for someone who already uses
Kubernetes daily should not open with "what is a container".

It is also the first node that **reads a field an earlier agent wrote**, so it is where
the state-passing contract gets its real test.

### 3. `research-agent` 🚧

Gathers trusted sources — official docs, Microsoft Learn, GitHub, reputable blogs — using
the `research` and `ranking` skills. This is the **grounding** step: it exists so chapters
are written from real, citable material instead of the model's recollection. Reduces
hallucination more than any prompt tweak can.

### 4. `curriculum-agent` 🚧

Designs the chapter list: titles, ordering, learning objectives per chapter, paced against
`daily_minutes`. A separate planning pass beats letting the chapter writer improvise,
because a plan can be checked for coverage and progression before expensive prose is generated.

### 5. `chapter-agent` 🚧

The workhorse — writes each chapter's actual content: explanation, code samples, diagrams,
key points, exercises. Uses the `chapter_writer`, `code_generator` and `diagrams` skills.
Runs per chapter, and is the target the review loop sends work back to.

### 6. `practice-agent` 🚧

Turns chapters into active recall: coding questions, assignments, exercises, MCQs. Reading
a chapter feels like learning; being unable to answer a question about it proves you haven't.

### 7. `project-agent` 🚧

Portfolio projects at beginner / intermediate / advanced level, each with features, folder
structure, milestones and stretch goals. This is what makes a course show up on a CV.

### 8. `quiz-agent` 🚧

Chapter quizzes, weekly quizzes, and a final assessment — scored and stored, so progress is
measurable rather than self-reported.

### 9. `interview-agent` 🚧

Tiered interview questions with model answers, plus mock interviews. Closes the loop on the
actual business goal: employees learning a skill in order to be hired or promoted into it.

### 10. `review-agent` 🚧

The quality gate, and the most important agent after `requirement`. Scores the course and
returns `ReviewResult`:

```python
class ReviewResult(BaseModel):
    score: int
    issues: list[str]
    regenerate_chapters: list[int]   # which chapters to rewrite
```

`regenerate_chapters` is what makes the loop targeted — we rewrite the three weak chapters,
not all twelve.

### `publisher` 📋 — deterministic, no LLM

Renders the finished course to Markdown / PDF / DOCX via the `exporter` skill, uploads to
Blob Storage, returns URLs. **No model involved**, so it lives in
[`backend/workflow/executors.py`](../backend/workflow/executors.py) with the other
deterministic nodes rather than in `agents/`.

### `mentor-agent` 🚧 — outside the graph

Everything above runs **once**, to build a course. The mentor runs **forever after**,
answering questions grounded in that user's course via Azure AI Search. It is served by
`/mentor` endpoints, not by the workflow. This is the feature that turns a one-off
document into an ongoing relationship.

---

## 6. Skills

### Agent vs skill — the distinction

| | Agent | Skill |
|---|---|---|
| Is | An LLM with a job | A reusable capability |
| Has | Prompt + output schema | A function signature |
| Deterministic? | No | Often yes |
| Reused? | Owns one graph node | Called by several agents |

The rule: **if it can be done without a model, or is needed by more than one agent, it's a
skill.** Skills keep agents thin and make the deterministic parts unit-testable without
spending tokens.

| Skill | Used by | What it does | Status |
|---|---|---|---|
| `research` | research-agent | Fetches candidate sources | 🚧 |
| `ranking` | research-agent | Scores/orders sources by trust and relevance | 🚧 |
| `chapter_writer` | chapter-agent | Long-form chapter prose | 🚧 |
| `code_generator` | chapter, practice, project | Runnable, idiomatic code samples | 🚧 |
| `diagrams` | chapter-agent | Mermaid diagrams for concepts | 🚧 |
| `quiz_generator` | quiz, practice | Question sets with correct answers | 🚧 |
| `project_generator` | project-agent | Project scaffolds and milestones | 🚧 |
| `reviewer` | review-agent | Returns a quality score | 🚧 |
| `exporter` | publisher | Markdown → PDF / DOCX, upload | 🚧 |

`code_generator` is the clearest case for the split: chapters need examples, practice needs
starter code, projects need scaffolds. One skill, three callers.

---

## 7. Shared state contract

[`backend/workflow/state.py`](../backend/workflow/state.py) is the contract binding all
agents. **Changing a model here changes the prompts downstream**, because these schemas
*are* what the models see.

```python
class CourseState(BaseModel):
    job_id: str
    user_id: str
    prompt: str                                  # raw Teams text

    request: LearningRequest | None              # agent 1
    skill_analysis: SkillAnalysis | None         # agent 2
    research: list[ResearchSource]               # agent 3
    curriculum: Curriculum | None                # agent 4
    chapters: list[Chapter]                      # agent 5
    practice: list[PracticeItem]                 # agent 6
    projects: list[Project]                      # agent 7
    quizzes: list[Quiz]                          # agent 8
    interview: list[InterviewQuestion]           # agent 9
    review: ReviewResult | None                  # agent 10
    published: PublishedCourse | None            # publisher

    completed_steps: list[WorkflowStep]          # drives percent
    revision_count: int                          # caps the review loop
```

Two helpers carry real logic:

- `mark(step)` — records a finished step, **ignoring duplicates**. The review loop revisits
  `chapter`, so without this guard progress would creep past 100%.
- `should_regenerate` — `review.score < 90 and revision_count < 2`. The loop condition,
  expressed once, in the state rather than scattered across edges.

---

## 8. Folder structure

```
LearnForgeAI/
├── backend/                  FastAPI service — owns the workflow
│   ├── api/                  HTTP routers (thin: validate, delegate, return)
│   ├── agents/               One module per agent + its executor
│   ├── skills/               Reusable capabilities, one package each
│   ├── workflow/             Graph definition, shared state, runner
│   ├── prompts/              System prompts as .md files
│   ├── services/             Azure SDK wrappers
│   ├── schemas/              API request/response models
│   ├── models/               Persisted entities
│   ├── config/               Settings from env
│   └── utils/
├── teams-bot/                Bot Framework app — the only Teams-aware code
├── tests/                    Mirrors backend/
├── docker/                   One Dockerfile per deployable
├── docs/                     This file
└── generated_courses/        Local output during development
```

### Why each folder exists

**`backend/api/`** — Routers stay thin on purpose: validate input, kick off work, return.
No business logic, so the same logic can later be triggered by a timer or queue instead of HTTP.
`course.py` ✅ · `mentor.py` 🚧 · `quiz.py` 🚧 · `progress.py` 🚧

**`backend/agents/`** — One module per agent, each holding the agent factory *and* its
executor. Colocating them means everything about one graph node is in one file. Filenames
stay `snake_case`; Foundry agent `name=` values must be hyphenated (`requirement-agent`),
as Foundry rejects underscores.

**`backend/skills/`** — A package per skill rather than a flat module, so a skill can grow
its own templates, helpers and tests without cluttering others.

**`backend/workflow/`** — The orchestration layer:

| File | Role | Status |
|---|---|---|
| `state.py` | The shared contract — read this first | ✅ |
| `workflow.py` | Graph wiring: nodes, edges, conditions | ✅ |
| `runner.py` | Runs the graph, translates events into job updates | ✅ |
| `executors.py` | Deterministic non-agent nodes (rejection, publisher) | ✅ |
| `conditions.py` | Reusable edge predicates | 🚧 |

**`backend/prompts/`** — Prompts are `.md` files, not Python strings. They can be edited,
diffed and reviewed without touching code — a non-developer can improve a prompt in a PR.
`loader.py` caches reads, so **prompt edits need a process restart** to take effect.

**`backend/services/`** — All Azure SDK usage is confined here. Agents and skills never
import an Azure SDK directly, so swapping a provider or faking one in tests touches one file.
`foundry.py` ✅ · `job_store.py` ✅ (in-memory; Cosmos-backed later) · `cosmos.py`,
`blob_storage.py`, `ai_search.py` 🚧

**`backend/schemas/` vs `backend/models/`** — A deliberate split. `schemas/` is the public
API shape (what Teams sends and receives); `models/` is what we persist. Keeping them apart
means a database change doesn't silently alter the public API.

**`teams-bot/`** — Separately deployable. `backend_client.py` is the *only* place it talks
to the backend, and it never imports from `backend/` — the two communicate strictly over
HTTP, so they can scale and deploy independently.

**`tests/`** — Mirrors `backend/`. Split into two layers:

| Layer | Command | Speed | Proves |
|---|---|---|---|
| Offline (default) | `pytest -q` | ~4s | Wiring, schemas, conditions |
| Live (opt-in) | `pytest -m live` | ~45s, costs tokens | The *model* behaves |

`pytest.ini` sets `addopts = -m "not live"` so live tests never run by accident.

---

## 9. Azure services

| Service | Purpose | Status |
|---|---|---|
| Microsoft Foundry | `gpt-5-mini` — every agent's model | ✅ live |
| Azure AI Search | Grounding for research + mentor | 📋 |
| Cosmos DB | Users, courses, progress, scores, chat history | 📋 |
| Blob Storage | Published Markdown / PDF / DOCX | 📋 |
| Container Apps | Hosts backend + teams-bot | 📋 |

**Auth is keyless.** `DefaultAzureCredential` everywhere; no secrets in `.env`.

Two traps already hit and worth recording:

- **Endpoint.** `*.cognitiveservices.azure.com` is the Speech/Vision/Language endpoint.
  Model inference needs `*.services.ai.azure.com`, **and** `FoundryChatClient` wants the
  full *project* path: `.../api/projects/learnforge`.
- **Quota.** In this subscription every `Standard` SKU quota is 0 — capacity exists only in
  `GlobalStandard`. Deploying with the portal's default fails with a misleading quota error.

---

## 10. Key design decisions

| Decision | Rationale |
|---|---|
| Microsoft Agent Framework | First-party, GA, native Foundry integration. Not LangGraph |
| Option A hosting | We own orchestration; local debugging; agents stay portable |
| State-as-message | One object, in order — simple to reason about and to resume |
| Executor id == `WorkflowStep` | Progress mapping is a lookup, not a translation table |
| Prompts in `.md` | Editable and reviewable without code changes |
| Structured output everywhere | Downstream agents parse fields, never prose |
| Async jobs + polling | Generation outlives any HTTP timeout |
| `rejected` ≠ `failed` | Off-topic chat isn't an error |
| Two-layer tests | Fast feedback by default; model checks on demand |

---

## 11. Known gaps

Real, tracked, and deliberately deferred:

1. **Durability** — `BackgroundTasks` dies with the process, orphaning in-flight jobs.
   `WorkflowBuilder` accepts `checkpoint_storage=`; that's the path.
2. **Job store is in-memory** — restarts lose jobs. The interface is already isolated in
   `job_store.py`, so Cosmos slots in behind it.
3. **Parallelism is unsafe today** — state is shared by reference (see §4).
4. **Python version mismatch** — local venv is 3.13, Dockerfiles pin `python:3.12-slim`.
5. **Single `requirements.txt`** — should split per container once they deploy separately.
6. **No auth on the backend** — Teams identity is trusted but not yet verified.

---

## 12. Build order

| # | Milestone | Status |
|---|---|---|
| 1 | Foundry client + settings, live call | ✅ |
| 2 | `requirement-agent` + structured output | ✅ |
| 3 | One-node workflow, verified live | ✅ |
| 4 | HTTP → job → workflow → progress slice | ✅ |
| 5 | Rejection path for off-topic prompts | ✅ |
| 6 | Tests: offline + opt-in live layers | ✅ |
| 7 | **`skill-analysis-agent` — first agent-to-agent handoff** | ⬅️ next |
| 8 | `research-agent` + Azure AI Search grounding | 📋 |
| 9 | `curriculum` → `chapter` — the content core | 📋 |
| 10 | `practice`, `project`, `quiz`, `interview` | 📋 |
| 11 | `review-agent` + the regeneration loop | 📋 |
| 12 | `publisher` + Blob Storage export | 📋 |
| 13 | Cosmos-backed jobs and progress | 📋 |
| 14 | Teams bot + Adaptive Cards | 📋 |
| 15 | Mentor agent | 📋 |
| 16 | Deploy to Container Apps | 📋 |

The order is deliberate: each milestone is runnable end-to-end, so there is always
something to test rather than a large half-built graph.
