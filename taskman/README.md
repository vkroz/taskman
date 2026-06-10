# taskman

Uniform recursive work-item management. A CLI for tracking epic/feature/task hierarchies stored as nested directories with name-encoded status, ID, and priority.

This is a work-in-progress rewrite of the bash `taskman.sh` in `plugins/agn/scripts/`. See the parent repo's `tasks/epics/20260525_taskman_python.md` for the design and decomposition.

## Install

**End user (no repo checkout)** — install straight from GitHub. `uv` fetches the package and, if needed, a suitable Python:

    uv tool install "git+https://github.com/AgentureHQ/agenture-loop.git#subdirectory=taskman"

**Developer (from a clone)** — editable install against your working tree:

    uv tool install -e ./taskman

After install, `taskman --help` is on PATH and `taskman version` prints the version. If your shell can't find `taskman`, run `uv tool update-shell` and restart the shell so uv's tool directory is on `PATH`. (`uv` provisions Python 3.11+ automatically. `pipx install "git+https://github.com/AgentureHQ/agenture-loop.git#subdirectory=taskman"` also works, but requires Python ≥3.11 already present.)

## Shell completion

Enable tab-completion for subcommands and options. Run once in each shell you use:

    taskman --install-completion

Then restart the shell. Auto-detects zsh, bash, fish, and PowerShell.

## Develop

    cd taskman
    uv sync --extra dev
    uv run pytest

## Layout

    taskman/
      pyproject.toml
      src/taskman/
        cli.py              # typer entry point
        model/              # name grammar, ID allocator, YAML I/O, layout
        commands/           # subcommand implementations
      tests/                # pytest suite

## Status

Scaffolding only. Real commands land in subsequent tasks under feature `workitem_model_and_core`.
