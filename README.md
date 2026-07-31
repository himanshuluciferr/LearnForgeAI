# LearnForge AI

AI-powered personalized course generation, delivered inside Microsoft Teams.

A user asks `@LearnForge Teach me Azure AI Search` and the system researches, plans, writes,
reviews and publishes a complete course — then stays available as an AI mentor grounded
in that generated course.

## Architecture

```
Microsoft Teams
      |
Microsoft 365 Agent      (auth, adaptive cards)
      |
FastAPI Backend          (REST, persistence)
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
| `teams-bot/` | Teams interaction only — no AI logic |
| `backend/api/` | REST endpoints |
| `backend/agents/` | `ChatAgent` definitions — instructions and tool wiring |
| `backend/workflow/` | Orchestration only — decides *what* runs next |
| `backend/skills/` | Reusable capabilities exposed to agents as tools |
| `backend/prompts/` | Centralized prompt templates |
| `backend/services/` | Thin Azure wrappers — swappable and mockable |

## Workflow

```
requirement -> skill-analysis -> research -> curriculum -> chapter
   -> practice -> project -> quiz -> interview -> review -> publisher
```

`review` is the conditional edge: a quality score below threshold loops back for
regeneration instead of publishing. `mentor` runs on a separate post-generation path.

> Agent names must be alphanumeric with interior hyphens only — no underscores,
> max 63 characters. Foundry rejects anything else.

## Status

Scaffold only. No implementation, no Azure resources provisioned yet.

## Getting started

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn backend.main:app --reload
```
