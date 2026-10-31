# Networked Taskman. `taskman serve` listens on 0.0.0.0:$PORT (default 8080).
# Mount a volume at /data so the task tree survives container replacement.
# The image does not contain task data or an access token.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    TASKMAN_TASKS_DIR=/data/tasks

WORKDIR /app

RUN groupadd --system --gid 10001 taskman \
    && useradd --system --uid 10001 --gid taskman --home-dir /home/taskman --create-home taskman

COPY taskman/pyproject.toml taskman/README.md /app/taskman/
COPY taskman/src /app/taskman/src

RUN pip install --no-cache-dir "/app/taskman[serve]" \
    && mkdir -p /data/tasks \
    && chown -R taskman:taskman /data /home/taskman

USER taskman

EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/health', timeout=3)"

CMD ["taskman", "serve"]
