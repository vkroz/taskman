# Taskman server. Listens on 0.0.0.0:$PORT and uses Postgres from
# TASKMAN_DATABASE_URL. No cloud SDK and no task files are baked in.

FROM python:3.12-slim

# uv installs the exact versions in taskman/uv.lock, the same set CI tests.
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/venv \
    PATH="/app/venv/bin:$PATH" \
    PORT=8080

WORKDIR /app/taskman

RUN groupadd --gid 10001 taskman \
    && useradd --uid 10001 --gid taskman --home-dir /home/taskman --create-home taskman

# Dependencies first so a source-only change reuses this layer.
COPY taskman/pyproject.toml taskman/uv.lock taskman/README.md ./
RUN uv sync --frozen --no-install-project --extra serve

COPY taskman/src ./src
RUN uv sync --frozen --no-editable --extra serve

USER taskman

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/ready', timeout=3)"

CMD ["taskman", "serve"]
