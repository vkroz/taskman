# Taskman CLI

Uniform recursive work-item management. A CLI for tracking epic/feature/task hierarchies stored as nested directories with name-encoded status, ID, and priority.

The Python CLI stores work items, tracks dependencies, and provides a local browser view. Integration with the development skills is still in progress.

## Install

**End user (no repo checkout)** — install straight from GitHub. `uv` fetches the package and, if needed, a suitable Python:

    uv tool install "git+https://github.com/AgentureHQ/taskman.git#subdirectory=taskman"

**Developer (from a clone)** — editable install against your working tree:

    uv tool install -e ./taskman

After install, `taskman --help` is on PATH and `taskman version` prints the version. If your shell can't find `taskman`, run `uv tool update-shell` and restart the shell so uv's tool directory is on `PATH`. (`uv` provisions Python 3.11+ automatically. `pipx install "git+https://github.com/AgentureHQ/taskman.git#subdirectory=taskman"` also works, but requires Python ≥3.11 already present.)

`tm` is a short alias for the same CLI: `tm list`, `tm ready`, and `tm ui` work just like their `taskman` equivalents.

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

The CLI supports work-item creation, lifecycle transitions, dependency queries, search, validation, worktree status, and a local browser view. The legacy Claude Code skills still need migration to this CLI before the full development workflow works on a fresh install.
