# Taskman CLI

The `taskman` and `tm` commands share one Python implementation. See the [project README](../README.md) for installation, agent handoffs and the complete workflow.

```sh
uv sync --extra dev
uv run pytest
uv run taskman --help
uv run taskman help
```

Commands cover creation, nested planning, dependencies, execution status, shared context, attributed journals, verification and closure. All task mutations go through the CLI or the UI's shared backend. Closed tasks are immutable.

`TASKMAN_URL` and `TASKMAN_ACCESS_TOKEN` select the server. `taskman serve` is that server: gunicorn on `0.0.0.0:$PORT`, Postgres from `TASKMAN_DATABASE_URL`. Install the server extra with `uv sync --extra dev --extra serve`. The Docker setup in the [project README](../README.md) runs the app and Postgres.
