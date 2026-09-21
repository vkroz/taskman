# Taskman CLI

The `taskman` and `tm` commands share one Python implementation. See the [project README](../README.md) for installation, agent handoffs and the complete workflow.

```sh
uv sync --extra dev
uv run pytest
uv run taskman --help
uv run taskman help
```

Commands cover creation, nested planning, dependencies, execution status, shared context, attributed journals, verification and closure. All task mutations go through the CLI or the UI's shared backend. Closed tasks are immutable.

`TASKMAN_TASKS_DIR` selects a shared task tree. Otherwise Taskman searches from the working directory toward the repository root. The local UI supports task creation and inspection; agents execute work through the CLI.
