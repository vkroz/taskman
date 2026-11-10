"""Serialize CLI and UI mutations of the same task tree."""
import inspect
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Optional
import fcntl

import typer

_thread_lock = RLock()
_ACTOR_HELP = "Author of the change. Default: git user.name."


@contextmanager
def mutation_lock(tasks_dir: Path):
    tasks_dir.mkdir(parents=True, exist_ok=True)
    with _thread_lock, (tasks_dir / "_taskman.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def mutating(command):
    """Run the command under the store lock, with an optional ``--actor`` (decision M1).

    A command that declares its own ``actor`` parameter receives the value too.
    """
    signature = inspect.signature(command)
    own_actor = "actor" in signature.parameters

    @wraps(command)
    def wrapped(*args, actor=None, **kwargs):
        from taskman.storage.store import open_store

        store = open_store()
        if actor and hasattr(store, "actor"):
            store.actor = actor
        if own_actor:
            kwargs["actor"] = actor
        with store.lock():
            return command(*args, **kwargs)

    if not own_actor:
        option = inspect.Parameter(
            "actor",
            inspect.Parameter.KEYWORD_ONLY,
            default=typer.Option(None, "--actor", help=_ACTOR_HELP),
            annotation=Optional[str],
        )
        wrapped.__signature__ = signature.replace(parameters=[*signature.parameters.values(), option])
    return wrapped
