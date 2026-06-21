FROM python:3.12-slim AS base

WORKDIR /app
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir --upgrade pip

COPY pyproject.toml .
RUN pip install --no-cache-dir ".[bot]"

COPY src/config.py src/
COPY src/auth/ src/auth/
COPY src/messaging/ src/messaging/
COPY src/bot/ src/bot/
COPY main_bot.py .

FROM base AS dev
# dev stage: source is volume-mounted, nothing extra needed

FROM base AS prod
CMD ["python", "main_bot.py"]
