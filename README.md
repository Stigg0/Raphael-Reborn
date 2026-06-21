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
- OpenSSL for local Qdrant TLS certificate generation
- Podman or Docker for generating NATS bcrypt hashes with `nats-box`
- *(Optional)* A SubDL account for automated subtitle download

---

## Quick Start

### 1. Clone and configure

```bash
git clone <repo-url>
cd Raphael-Reborn
cp .env.example .env
```

Edit `.env`, then generate the required secrets and derived values.

Generate random keys:

```bash
python - <<'PY'
import secrets

for name in (
    "QDRANT_API_KEY",
    "API_SYNC_KEY",
    "API_READ_KEY",
    "API_ADMIN_KEY",
    "NATS_BOT_PASSWORD",
    "NATS_WORKER_PASSWORD",
    "NATS_API_PASSWORD",
    "NATS_MESSAGE_HMAC_KEY",
):
    size = 64 if name == "NATS_MESSAGE_HMAC_KEY" else 32
    print(f"{name}={secrets.token_hex(size)}")
PY
```

Put those values into `.env`:

```env
DISCORD_TOKEN=your_discord_bot_token
QDRANT_API_KEY=<generated QDRANT_API_KEY>
API_KEYS=sync:<generated API_SYNC_KEY>,read:<generated API_READ_KEY>,admin:<generated API_ADMIN_KEY>

NATS_BOT_PASSWORD=<generated NATS_BOT_PASSWORD>
NATS_WORKER_PASSWORD=<generated NATS_WORKER_PASSWORD>
NATS_API_PASSWORD=<generated NATS_API_PASSWORD>
NATS_MESSAGE_HMAC_KEY=<generated NATS_MESSAGE_HMAC_KEY>
```

Then generate the derived security material:

```bash
# Creates certs/qdrant/ca.pem, server.pem, and server-key.pem.
./scripts/generate_qdrant_tls.sh

# Reads NATS_*_PASSWORD from .env and writes the matching
# NATS_*_PASSWORD_BCRYPT values back into .env.
./scripts/generate_nats_bcrypt.sh
```

If you use Docker instead of Podman for helper containers:

```bash
CONTAINER_RUNTIME=docker ./scripts/generate_nats_bcrypt.sh
```

Finally configure your LLM provider:

```env
# Groq
LLM_PROVIDER=groq
GROQ_API_KEY=your_groq_api_key

# Or LM Studio / another OpenAI-compatible server
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://host.containers.internal:1234
OPENAI_MODEL=your-model-name
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

## Security Material

Several values in `.env` are generated locally and should not be committed.

### API and HMAC keys

Generate these with Python's `secrets` module:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Use separate values for:

| Variable | Used by | Purpose |
| -------- | ------- | ------- |
| `QDRANT_API_KEY` | Qdrant, API, worker | Authenticates Qdrant requests |
| `API_KEYS` | API clients | Bearer keys for `sync`, `read`, and `admin` API roles |
| `NATS_MESSAGE_HMAC_KEY` | bot, worker | Signs bot/worker NATS message envelopes |

`NATS_MESSAGE_HMAC_KEY` can be longer:

```bash
python -c "import secrets; print(secrets.token_hex(64))"
```

### NATS passwords and bcrypt hashes

Each NATS service user has two related values:

| Variable | Used by | Purpose |
| -------- | ------- | ------- |
| `NATS_BOT_PASSWORD` | bot client | Plain password the bot uses inside its `NATS_URL` |
| `NATS_BOT_PASSWORD_BCRYPT` | NATS server | Bcrypt hash stored in `nats.conf` |
| `NATS_WORKER_PASSWORD` | worker client | Plain password the worker uses inside its `NATS_URL` |
| `NATS_WORKER_PASSWORD_BCRYPT` | NATS server | Bcrypt hash stored in `nats.conf` |
| `NATS_API_PASSWORD` | API client | Plain password the API uses inside its `NATS_URL` |
| `NATS_API_PASSWORD_BCRYPT` | NATS server | Bcrypt hash stored in `nats.conf` |

Generate the plain passwords first:

```bash
python - <<'PY'
import secrets
for name in ("NATS_BOT_PASSWORD", "NATS_WORKER_PASSWORD", "NATS_API_PASSWORD"):
    print(f"{name}={secrets.token_hex(32)}")
