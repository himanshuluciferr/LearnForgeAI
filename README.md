# LearnForge AI

AI-powered personalized course generation, delivered as a web app.

A learner asks for a subject and the system researches, plans, writes, reviews and publishes a
complete course — a book they can read chapter by chapter, take quizzes on, and question. The
mentor answers from that course, or says it does not cover the question.

## Architecture

```
React app                (library, reader, quiz, mentor)
      |
FastAPI Backend          (REST, auth, persistence — also serves the built app)
      |
Agent Framework Workflow (state + routing only)
      |
      +-- AI Agents  ->  Reusable Skills (tools)
      |
Azure Services           (OpenAI, AI Search, Foundry)
      |
Storage & Database       (Cosmos DB, Blob Storage)
```

Orchestration uses [Microsoft Agent Framework](https://learn.microsoft.com/agent-framework/overview/agent-framework-overview)
Workflows — graph-based orchestration where agents and deterministic functions are
executors connected by edges.

## Layer responsibilities

| Layer | Role |
| --- | --- |
| `frontend/` | React app — no AI logic |
| `backend/api/` | REST endpoints |
| `backend/agents/` | `ChatAgent` definitions — instructions and tool wiring |
| `backend/workflow/` | Orchestration only — decides *what* runs next |
| `backend/skills/` | Reusable capabilities exposed to agents as tools |
| `backend/prompts/` | Centralized prompt templates |
| `backend/services/` | Thin Azure wrappers — swappable and mockable |
| `teams_bot/` | Parked. Predates authentication and does not currently work. |

## Workflow

```
requirement -> subject-analysis -> research -> curriculum -> chapter
   -> review -> practice -> project -> quiz -> publisher
```

`review` is the conditional edge: a quality score below threshold loops back for
regeneration instead of publishing. `mentor` runs on a separate post-generation path.

> Agent names must be alphanumeric with interior hyphens only — no underscores,
> max 63 characters. Foundry rejects anything else.

## Getting started

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env

# The app is served by the API, so it has to be built first.
cd frontend; npm ci; npm run build; cd ..

uvicorn backend.main:app --reload
```

Then open <http://127.0.0.1:8000>.

For frontend work, `npm run dev` in `frontend/` serves on 5173 and proxies the API, so the
paths match production.

## Checks

```powershell
pytest -m "not live"        # offline suite
pytest -m live              # calls the real model; costs money
cd frontend; npm test
python scripts\e2e_smoke.py # generates a real course against a running server
```
