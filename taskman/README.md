# Taskman CLI

The `taskman` and `tm` commands share one Python implementation. See the [project README](../README.md) for installation, agent handoffs and the complete workflow.

```sh
uv sync --extra dev
uv run pytest
uv run taskman --help
uv run taskman help
```

Commands cover creation, nested planning, dependencies, execution status, shared context, attributed journals, verification and closure. All task mutations go through the CLI or the UI's shared backend. Closed tasks are immutable.

`TASKMAN_TASKS_DIR` selects a shared task tree. Otherwise Taskman searches from the working directory toward the repository root. The local UI supports creation, open-task editing, eligible draft deletion, metadata queries and completed-history consolidation; agents execute work through the CLI.

`taskman serve` exposes that UI on `0.0.0.0:$PORT` behind `TASKMAN_ACCESS_TOKEN`. Install it with the `serve` extra (`uv sync --extra dev --extra serve`). The Docker setup in the [project README](../README.md) runs that command and stores the task tree on a volume.