PY
```

Put those plain password values into `.env`, then generate the matching bcrypt hashes:

```bash
./scripts/generate_nats_bcrypt.sh
```

The script reads the plain `NATS_*_PASSWORD` values from `.env`, uses the official
`nats-box` CLI to bcrypt-hash them, and writes `NATS_*_PASSWORD_BCRYPT` back into
`.env`. The NATS server receives only the bcrypt hash values; clients receive the
plain values so they can authenticate.

### Qdrant TLS certificates

Qdrant uses an API key and HTTPS internally. Generate local TLS files with:

```bash
./scripts/generate_qdrant_tls.sh
```

This creates ignored local files under `certs/qdrant/`:

| File | Purpose |
| ---- | ------- |
| `ca.pem` | CA certificate mounted into API/worker for verification |
| `ca-key.pem` | Local CA private key; keep secret |
| `server.pem` | Qdrant server certificate |
| `server-key.pem` | Qdrant server private key |

Compose mounts `server.pem` and `server-key.pem` into Qdrant, and mounts `ca.pem`
into API/worker. The app uses `QDRANT_TLS_CA_CERT` to verify Qdrant's certificate.

Regenerating these files changes the CA; restart Qdrant, API, and worker after
regeneration:

```bash
docker compose up -d --force-recreate qdrant api worker
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

**Coverage note:** Podnapisi and TVsubtitles have broad coverage but are not exhaustive for anime. If an episode isn't found, add it manually. If you use the SubDL-backed path, set `SUBDL_API_KEY` in `.env` first.

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

Start from `.env.example`. Values marked "generate" should be unique per deployment.

### Required secrets

| Variable | Required | How to get it | Description |
| -------- | -------- | ------------- | ----------- |
| `DISCORD_TOKEN` | Yes | Discord Developer Portal | Discord bot token |
| `QDRANT_API_KEY` | Yes | `python -c "import secrets; print(secrets.token_hex(32))"` | Qdrant API key used by Qdrant, API, and worker |
| `API_KEYS` | Yes | Generate three separate `secrets.token_hex(32)` values | Bearer keys as `sync:<key>,read:<key>,admin:<key>` |
| `NATS_MESSAGE_HMAC_KEY` | Yes | `python -c "import secrets; print(secrets.token_hex(64))"` | Signs bot/worker NATS payload envelopes |

### NATS auth

| Variable | Required | How to get it | Description |
| -------- | -------- | ------------- | ----------- |
| `NATS_BOT_USER` | Yes | Keep default or choose a username | Bot NATS username |
| `NATS_BOT_PASSWORD` | Yes | Generate with `secrets.token_hex(32)` | Bot client password |
| `NATS_BOT_PASSWORD_BCRYPT` | Yes | `./scripts/generate_nats_bcrypt.sh` | Bcrypt hash used by NATS server |
| `NATS_WORKER_USER` | Yes | Keep default or choose a username | Worker NATS username |
| `NATS_WORKER_PASSWORD` | Yes | Generate with `secrets.token_hex(32)` | Worker client password |
| `NATS_WORKER_PASSWORD_BCRYPT` | Yes | `./scripts/generate_nats_bcrypt.sh` | Bcrypt hash used by NATS server |
| `NATS_API_USER` | Yes | Keep default or choose a username | API NATS username |
| `NATS_API_PASSWORD` | Yes | Generate with `secrets.token_hex(32)` | API client password |
| `NATS_API_PASSWORD_BCRYPT` | Yes | `./scripts/generate_nats_bcrypt.sh` | Bcrypt hash used by NATS server |
| `NATS_URL` | Outside Compose | Compose sets per service | NATS connection URL if running services manually |

### Qdrant

