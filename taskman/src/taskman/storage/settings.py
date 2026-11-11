"""Storage settings from environment variables and a ``.env`` file.

The CLI loads ``.env`` from the repository root before a command runs.
Variables already set in the environment win. Docker Compose and Cloud Run
set the same variables directly.

Secrets (passwords) come only from the environment or from a secret file.
No error message contains a secret or a connection URL.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

from taskman.storage.store import StorageConfigError

ENV_FILE = ".env"
# Read from the user's environment only, never from a repository ``.env``.
_USER_ONLY = ("TASKMAN_SERVER_URL", "TASKMAN_TOKEN")
BACKENDS = ("sqlite", "postgres")
_SQL_SETTINGS = "Set TASKMAN_BACKEND=sqlite (the default) or postgres; see `taskman help`."
_SOCKET_SETTINGS = ("TASKMAN_DB_SOCKET", "TASKMAN_DB_NAME", "TASKMAN_DB_USER")


@dataclass(frozen=True, slots=True)
class StorageSettings:
    """Resolved storage settings.

    ``sqlite_path`` is set for the sqlite backend. ``postgres`` holds the
    connection for the postgres backend: either ``url``, or ``host`` (the
    socket directory), ``dbname`` and ``user``; plus ``password`` when one is
    configured.
    """

    backend: str
    sqlite_path: Path | None = None
    postgres: dict[str, str] = field(default_factory=dict, repr=False)


def repo_root(start: Path) -> Path:
    """The nearest directory that contains ``.git``, or ``start`` when there is none."""
    start = start.resolve()
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return start


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines.

    Blank lines and lines that start with ``#`` are skipped. An ``export``
    prefix is allowed. A value in single or double quotes is taken without the
    quotes; an unquoted value ends at `` #``.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def _tracked_by_git(root: Path, path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", path.name],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def load_env_file(start: Path | None = None, environ: MutableMapping[str, str] = os.environ) -> Path | None:
    """Copy variables from ``<repo root>/.env`` into ``environ`` where they are not set.

    Returns the file that was loaded, or ``None``. A ``.env`` that git tracks is
    not loaded: a committed file could send credentials to a host the
    repository chooses. A warning goes to stderr instead.
    """
    root = repo_root(start or Path.cwd())
    path = root / ENV_FILE
    if not path.is_file():
        return None
    if _tracked_by_git(root, path):
        import sys

        print(f"Taskman ignores {path}: git tracks it. Keep .env out of git.", file=sys.stderr)
        return None
    for key, value in parse_env_file(path.read_text(encoding="utf-8")).items():
        if key in _USER_ONLY:
            # A repository file must not send the user's token to a host that the repository names.
            import sys

            print(f"Taskman ignores {key} in {path}. Set it in your environment.", file=sys.stderr)
            continue
        environ.setdefault(key, value)
    return path


def default_sqlite_path() -> Path:
    return Path.home() / ".local" / "share" / "taskman" / "taskman.db"


def backend_name(environ: Mapping[str, str] = os.environ) -> str:
    """The backend named by ``TASKMAN_BACKEND``. Defaults to ``sqlite`` (decision D7)."""
    if environ.get("TASKMAN_TASKS_DIR", "").strip():
        raise StorageConfigError(
            "TASKMAN_TASKS_DIR is no longer used: Taskman stores work items in SQLite or Postgres, "
            f"not in tasks/ files. Unset it. {_SQL_SETTINGS}"
        )
    name = environ.get("TASKMAN_BACKEND", "").strip() or "sqlite"
    if name == "file":
        raise StorageConfigError(
            f"The file backend was removed; Taskman does not read tasks/ files. {_SQL_SETTINGS}"
        )
    if name not in BACKENDS:
        raise StorageConfigError(
            f"Unknown storage backend {name!r} in TASKMAN_BACKEND. Supported: {', '.join(BACKENDS)}."
        )
    return name


def storage_settings(environ: Mapping[str, str] = os.environ) -> StorageSettings:
    """Resolve and check the storage settings for the configured backend."""
    backend = backend_name(environ)
    url = environ.get("TASKMAN_DATABASE_URL", "").strip()
    if backend == "sqlite":
        return StorageSettings(backend, sqlite_path=_sqlite_path(url))
    return StorageSettings(backend, postgres=_postgres(url, environ))


def _sqlite_path(url: str) -> Path:
    if not url:
        return default_sqlite_path()
    if not url.startswith("sqlite://"):
        raise StorageConfigError("TASKMAN_DATABASE_URL must start with sqlite:// for the sqlite backend.")
    path = Path(url[len("sqlite://") :]).expanduser()
    if not path.is_absolute():
        raise StorageConfigError(
            "TASKMAN_DATABASE_URL for sqlite needs an absolute path, for example sqlite:///home/me/taskman.db."
        )
    return path


def _postgres(url: str, environ: Mapping[str, str]) -> dict[str, str]:
    if url:
        if not url.startswith(("postgresql://", "postgres://")):
            raise StorageConfigError(
                "TASKMAN_DATABASE_URL must start with postgresql:// for the postgres backend."
            )
        connection = {"url": url}
    else:
        values = {name: environ.get(name, "").strip() for name in _SOCKET_SETTINGS}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise StorageConfigError(
                "The postgres backend needs TASKMAN_DATABASE_URL, or TASKMAN_DB_SOCKET, "
                f"TASKMAN_DB_NAME and TASKMAN_DB_USER. Missing: {', '.join(missing)}."
            )
        connection = {
            "host": values["TASKMAN_DB_SOCKET"],
            "dbname": values["TASKMAN_DB_NAME"],
            "user": values["TASKMAN_DB_USER"],
        }
    password = _password(environ)
    if password:
        connection["password"] = password
    return connection


def _password(environ: Mapping[str, str]) -> str:
    direct = environ.get("TASKMAN_DB_PASSWORD", "")
    file_name = environ.get("TASKMAN_DB_PASSWORD_FILE", "").strip()
    if direct and file_name:
        raise StorageConfigError("Set TASKMAN_DB_PASSWORD or TASKMAN_DB_PASSWORD_FILE, not both.")
    if not file_name:
        return direct.strip()
    try:
        # A mounted secret often ends with a newline; it is not part of the password.
        return Path(file_name).expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise StorageConfigError(
            f"Cannot read the file named by TASKMAN_DB_PASSWORD_FILE: {exc.strerror}."
        ) from None
