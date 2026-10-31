"""Install the shared Taskman plugin through host-specific CLI adapters."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import typer

from taskman.config import save_server_url


@dataclass(frozen=True)
class Platform:
    executable: str
    install_args: tuple[str, ...]

    def source_registered(self, source: str) -> bool:
        args = [self.executable, "plugin", "marketplace", "list", "--json"]
        result = run(args)
        try:
            data = json.loads(result)
            entries = data.get("marketplaces", []) if isinstance(data, dict) else data
            existing = next((entry for entry in entries if entry.get("name") == "taskman"), None)
            if existing is None:
                return False
            current = (existing.get("marketplaceSource") or {}).get("source") or existing.get("path") or existing.get("repo")
            if current != source:
                raise typer.BadParameter(
                    f"{self.executable} already has a taskman marketplace from {current!r}; "
                    "choose that source or remove that registration explicitly"
                )
            return True
        except (ValueError, TypeError, AttributeError) as exc:
            if isinstance(exc, typer.BadParameter):
                raise
            raise typer.BadParameter(f"cannot read {self.executable} marketplace list: {exc}") from exc


PLATFORMS = {
    "codex": Platform("codex", ("plugin", "add", "tm@taskman")),
    "claude-code": Platform("claude", ("plugin", "install", "tm@taskman", "--scope", "user")),
}


def run(args: list[str]) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise typer.BadParameter(f"could not run {args[0]}: {exc}") from exc
    if result.returncode:
        raise typer.BadParameter(f"{' '.join(args[:3])} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def _validate_source(source: str) -> str:
    local = Path(source).expanduser()
    if local.exists():
        local = local.resolve()
        manifest = local / ".claude-plugin/marketplace.json"
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data.get("name") != "taskman" or not any(p.get("name") == "tm" for p in data.get("plugins", [])):
                raise ValueError("expected the taskman marketplace with plugin tm")
        except (ValueError, OSError) as exc:
            raise typer.BadParameter(f"invalid local Taskman marketplace: {exc}") from exc
        return str(local)
    if source.startswith(("/", ".", "~")):
        raise typer.BadParameter(f"local source does not exist: {source}")
    if source.startswith("-"):
        raise typer.BadParameter("source must be a repository or directory")
    return source


def _register(name: str, source: str) -> None:
    adapter = PLATFORMS[name]
    registered = adapter.source_registered(source)
    if not registered:
        run([adapter.executable, "plugin", "marketplace", "add", source])
    if name == "claude-code" and registered:
        run([adapter.executable, "plugin", "marketplace", "update", "taskman"])
    run([adapter.executable, *adapter.install_args])
    if name == "claude-code":
        run([adapter.executable, "plugin", "update", "tm@taskman", "--scope", "user"])


def _skip_message(name: str, source: str) -> str:
    executable = PLATFORMS[name].executable
    return (
        f"Skipped {name}: {executable} is not on PATH. "
        f"Install {executable}, then run: taskman install --platform {name} --source {source}"
    )


def install(
    platform: list[str] | None = typer.Option(None, "--platform", help="Target platform; repeat to install into multiple hosts."),
    source: str = typer.Option("vkroz/taskman", "--source", help="GitHub repository or local Taskman checkout."),
    server: str | None = typer.Option(None, "--server", help="Save this Taskman server URL for the CLI."),
    list_platforms: bool = typer.Option(False, "--list-platforms", help="Show supported installation adapters."),
) -> None:
    """Register the plugin for each host CLI that is installed.

    A missing host CLI is skipped with the command to finish later. The command
    exits 0 in that case, including when every requested host is missing.
    It exits 2 when a platform name or source is invalid, or when a host CLI
    is present but its plugin command fails.
    """
    if list_platforms:
        typer.echo("\n".join(PLATFORMS))
        return
    if server:
        if not server.startswith(("http://", "https://")):
            raise typer.BadParameter("server URL must start with http:// or https://")
        path = save_server_url(server)
        typer.echo(f"Saved Taskman server URL to {path}")
    if not platform:
        if server:
            return
        raise typer.BadParameter("choose at least one --platform; see --list-platforms")
    targets = list(dict.fromkeys(platform))
    for name in targets:
        if name not in PLATFORMS:
            raise typer.BadParameter(f"unsupported platform: {name}; available: {', '.join(PLATFORMS)}")
    source = _validate_source(source)
    present = [name for name in targets if shutil.which(PLATFORMS[name].executable)]
    missing = [name for name in targets if name not in present]
    failures: list[str] = []
    for name in present:
        try:
            _register(name, source)
        except typer.BadParameter as exc:
            failures.append(f"{name}: {exc}")
            continue
        typer.echo(f"Installed Taskman plugin for {name}.")
    for name in missing:
        typer.echo(_skip_message(name, source), err=True)
    if failures:
        for line in failures:
            typer.echo(line, err=True)
        raise typer.Exit(2)
    if present:
        typer.echo("Start a fresh host session to load the plugin.")
    elif missing:
        typer.echo("No plugins registered. Install a host CLI and re-run the command shown above.", err=True)
    typer.echo(
        "Point the CLI at the server with TASKMAN_URL and TASKMAN_ACCESS_TOKEN, "
        "or `taskman config --server URL`. In each git checkout, run `taskman hooks install` "
        "so commits are reported when the host has no plugin hook."
    )
