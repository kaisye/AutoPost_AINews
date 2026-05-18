# AI News Agent - Portfolio Overview

## Project Summary

AI News Agent is a portfolio-grade Agentic AI project for automated social media intelligence and publishing. It discovers high-impact AI news, ranks articles, writes Vietnamese Facebook analysis posts, asks for Telegram approval, prevents duplicate posts, and optionally publishes to a Facebook Page.

The project demonstrates how to move beyond a simple chatbot and build an operational agent with workflow state, tools, memory, governance, and automation.

## Portfolio One-Liner

Built an end-to-end Agentic AI system that discovers high-impact AI news, ranks articles, writes Vietnamese Facebook analysis posts, routes drafts through Telegram approval, prevents duplicate publishing, and optionally posts to Facebook automatically.

## What This Project Demonstrates

- Agentic workflow design with LangGraph.
- Tool use across news sources, Telegram, and Facebook.
- LLM-based drafting and revision.
- Human-in-the-loop approval.
- Persistent domain memory with SQLite.
- Duplicate prevention and auditability.
- Local admin UI for configuration and scheduling.
- Production-minded configuration, tests, and secret hygiene.

## Core Agentic AI Concepts

### Goal

The agent has a clear business goal: create a high-quality Facebook post from current AI news with minimal manual work.

### State

The workflow carries structured state across nodes:

- Run ID
- Recent posts
- Candidate articles
- Enriched articles
- Ranked articles
- Selected articles
- Draft
- Approval result
- Facebook post ID
- Skip reason
- Revision count

### Tools

The agent uses external systems as tools:

- RSS feeds for baseline news discovery.
- Hacker News for engagement signals.
- Tavily and NewsAPI for optional broader search.
- NVIDIA NIM or OpenAI-compatible LLM APIs for writing.
- Telegram Bot API for human approval.
- Facebook Graph API for publishing.
- SQLite for memory and audit history.

### Memory

Memory is used for both behavior and governance:

- Article fingerprints reduce repeated discovery.
- Post history prevents duplicate publishing.
- Recent posts are injected into prompts.
- Approval feedback and publish IDs are stored for audit.

### Governance

The workflow includes explicit approval control:

- Telegram approval before publishing.
- `APPROVE`, `REJECT`, and `EDIT` command handling.
- Optional auto-approval after timeout.
- Audit logs for every run.

## System Architecture

```text
News Sources
  -> Collection
  -> Metadata Enrichment
  -> Impact Ranking
  -> Article Selection
  -> LLM Drafting
  -> Duplicate Guard
  -> Telegram Approval
  -> Optional Revision
  -> Optional Facebook Publishing
  -> Memory Persistence
```

## LangGraph Workflow Nodes

| Node | Responsibility |
| --- | --- |
| `load_memory` | Load recent posts for prompt memory and duplicate avoidance |
| `collect_news` | Collect candidate AI news from multiple sources |
| `enrich_articles` | Fetch article metadata and images |
| `rank_articles` | Score articles by recency, engagement, relevance, and novelty |
| `select_ranked_articles` | Select fresh articles after filtering posted URLs |
| `draft_post` | Generate a Facebook post with the LLM |
| `check_duplicate_post` | Skip drafts too similar to recent posts |
| `send_telegram` | Send draft to Telegram for review |
| `wait_approval` | Wait for approval, rejection, edit request, or timeout |
| `revise_post` | Revise content from human feedback |
| `publish_facebook` | Publish to Facebook Page when enabled |
| `persist_memory` | Store run history, feedback, and publish result |

## Scoring Strategy

The ranking system combines deterministic signals:

```text
final_score = 0.34 * recency + 0.30 * engagement + 0.24 * relevance + 0.12 * novelty
```

- `recency`: favors fresh articles within the configured lookback window.
- `engagement`: uses points, comments, search scores, or source popularity.
- `relevance`: checks for AI-related keywords and strategic topics.
- `novelty`: reduces priority for articles already seen in memory.

This keeps the LLM focused on writing and analysis while code handles deterministic ranking.

## Duplicate Prevention

Duplicate prevention is a key production feature:

- Canonical URL filtering removes articles that were already posted, even when tracking parameters differ.
- Content similarity compares new drafts against recent posts.
- Duplicate drafts are stored with `skipped_duplicate` and never sent to Telegram or Facebook.
- Recent posts are included in the prompt to reduce repeated angles.

## Human Approval Flow

```text
Draft
  -> Telegram Review
  -> APPROVE: publish or persist
  -> REJECT: store feedback and stop
  -> EDIT: revise and resend
  -> Timeout: auto-approve if configured
```

