# Raphael Reborn

A Discord bot that answers questions about the [Tensura: Reincarnated Minecraft mod](https://tensura.wiki.gg/) in the voice of Raphael, Lord of Wisdom. Any message starting with `Raphael, ` triggers a response.

Raphael retrieves relevant wiki pages from a vector store, optionally grounds its persona in actual anime dialogue, re-ranks the results with a cross-encoder, then generates a response with an LLM.

---

## Architecture

```
Discord message "Raphael, ..."
  │
  ▼
bot          — receives message, rate-limits via NATS KV, publishes to NATS stream
  │
  ▼  NATS JetStream "MESSAGES"
  │
  ▼
worker       — pulls event, queries Qdrant (wiki + subtitles), re-ranks results,
               calls LLM (Groq or Ollama), publishes reply to NATS stream "REPLIES"
  │
  ▼
bot          — receives reply, sends to Discord channel

Wiki sync:
  POST /sync/wiki → api service → scrape wiki → upsert to Qdrant
  POST /sync/addons → api service → scrape configured add-on/wiki/modpack pages → upsert to Qdrant
```

**Services (Docker Compose):**

| Service  | Role                                          |
| -------- | --------------------------------------------- |
| `bot`    | Discord gateway — thin event bridge           |
| `worker` | RAG pipeline + LLM inference                  |
| `api`    | FastAPI — sync triggers, health, stats         |
| `qdrant` | Vector store — `wiki` + `addons` + `subtitles` collections |
| `nats`   | JetStream — event streaming + KV state         |
| `ollama` | Local LLM (optional — enable with `--profile local-llm`) |

---

## Prerequisites

- Docker + Docker Compose v2
- A Discord bot token ([guide](https://discord.com/developers/docs/getting-started))
- A Groq API key ([console.groq.com](https://console.groq.com)) — or run Ollama locally
- *(Optional)* An OpenSubtitles.com account for automated subtitle download

---

## Quick Start

### 1. Clone and configure

```bash
git clone <repo-url>
cd Raphael-Reborn
cp .env.example .env
```

Edit `.env` — fill in at minimum:

```
DISCORD_TOKEN=your_discord_bot_token
GROQ_API_KEY=your_groq_api_key
QDRANT_API_KEY=<generate: python -c "import secrets; print(secrets.token_hex(32))">
API_KEYS=sync:<generate key>,read:<generate key>,admin:<generate key>
```

### 2. Start services

```bash
docker compose up -d --build
```

### 3. Seed the knowledge indexes

This runs once and rebuilds the searchable Qdrant collections from configured
sources: the main Tensura wiki, TRBeyond docs, the Beyond Worlds modpack
manifest, bundled Modrinth project metadata, and each bundled mod's `wiki_url`
up to `ADDON_MOD_WIKI_MAX_PAGES` pages.

```bash
docker compose run --rm api python scripts/seed_knowledge.py
```

For a faster external-source-only refresh after the main wiki is already indexed:

```bash
docker compose run --rm api python scripts/seed_addons.py
```

### 4. (Optional) Add subtitle persona data

See the [Subtitle Setup](#subtitle-setup) section below.

The bot works without subtitles — they improve Raphael's voice accuracy but are not required.

### 5. Verify everything is running

```bash
docker compose ps
curl http://localhost:8080/health
curl http://localhost:8080/stats -H "Authorization: Bearer <your_read_key>"
```

---

## Subtitle Setup

The `subtitles` Qdrant collection holds Raphael's actual anime dialogue. The worker uses these lines as tonal grounding when building responses — the LLM sees real examples of how Raphael speaks.

**Only Raphael's lines are indexed.** Every other character is stripped before the data reaches Qdrant.

### Automated download (recommended)

No account needed. The script uses [subliminal](https://subliminal.readthedocs.io/) with Podnapisi and TVsubtitles providers.

```bash
docker compose run --rm api python scripts/fetch_subtitles.py
docker compose run --rm api python scripts/seed_subtitles.py
```

`fetch_subtitles.py` searches for English subtitles across all Tensura seasons and saves them to `subtitles/`. It skips already-present files, so reruns are safe.

**Coverage note:** Podnapisi and TVsubtitles have broad coverage but are not exhaustive for anime. If an episode isn't found, add it manually. You can also uncomment the OpenSubtitles provider in `scripts/fetch_subtitles.py` if you get credentials later.

### Manual (if you already have subtitle files)

Drop `.srt`, `.ass`, or `.ssa` files into the `subtitles/` directory, then:

```bash
docker compose run --rm api python scripts/seed_subtitles.py
```

For reliable Raphael detection, use ASS/SSA files where the Style field is the character name (e.g. `Style=Raphael` or `Style=Great Sage`). SRT files will attempt to detect speakers from inline tags like `RAPHAEL: ...` or `[Raphael] ...`.

---

## Wiki Sync

The wiki index is seeded once at first deploy. Subsequent syncs are triggered via the API:

```bash
# Incremental — only pages changed since last sync (fast, use regularly)
curl -X POST http://localhost:8080/sync/wiki/incremental \
     -H "Authorization: Bearer <sync_key>"

# Full re-index — re-fetches everything (slow, use if wiki structure changed)
curl -X POST http://localhost:8080/sync/wiki \
     -H "Authorization: Bearer <sync_key>"

# Check job status
curl http://localhost:8080/sync/status/<job_id> \
     -H "Authorization: Bearer <read_key>"

# Re-index subtitles (after adding new subtitle files)
curl -X POST http://localhost:8080/sync/subtitles \
     -H "Authorization: Bearer <sync_key>"

# Re-index configured external add-on pages
curl -X POST http://localhost:8080/sync/addons \
     -H "Authorization: Bearer <sync_key>"
```

All sync endpoints return `202 Accepted` immediately with a `job_id`. Poll `/sync/status/<job_id>` for progress.

---

## API Reference

### Public endpoints

| Method | Path       | Description              |
| ------ | ---------- | ------------------------ |
| `GET`  | `/health`  | Liveness check           |
| `GET`  | `/ready`   | Readiness (checks Qdrant) |

### Authenticated endpoints

All require `Authorization: Bearer <key>`.

| Method | Path                         | Role    | Description                         |
| ------ | ---------------------------- | ------- | ----------------------------------- |
| `GET`  | `/stats`                     | `read`  | Collection sizes + index health     |
| `GET`  | `/sync/status/{job_id}`      | `read`  | Sync job status                     |
| `POST` | `/sync/wiki`                 | `sync`  | Full wiki re-index                  |
| `POST` | `/sync/wiki/incremental`     | `sync`  | Incremental wiki sync               |
| `POST` | `/sync/subtitles`            | `sync`  | Re-index subtitle collection        |

**Roles:** `sync` → trigger syncs. `read` → read stats/status. `admin` → all of the above.

---

## Local LLM

### LM Studio / OpenAI-Compatible

LM Studio exposes an OpenAI-compatible API. Use `LLM_PROVIDER=openai`; this sends
chat requests to `/v1/chat/completions`.

```bash
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://host.docker.internal:1234
OPENAI_MODEL=qwen/qwen3-8b
OPENAI_ENABLE_WEB_SEARCH=true
OPENAI_WEB_SEARCH_MAX_RESULTS=5
```

From Podman on Linux, use `host.containers.internal` instead of
`host.docker.internal`.

When `OPENAI_ENABLE_WEB_SEARCH=true`, the worker exposes a `web_search` tool to
tool-capable OpenAI-compatible models. The model can request web results for
current or external information, while normal Tensura wiki questions still use
the indexed vector stores first.

### Ollama

To run inference locally instead of using Groq:

```bash
# Start with Ollama profile
docker compose --profile local-llm up -d

# Pull a model (llama3.2 is a good starting point)
docker compose exec ollama ollama pull llama3.2

# Switch the worker to Ollama
# In .env:
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.2

docker compose restart worker
```

Any model supported by Ollama works. Larger models (e.g. `llama3.1:70b`) give better persona accuracy but require more VRAM.

---

## Environment Variables

| Variable                    | Required | Default                     | Description                                      |
| --------------------------- | -------- | --------------------------- | ------------------------------------------------ |
| `DISCORD_TOKEN`             | Yes      | —                           | Discord bot token                                |
| `QDRANT_API_KEY`            | Yes      | —                           | Qdrant auth key (used by worker, api, and Qdrant itself) |
| `API_KEYS`                  | Yes      | —                           | M2M keys: `"sync:key,read:key,admin:key"`        |
| `GROQ_API_KEY`              | Groq     | —                           | Groq API key (required when `LLM_PROVIDER=groq`) |
| `LLM_PROVIDER`              | No       | `groq`                      | `groq`, `ollama`, or `openai`                    |
| `OPENAI_BASE_URL`           | LM Studio| —                           | OpenAI-compatible local endpoint                 |
| `OPENAI_MODEL`              | LM Studio| —                           | Model name for OpenAI-compatible local endpoint  |
| `OPENAI_ENABLE_WEB_SEARCH`  | No       | `false`                     | Expose the worker's web search tool to OpenAI-compatible models |
| `OPENAI_WEB_SEARCH_MAX_RESULTS` | No    | `5`                         | Maximum web results returned to the model        |
| `OLLAMA_MODEL`              | No       | `llama3.2`                  | Model name for Ollama                            |
| `ADDON_SOURCE_URLS`         | No       | TRBeyond + Beyond Worlds    | External docs/modpack pages indexed into `addons` |
| `ADDON_MOD_WIKI_MAX_PAGES`  | No       | `8`                         | Max same-site wiki/doc pages to crawl per bundled Modrinth mod |
| `WIKI_HOST`                 | No       | `tensura.wiki.gg`           | Wiki hostname to scrape                          |
| `OPENSUBTITLES_API_KEY`     | Subtitles| —                           | OpenSubtitles app API key                        |
| `OPENSUBTITLES_USERNAME`    | Subtitles| —                           | OpenSubtitles username                           |
| `OPENSUBTITLES_PASSWORD`    | Subtitles| —                           | OpenSubtitles password                           |

Variables set automatically by `docker-compose.yml` (override only if running outside Docker):

| Variable          | Default                      |
| ----------------- | ---------------------------- |
| `NATS_URL`        | `nats://nats:4222`           |
| `QDRANT_URL`      | `http://qdrant:6333`         |
| `OLLAMA_BASE_URL` | `http://ollama:11434`        |

---

## Development

### Running outside Docker

```bash
python -m venv .venv && source .venv/bin/activate

# Install all deps (or install per-service extras individually)
pip install -e ".[bot,worker,api,dev]"

# Start infrastructure only
docker compose up -d qdrant nats

# Update .env with local URLs
NATS_URL=nats://localhost:4222
QDRANT_URL=http://localhost:6333

# Run services locally
python main_bot.py
python main_worker.py
python main_api.py
```

### After code changes

```bash
docker compose restart bot worker    # restart both services
docker compose logs -f worker        # tail logs
```

### Tests

```bash
docker compose run --rm worker pytest tests/ -v
```

---

## Project Layout

```
src/
  config.py              All config via pydantic-settings (single source of truth)
  auth/middleware.py     M2M Bearer-key validation for FastAPI
  messaging/streams.py   NATS stream + KV bucket definitions
  bot/                   Discord client, event handler, reply consumer, formatter
  worker/                NATS pull consumer + RAG→rerank→LLM pipeline
  api/                   FastAPI app + sync/health/stats routes
  llm/
    base.py              LLMProvider protocol
    router.py            Query expansion + persona wrapper
    prompts.py           Raphael system prompt + RAG prompt builder
    providers/groq.py    4-model fallback chain (llama-3.3-70b → qwen3-32b → ...)
    providers/ollama.py  Local LLM via Ollama REST API
  rag/
    embedder.py          Shared SentenceTransformer singleton (all-MiniLM-L6-v2)
    reranker.py          Cross-encoder reranker (ms-marco-MiniLM-L-6-v2)
    client.py            Shared Qdrant client + collection/index setup
    wiki/                Scraper, wikitext cleaner, chunker, indexer, retriever
    subtitles/           SRT/ASS/VTT loader, indexer (Raphael-only), retriever

docker/                  Per-service Dockerfiles
subtitles/               Drop subtitle files here (volume-mounted into api + worker)
scripts/
  seed_wiki.py           One-time full wiki scrape + index
  seed_subtitles.py      Index subtitle files from subtitles/ directory
  fetch_subtitles.py     Auto-download subtitles from OpenSubtitles
  discord_notify.py      Post changelogs to Discord
```
