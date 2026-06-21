FROM python:3.12-slim AS base

WORKDIR /app
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

COPY pyproject.toml .
RUN pip install --no-cache-dir ".[worker]"

# Pre-download models so the container has no runtime network dependency.
# Set SKIP_MODEL_DOWNLOAD=1 at build time to skip (e.g. when using EMBED_PROVIDER=api).
ARG SKIP_MODEL_DOWNLOAD=1
RUN if [ "$SKIP_MODEL_DOWNLOAD" = "0" ]; then \
      python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" && \
      python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"; \
    fi

COPY src/config.py src/
COPY src/auth/ src/auth/
COPY src/messaging/ src/messaging/
COPY src/llm/ src/llm/
COPY src/rag/ src/rag/
COPY src/worker/ src/worker/
COPY main_worker.py .

FROM base AS dev

FROM base AS prod
CMD ["python", "main_worker.py"]
