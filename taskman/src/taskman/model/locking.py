"""Serialize CLI and UI mutations of the same task tree."""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from threading import RLock
import fcntl

_thread_lock = RLock()


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
    @wraps(command)
    def wrapped(*args, **kwargs):
        from taskman.storage.store import open_store
        with open_store().lock():
            return command(*args, **kwargs)
    return wrapped