| Variable | Required | Default | Description |
| -------- | -------- | ------- | ----------- |
| `QDRANT_URL` | Yes | Compose uses `https://qdrant:6333` | Qdrant URL. Use `https://localhost:6333` outside Compose |
| `QDRANT_TLS_CA_CERT` | Yes | `/app/certs/qdrant/ca.pem` in Compose | CA path used by API/worker to verify Qdrant TLS |
| `WIKI_COLLECTION` | No | `wiki` | Qdrant wiki collection |
| `SUBTITLES_COLLECTION` | No | `subtitles` | Qdrant subtitle collection |
| `ADDONS_COLLECTION` | No | `addons` | Qdrant add-on/mod metadata collection |

### LLM

| Variable | Required | Default | Description |
| -------- | -------- | ------- | ----------- |
| `LLM_PROVIDER` | No | `groq` | `groq`, `ollama`, or `openai` |
| `GROQ_API_KEY` | Groq only | — | Groq API key |
| `OPENAI_BASE_URL` | OpenAI-compatible only | — | LM Studio or other OpenAI-compatible endpoint |
| `OPENAI_MODEL` | OpenAI-compatible only | — | Model name |
| `OPENAI_ENABLE_WEB_SEARCH` | No | `false` | Enables the worker's DuckDuckGo-backed web search tool |
| `OPENAI_WEB_SEARCH_MAX_RESULTS` | No | `5` | Maximum web results sent to the model |
| `OLLAMA_BASE_URL` | Ollama only | `http://ollama:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | Ollama only | `llama3.2` | Ollama model name |

### Retrieval and indexing

| Variable | Required | Default | Description |
| -------- | -------- | ------- | ----------- |
| `WIKI_HOST` | No | `tensura.wiki.gg` | Main MediaWiki host |
| `ADDON_SOURCE_URLS` | No | TRBeyond + Beyond Worlds | Comma-separated external docs/modpack pages |
| `ADDON_MOD_WIKI_MAX_PAGES` | No | `8` | Max same-site wiki/doc pages per bundled Modrinth mod |
| `EMBED_PROVIDER` | No | `local` | `local` sentence-transformers or `api` embeddings |
| `EMBED_BASE_URL` | API embeddings only | — | `{base}/v1/embeddings` provider |
| `EMBED_MODEL` | No | `all-MiniLM-L6-v2` | Embedding model |
| `RERANK_MODEL` | No | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder reranker. Set empty to disable |
| `RERANK_TOP_K` | No | `5` | Number of chunks retained after reranking |
| `RELEVANCE_THRESHOLD` | No | `0.30` | Minimum vector relevance score |
| `K_FACTUAL` | No | `8` | Wiki factual retrieval count |
| `K_COMPARATIVE` | No | `10` | Wiki comparative/list retrieval count |
| `MAX_ENUM_CHARS` | No | `8000` | Context budget for enumeration questions |
| `MAX_COMPARATIVE_CHARS` | No | `16000` | Context budget for comparison questions |

### Bot behavior and subtitles

| Variable | Required | Default | Description |
| -------- | -------- | ------- | ----------- |
| `SHORT_RESPONSE_CHANNEL_ID` | No | empty | Discord channel ID that should receive short replies |
| `SHORT_RESPONSE_CHAR_LIMIT` | No | `0` | Character limit for that channel; `0` disables |
| `COOLDOWN_SECONDS` | No | `6` | Per-user bot cooldown |
| `HISTORY_TTL_SECONDS` | No | `600` | NATS KV TTL for conversation history |
| `MAX_HISTORY_PAIRS` | No | `3` | Recent Q/A pairs sent to the worker |
| `SUBDL_API_KEY` | Subtitles | — | Optional SubDL API key for subtitle download |

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
QDRANT_URL=https://localhost:6333
QDRANT_TLS_CA_CERT=./certs/qdrant/ca.pem

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
  fetch_subtitles.py     Auto-download subtitles
  generate_qdrant_tls.sh Generate local Qdrant TLS CA/server certs
  generate_nats_bcrypt.sh Generate NATS bcrypt hashes from .env passwords
  discord_notify.py      Post changelogs to Discord
```
