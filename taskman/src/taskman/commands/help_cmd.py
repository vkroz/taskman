"""Short description of where Taskman keeps work."""
from __future__ import annotations

import typer

_HELP_TEXT = """\
TASKMAN

Tasks, events, and flight-control state live in Postgres on the Taskman server.
The `taskman` CLI is an HTTP client. The server does not read or write git
checkouts. `taskman pull ID` writes a working copy under `.taskman/tasks/`
on the machine where the CLI runs.

Set TASKMAN_URL (default http://127.0.0.1:8080) and TASKMAN_ACCESS_TOKEN.
`taskman config --server URL` saves the URL for this user. The token can also
live in a config file under the key "token"; prefer the environment variable
and do not commit it.

Commands:
  new, list, show, update, move, depend, ready, context, pull, record
  complete (alias close), activity, report, validate
  hook claude|git, hooks install, config, install, serve, ui

A task id is an integer assigned by the server. Status is backlog, active, or
done. `pull` moves a backlog task to active and records who pulled it.
`complete` marks it done.

--agent (alias --author) names the AUTHOR of the changes an event records:
a person, claude-code, codex, and so on. Taskman does not control agents.
Every command that records an event requires it: pull, record, complete,
close, report commit|pr|completion, and hook claude|git. It is read only from
the command line. A missing or blank value fails with
"--agent <author> is required: name who authored these changes".
Surrounding whitespace is trimmed. The server also rejects a blank author.

Hooks report commits, pull requests, and completion. Claude Code runs the
plugin hook, which records the author as claude-code. Codex and other git
clients use `taskman hooks install`, which writes a post-commit hook that
records the commit author: `git log -1 --format=%an`, then
`git config user.name`, then `git`. Pull requests without a plugin hook are
reported with `taskman report pr --url URL --agent AUTHOR`.

Files already stored under a repository's tasks/ directory are not imported.
Copy their text into `taskman new` yourself.

The access check is one shared bearer token. There are no user accounts.
GET /health is liveness and does not open a database connection. GET /ready
checks Postgres. Neither requires the token. The flight-control page keeps
the token in the browser tab and sends it as Authorization: Bearer.

Repeating a commit with the same sha, or a completion that matches the task's
latest event for the same author and session, returns 200 and the event
already stored. A new event returns 201.

`complete` clears .taskman/active when it names the completed task. The Stop
hook does not report a completion for a task that is already done.
"""


def help_cmd() -> None:
    """Print how Taskman stores work and how the CLI reaches the server."""
    typer.echo(_HELP_TEXT)