This is important for public-facing content because it reduces risk while preserving automation.

## Technical Stack

- Python
- LangGraph
- Pydantic Settings
- SQLite
- FastAPI
- Uvicorn
- APScheduler
- HTTPX
- BeautifulSoup
- NVIDIA NIM OpenAI-compatible API
- Telegram Bot API
- Facebook Graph API
- Pytest
- Ruff

## Key Engineering Decisions

### Workflow over free-form agent loops

The project uses a controlled graph instead of giving the LLM unlimited autonomy. This makes the system easier to debug, test, audit, and extend.

### Code-first ranking

Ranking is deterministic and transparent. The LLM is reserved for language generation, synthesis, and revision.

### Human-in-the-loop before publishing

Publishing to a social platform is a public side effect, so the workflow adds approval before that action.

### Memory as a first-class system component

Memory is used for quality, safety, and auditability. It is not treated as a cosmetic feature.

### Provider abstraction

The project can use NVIDIA NIM or OpenAI native APIs through configuration, making it easier to switch providers.

## Challenges and Solutions

### API configuration

Challenge: different LLM providers require different API keys and base URLs.

Solution: use `LLM_PROVIDER`, provider-specific validation, and OpenAI-compatible configuration.

### Scheduling

Challenge: saving a posting time is not enough if no process is running.

Solution: provide both a daemon mode and an admin UI with an embedded scheduler and next-run visibility.

### Duplicate posts

Challenge: news sources may return the same story with slightly different URLs.

Solution: canonical URL memory and post-content similarity checks.

### Governance

Challenge: fully automated publishing can create public mistakes.

Solution: Telegram approval, edit requests, rejection handling, timeout configuration, and audit logs.

### Image generation

Challenge: the default text model does not generate images.

Solution: use article metadata images for now. A future enhancement can add a separate image generation provider.

## Production Roadmap

### Phase 1: Stable local automation

- Admin UI.
- Scheduler.
- Telegram approval.
- Facebook publishing.
- Duplicate prevention.
- Core tests.

### Phase 2: Production hardening

- Dockerfile and Docker Compose.
- Persistent LangGraph checkpoint.
- Worker queue.
- Structured logging.
- Health checks.
- Secret manager integration.

### Phase 3: Content quality

- Fact-check node.
- LLM evaluator node.
- Brand voice memory.
- Embedding-based semantic duplicate detection.
- Topic diversification.

### Phase 4: Multi-channel publishing

- LinkedIn, X, Threads, and newsletter output.
- Channel-specific formatting.
- Calendar planning.
- Analytics feedback loop.
- Optional AI-generated image workflow.

## Resume Bullets

- Designed and implemented a LangGraph-based Agentic AI workflow for automated AI news discovery, ranking, drafting, approval, and publishing.
- Integrated NVIDIA NIM OpenAI-compatible LLMs with Telegram and Facebook APIs to build a human-governed publishing agent.
- Built persistent SQLite memory for article fingerprinting, post history, duplicate prevention, feedback, and auditability.
- Developed a FastAPI admin dashboard for scheduling, provider configuration, theme controls, manual runs, and post history monitoring.
- Added approval timeout handling, revision loops, duplicate guardrails, and tests for production-oriented reliability.

## Demo Script

1. Open the admin UI at `http://127.0.0.1:8787`.
2. Show model, schedule, approval timeout, Facebook setting, and article count.
3. Click Run Now.
4. Explain collection, enrichment, ranking, drafting, and duplicate checking.
5. Show the Telegram approval message.
6. Reply with `EDIT:` to demonstrate revision or `APPROVE` to continue.
7. Show recent memory and explain duplicate prevention.
8. Explain how the daemon/UI scheduler keeps the system automated.

## Interview Talking Points

If asked why LangGraph was used:

- The workflow has state, branching, revision loops, approval gates, and memory. LangGraph makes these explicit and testable.

If asked how this differs from a normal automation script:

- It combines LLM reasoning, tool use, conditional routing, memory, human governance, scheduling, and audit logs.

If asked how hallucination risk is reduced:

- Source articles are passed into the prompt, publishing requires approval, and future fact-check nodes can verify claims before posting.

If asked how it can scale:

- Split UI and worker processes, add a queue, use persistent checkpoints, move SQLite to a production database, add observability, and manage secrets centrally.

## Portfolio Message

This project shows the ability to build more than a chatbot. It demonstrates an operational Agentic AI workflow that can collect information, reason over it, act through tools, remember prior runs, involve humans when needed, and run automatically with safeguards.
