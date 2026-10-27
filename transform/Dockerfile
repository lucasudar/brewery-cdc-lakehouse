# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.9.5 AS uv

FROM python:3.12.16-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12.16-slim-bookworm
RUN useradd --create-home --uid 10001 app
COPY --from=build --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 \
    DBT_USE_COLORS=false DBT_SEND_ANONYMOUS_USAGE_STATS=false DO_NOT_TRACK=1
USER app
# Bake DuckDB extensions into the image: no downloads at runtime.
RUN python -c "import duckdb; [duckdb.sql(f'INSTALL {e}') for e in ('httpfs', 'iceberg', 'avro')]"
WORKDIR /app/dbt
COPY --chown=app:app dbt_project.yml profiles.yml ./
COPY --chown=app:app macros ./macros
COPY --chown=app:app models ./models
COPY --chown=app:app tests ./tests
ENV DBT_PROJECT_DIR=/app/dbt MART_OUTPUT_DIR=/app/dbt/target/marts DBT_DUCKDB_PATH=:memory:
RUN dbt parse --profiles-dir . --no-partial-parse >/dev/null
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --retries=6 \
  CMD python -c "import os,sys,time; sys.exit(time.time()-os.path.getmtime('/tmp/transformer.healthy')>60)"
CMD ["python", "-m", "transformer.runner"]
