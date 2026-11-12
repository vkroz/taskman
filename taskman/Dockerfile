# Taskman server image. Build context: this directory (taskman/).
# All settings come from environment variables and mounted secret files.
# Default command: the server on 0.0.0.0:$PORT (8080 when PORT is not set).
# Other commands: pass them as arguments, for example `db upgrade`.

FROM python:3.12.16-slim AS build

RUN pip install --no-cache-dir uv==0.11.32

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so that a source-only change reuses this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra postgres --no-install-project --no-editable

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra postgres --no-editable


FROM python:3.12.16-slim

RUN groupadd --system --gid 10001 taskman \
    && useradd --system --uid 10001 --gid taskman --no-create-home taskman

COPY --from=build /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app
USER taskman
EXPOSE 8080

ENTRYPOINT ["taskman"]
CMD ["serve", "--host", "0.0.0.0"]
