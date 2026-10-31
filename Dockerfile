# Taskman server. Listens on 0.0.0.0:$PORT and uses Postgres from
# TASKMAN_DATABASE_URL. No cloud SDK and no task files are baked in.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PORT=8080

WORKDIR /app

RUN groupadd --gid 10001 taskman \
    && useradd --uid 10001 --gid taskman --home-dir /home/taskman --create-home taskman

COPY taskman/pyproject.toml taskman/README.md /app/taskman/
COPY taskman/src /app/taskman/src

RUN pip install --no-cache-dir "/app/taskman[serve]"

USER taskman

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/health', timeout=3)"

CMD ["taskman", "serve"]
