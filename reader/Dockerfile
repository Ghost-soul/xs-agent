# syntax=docker/dockerfile:1
FROM node:22.18.0-bookworm-slim AS frontend
WORKDIR /build/frontend
RUN corepack enable && corepack prepare pnpm@10.13.1 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN --mount=type=cache,id=xs-agent-pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --store-dir /pnpm/store
COPY frontend/tsconfig.app.json frontend/tsconfig.reader.json frontend/vite.reader.config.ts ./
COPY frontend/reader ./reader
COPY frontend/src/reader ./src/reader
RUN pnpm exec tsc -p tsconfig.reader.json && pnpm exec vite build --config vite.reader.config.ts

FROM python:3.12.10-slim-bookworm AS dependencies
COPY reader/requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

FROM python:3.12.10-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH PYTHONPATH=/app
WORKDIR /app
RUN groupadd --gid 10001 reader && useradd --uid 10001 --gid reader --no-create-home reader
COPY --from=dependencies /opt/venv /opt/venv
COPY reader/__init__.py reader/app.py reader/database.py reader/main.py ./reader/
COPY deploy/__init__.py deploy/web.py deploy/healthcheck.py ./deploy/
COPY --from=frontend /build/frontend/reader-dist ./web
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=15s --start-period=15s --retries=3 \
    CMD ["python", "-B", "/app/deploy/healthcheck.py"]
ENTRYPOINT ["python", "-B", "-m", "reader.main"]
