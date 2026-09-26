# syntax=docker/dockerfile:1
FROM node:22.18.0-bookworm-slim AS frontend
WORKDIR /build/frontend
RUN corepack enable && corepack prepare pnpm@10.13.1 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN --mount=type=cache,id=xs-agent-pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --store-dir /pnpm/store
COPY frontend/index.html frontend/tsconfig*.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN pnpm build

FROM python:3.12.10-slim-bookworm AS dependencies
ENV UV_LINK_MODE=copy
WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.3
COPY pyproject.toml uv.lock ./
COPY deploy/README.md ./README.md
RUN --mount=type=cache,id=xs-agent-uv,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra rag --no-install-project

FROM python:3.12.10-slim-bookworm AS application
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src:/app \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app
RUN groupadd --gid 10001 novel && useradd --uid 10001 --gid novel --no-create-home novel \
    && mkdir -p /app/data/content /app/data/credentials /app/data/tokenizers /app/logs \
    && chown -R 10001:10001 /app/data /app/logs
COPY --from=dependencies /app/.venv /app/.venv
COPY src/novel_writer ./src/novel_writer
COPY migrations ./migrations
COPY configs/genre-quality-cards/genres ./configs/genre-quality-cards/genres
COPY configs/genre-quality-cards/narrative ./configs/genre-quality-cards/narrative
COPY configs/prompt-templates/published.json ./configs/prompt-templates/published.json
COPY deploy/alembic.ini ./alembic.ini
COPY deploy/__init__.py deploy/entrypoint.py deploy/web.py deploy/healthcheck.py ./deploy/
COPY --from=frontend /build/frontend/dist ./web
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-B", "/app/deploy/healthcheck.py"]
ENTRYPOINT ["python", "-B", "/app/deploy/entrypoint.py"]
CMD ["serve"]
