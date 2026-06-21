# CLAUDE.md — Raphael Reborn

## Project Overview

**Raphael Reborn** is a Discord bot themed as Raphael, Lord of Wisdom from the Tensura anime. It answers questions about the [Tensura Minecraft mod](https://tensura.wiki.gg/) by retrieving wiki data + anime subtitle context and responding in-character.

**Trigger:** Any Discord message starting with `Raphael, ` triggers a bot response.

---

## Tech Stack

| Layer            | Technology                                                                  |
| ---------------- | --------------------------------------------------------------------------- |
| Discord bot      | `discord.py >= 2.3.0`                                                       |
| Message queue    | NATS JetStream (streams + KV for rate limiting & history)                   |
| LLM inference    | Groq API (4-model chain) **or** Ollama (local, OpenAI-compatible)           |
| Embeddings       | `sentence-transformers` (`all-MiniLM-L6-v2`, local, no API)                 |
| Vector store     | Qdrant (networked, persistent, two collections: `wiki` + `subtitles`)       |
| Wiki scraping    | `mwclient` (MediaWiki API for `tensura.wiki.gg`)                            |
| Sync API         | FastAPI — M2M Bearer-key protected endpoints                                |
| Containerisation | Docker Compose (bot, worker, api, qdrant, nats, ollama profile)             |
| Python version   | 3.12 (in Docker)                                                            |

---

## Directory Structure

```
src/
  config.py           pydantic-settings — all config, validated at boot
  auth/
    middleware.py     FastAPI M2M Bearer-key validation
  messaging/
    streams.py        NATS stream + KV definitions shared by all services
  bot/
    client.py         Discord client + graceful shutdown
    events.py         on_message → NATS publish + pending-reply tracking
    replies.py        NATS reply consumer → resolves Discord futures
    formatter.py      Discord 2000-char chunking
  worker/
    consumer.py       NATS pull consumer loop
    pipeline.py       RAG → LLM → NATS reply publish
  api/
    app.py            FastAPI app factory
    routes/
      health.py       GET /health  GET /ready
      index.py        GET /stats
      sync.py         POST /sync/wiki  POST /sync/wiki/incremental  POST /sync/subtitles
  llm/
    base.py           LLMProvider protocol
    router.py         Wrapper with query expansion + persona
    prompts.py        Raphael system prompt + RAG prompt builder
    providers/
      groq.py         4-model fallback chain
      ollama.py       OpenAI-compatible local LLM
  rag/
    embedder.py       Shared SentenceTransformer singleton
    client.py         Shared Qdrant client + collection setup
    wiki/
      scraper.py      mwclient MediaWiki scraper + disk cache
      cleaner.py      Wikitext → clean text + chunk builder
      indexer.py      Qdrant upsert
      categories.py   Category/enumeration detection
      normalizer.py   Query normalisation + meta-question detection
      scoring.py      Dynamic text-contains scoring + blocking
      retriever.py    Main orchestrator — all retrieval strategies
    subtitles/
      loader.py       SRT/ASS/VTT parser + speaker detection
      indexer.py      Subtitle chunk + Qdrant upsert
      retriever.py    Persona query (Raphael lines) + lore query

docker/
  bot.Dockerfile
  worker.Dockerfile
  api.Dockerfile
subtitles/          Drop .srt / .ass / .vtt files here (volume-mounted)
scripts/
  seed_wiki.py      One-time wiki scrape + index
  seed_subtitles.py One-time subtitle index
  discord_notify.py Post changelog to Discord
  log_audit.py      Analyse conversations.log
main_bot.py         Bot container entrypoint
main_worker.py      Worker container entrypoint
main_api.py         API container entrypoint
docker-compose.yml
docker-compose.dev.yml
nats.conf
pyproject.toml
.env.example
```

---

## Environment Variables

```
DISCORD_TOKEN=          # Discord bot token
QDRANT_API_KEY=         # Qdrant auth key (also set in docker-compose for Qdrant itself)
GROQ_API_KEY=           # Groq API key (only when LLM_PROVIDER=groq)
API_KEYS=               # M2M keys: "sync:key1,read:key2,admin:key3"
LLM_PROVIDER=groq       # "groq", "ollama", or "openai" for LM Studio/OpenAI-compatible servers
WIKI_HOST=tensura.wiki.gg
# Set by docker-compose.yml (override only if running outside Docker):
NATS_URL=nats://nats:4222
QDRANT_URL=http://qdrant:6333
OLLAMA_BASE_URL=http://ollama:11434   # only when LLM_PROVIDER=ollama
OPENAI_BASE_URL=http://host.containers.internal:1234  # only when LLM_PROVIDER=openai
```

Copy `.env.example` → `.env` and fill values.
Generate M2M keys: `python -c "import secrets; print(secrets.token_hex(32))"`

---

## How to Run

### First-time setup

```bash
cp .env.example .env
# Fill in .env (DISCORD_TOKEN, GROQ_API_KEY, QDRANT_API_KEY, API_KEYS)

docker compose up -d --build

# Seed wiki index (run once — ~7 min for ~1300 pages)
docker compose run --rm api python scripts/seed_wiki.py

# Seed all searchable knowledge collections from configured sources.
# This includes the main wiki, TRBeyond docs, the Beyond Worlds modpack,
# bundled Modrinth project metadata, and bounded bundled-mod wiki_url crawls.
docker compose run --rm api python scripts/seed_knowledge.py

# Optional: refresh only subtitles after adding .srt/.ass files
docker compose run --rm api python scripts/seed_subtitles.py
```

### Daily usage

```bash
docker compose ps
docker compose logs -f bot
docker compose logs -f worker
docker compose restart bot worker    # after code changes
```

### Sync API

```bash
# Incremental wiki sync
curl -X POST http://localhost:8080/sync/wiki/incremental \
     -H "Authorization: Bearer <sync_key>"

# Check job status
curl http://localhost:8080/sync/status/<job_id> \
     -H "Authorization: Bearer <read_key>"

# Full wiki re-index
curl -X POST http://localhost:8080/sync/wiki \
     -H "Authorization: Bearer <sync_key>"

# Re-index subtitle files
curl -X POST http://localhost:8080/sync/subtitles \
     -H "Authorization: Bearer <sync_key>"
```

### Local LLM (Ollama)

```bash
docker compose --profile local-llm up -d
docker compose exec ollama ollama pull llama3.2
# Set LLM_PROVIDER=ollama + OLLAMA_MODEL=llama3.2 in .env, then:
docker compose restart worker
```

### Index verification

```bash
curl http://localhost:8080/stats -H "Authorization: Bearer <read_key>"
```

---

## Architecture

```
Discord "Raphael, ..."
  → bot/events.py on_message()
      ├── query clean + injection check
      ├── NATS KV rate-limit check (ratelimit bucket, TTL=6s)
      └── publish → NATS Stream "MESSAGES"
                         │
              worker/consumer.py (pull consumer)
                         │
              worker/pipeline.py:
                ├── NATS KV history fetch (history bucket, TTL=600s)
                ├── rag/wiki/retriever.py → Qdrant "wiki"
                ├── rag/addons/retriever.py → Qdrant "addons"
                ├── rag/subtitles/retriever.py → Qdrant "subtitles"
                ├── llm/router.py → GroqProvider or OllamaProvider
                └── publish → NATS Stream "REPLIES"
                         │
      bot/replies.py (push consumer)
          → resolves pending Future → Discord reply

Wiki sync (API-triggered):
  POST /sync/wiki → api/routes/sync.py
      → rag/wiki/scraper.py + rag/wiki/indexer.py → Qdrant
  POST /sync/addons → api/routes/sync.py
      → rag/addons/scraper.py + rag/addons/indexer.py → Qdrant
```

---

## Qdrant Collections

| Collection  | Contents              | Key payload fields                                                                              |
| ----------- | --------------------- | ----------------------------------------------------------------------------------------------- |
| `wiki`      | Wiki page chunks      | `text`, `page_title`, `section`, `chunk_type`, `page_category`, `page_path_segments`, `page_categories` |
| `addons`    | External add-on docs  | `text`, `project_title`, `section`, `url`, `source_type`                                        |
| `subtitles` | Anime dialogue chunks | `text`, `speaker`, `season`, `episode`, `is_raphael`                                           |

---

## M2M Authentication

`API_KEYS` format: `"sync:key1,read:key2,admin:key3"`

| Role    | Permissions                    |
| ------- | ------------------------------ |
| `sync`  | Trigger sync endpoints         |
| `read`  | Read stats and sync status     |
| `admin` | All of the above               |

Keys validated with `hmac.compare_digest` (constant-time, no timing attacks).

---

## Code Conventions

**Logging:** `logging.getLogger(__name__)` — never `print()` (except seed scripts)

**Config:** All tunable values in `src/config.py` via `Settings`. Never hardcode inline.

**Type hints:** All function signatures annotated with explicit return types.

**Paths:** `pathlib.Path` everywhere.

**File size:** Keep files under 800 lines, functions under 50 lines.

---

## Constants

| Constant              | Location                    | Value                     |
| --------------------- | --------------------------- | ------------------------- |
| `PRIMARY_MODEL`       | `llm/providers/groq.py`     | `llama-3.3-70b-versatile` |
| `FALLBACK_MODEL`      | `llm/providers/groq.py`     | `qwen/qwen3-32b`          |
| `MAX_TOKENS`          | `llm/router.py`             | `1024`                    |
| `TEMPERATURE`         | `llm/router.py`             | `0.1`                     |
| `K_FACTUAL`           | `config.py`                 | `8`                       |
| `K_COMPARATIVE`       | `config.py`                 | `10`                      |
| `RELEVANCE_THRESHOLD` | `config.py`                 | `0.30`                    |
| `CHUNK_MAX_TOKENS`    | `rag/wiki/cleaner.py`       | `400`                     |
| Embedding model       | `config.py`                 | `all-MiniLM-L6-v2`        |

---

## Testing

```bash
docker compose run --rm worker pytest tests/
```

Tests need updating for the new architecture. When adding tests: use pytest, target 80% coverage, write tests first (TDD).

---

## Bot Restart Policy

After any change that affects output:

```bash
docker compose restart worker bot
```

---

## Task Tracking

Tasks and ideas are tracked in **Linear** under the **Tensura** team, project **Raphael**.

**MANDATORY — no exceptions:**

- Every bug, idea, improvement, fix, or task gets a Linear issue immediately — before any code is written.
- When starting work, set status to **In Progress**. When finishing, mark **Done**.

**Milestone placement:**

| Milestone                    | What belongs here                                               |
| ---------------------------- | --------------------------------------------------------------- |
| Phase 0 — Setup Cleanup      | Tooling, CI, config, dependency hygiene, infra housekeeping     |
| Phase 1 — RAG Infrastructure | Core scraping, indexing, chunking, Qdrant wiring                |
| Phase 2 — Hardening          | Reliability, rate limits, error handling, async safety          |
| Phase 2 — Simple Recitation  | Bot responds correctly to basic factual questions               |
| Phase 2 — Answer Quality     | Retrieval quality bugs                                          |
| Phase 3 — Advanced Reasoning | Enumeration queries, comparisons, multi-entity reasoning        |
| Phase 4 — Chatbot Persona    | Persona polish, subtitle grounding, non-wiki questions          |

**Labels:** Apply type (`Bug`, `Feature`, `Improvement`) AND component (`Bot`, `LLM`, `RAG`, `Infrastructure`, `Testing`).

---

## Automated Pipeline

**Manual (after every `git push`):**

Compose a categorised changelog (Added/Updated/Fixed) and send via `post_discord()` in `scripts/discord_notify.py`. Posts to channel `1488606233807028275`.
