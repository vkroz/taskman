"""Install the shared Taskman plugin through host-specific CLI adapters."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import typer


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
                raise typer.BadParameter(f"{self.executable} already has a taskman marketplace from {current!r}; choose that source or remove that registration explicitly")
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


def install(
    platform: list[str] | None = typer.Option(None, "--platform", help="Target platform; repeat to install into multiple hosts."),
    source: str = typer.Option("vkroz/taskman", "--source", help="GitHub repository or local Taskman checkout."),
    list_platforms: bool = typer.Option(False, "--list-platforms", help="Show supported installation adapters."),
) -> None:
    """Install the plugin into selected platforms; the Taskman CLI is shared."""
    if list_platforms:
        typer.echo("\n".join(PLATFORMS))
        return
    if not platform:
        raise typer.BadParameter("choose at least one --platform; see --list-platforms")
    targets = list(dict.fromkeys(platform))
    for name in targets:
        if name not in PLATFORMS:
            raise typer.BadParameter(f"unsupported platform: {name}; available: {', '.join(PLATFORMS)}")
        if not shutil.which(PLATFORMS[name].executable):
            raise typer.BadParameter(f"install {PLATFORMS[name].executable} before installing its Taskman plugin")
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
        source = str(local)
    elif source.startswith(("/", ".", "~")):
        raise typer.BadParameter(f"local source does not exist: {source}")
    elif source.startswith("-"):
        raise typer.BadParameter("source must be a repository or directory")

    # Resolve all target registrations before changing any host.
    registered = {name: PLATFORMS[name].source_registered(source) for name in targets}
    for name in targets:
        adapter = PLATFORMS[name]
        if not registered[name]:
            run([adapter.executable, "plugin", "marketplace", "add", source])
        # Local sources are read on install. Explicit refresh handles host caches.
        if name == "claude-code" and registered[name]:
            run([adapter.executable, "plugin", "marketplace", "update", "taskman"])
        run([adapter.executable, *adapter.install_args])
        if name == "claude-code":
            # install is a no-op for an existing plugin; update is required.
            run([adapter.executable, "plugin", "update", "tm@taskman", "--scope", "user"])
        typer.echo(f"Installed Taskman plugin for {name}.")
    typer.echo("Use a fresh session to load the plugin. Taskman keeps work items in its SQLite or Postgres database; run `taskman db upgrade` once.")
