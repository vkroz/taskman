"""Run CLI mutations under the store's write lock."""
import inspect
from functools import wraps
from typing import Optional

import typer

_ACTOR_HELP = "Author of the change. Default: git user.name."


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
        if actor:
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
