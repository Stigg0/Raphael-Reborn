FROM python:3.12-slim AS base

WORKDIR /app
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HF_HOME=/tmp/huggingface
ENV TRANSFORMERS_CACHE=/tmp/huggingface

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

COPY pyproject.toml .
RUN pip install --no-cache-dir ".[api]"

# Pre-download embedding model (used during wiki/subtitle indexing).
# Skip with --build-arg SKIP_MODEL_DOWNLOAD=1 when using EMBED_PROVIDER=api.
ARG SKIP_MODEL_DOWNLOAD=1
RUN if [ "$SKIP_MODEL_DOWNLOAD" = "0" ]; then \
      python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"; \
    fi

COPY src/config.py src/
COPY src/auth/ src/auth/
COPY src/messaging/ src/messaging/
COPY src/rag/ src/rag/
COPY src/api/ src/api/
COPY scripts/ scripts/
COPY main_api.py .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data/pages /app/data/addons /app/subtitles \
    && chown -R appuser:appuser /app /home/appuser
USER appuser

FROM base AS dev

FROM base AS prod
CMD ["python", "main_api.py"]
