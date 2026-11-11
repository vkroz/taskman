"""`help` command — print the taskman storage model documentation.

This is the in-tool source of truth for the storage model, keys, lifecycle
and invariants. For per-command syntax, use
``taskman <command> --help``.

Module named ``help_cmd`` (not ``help``) to avoid shadowing the builtin.
"""
from __future__ import annotations

import typer

_HELP_TEXT = """\
TASKMAN — Shared work items for coding agents and people

This help documents the storage model and the lifecycle rules. For command
syntax, run `taskman <command> --help`. For the command list, run
`taskman --help`.

================================================================================
STORAGE
================================================================================

  Taskman keeps work items in a SQL database: SQLite (the default) or
  Postgres. Run `taskman db upgrade` once to create the schema, and again
  after you upgrade Taskman. It does nothing when the schema is current.

  Taskman does not read `tasks/` Markdown files. Such files are historical
  text from earlier versions.

================================================================================
ITEMS AND KEYS
================================================================================

  An item has a key, a type, a title, a description, a status, an optional
  parent, dependencies, an assignee, labels, comments and an activity
  history.

  key        <PROJECT>-<number>, for example TM-42. The project key has 2-10
             uppercase letters or digits and starts with a letter. Numbers
             count up per project and are never used again, even after a
             discard.
  type       epic | feat | task. Change it with `convert`.
  status     backlog | active | done.
  draft      A new item is a draft until `finalize`.

  A new top-level item needs --project KEY; Taskman creates the project on
  first use. A child gets the project of its parent. Dependencies stay inside
  one project. A command finds the item and its project from the key.

  A comment has a kind: decision, progress, handoff, summary or
  verification. A verification comment has a result: pass, fail or blocked.
  Nobody can change or delete a comment.

  Each change to a field writes an activity entry with its author and time.
  The author is the --actor value, or the git user.name.

================================================================================
LIFECYCLE
================================================================================

  new       Create a draft work item (top-level with --project, or nested
            with --parent).
  finalize  Clear the draft marker after the user reviews.
  move      Change status. Children keep their own status.
  convert   Change an item's type. An item with children cannot become a task.
  close     Mark a completed item done. Requires all descendants and
            dependencies done, a current summary, and a current passing
            verification.
  list      List items; --json for agents.
  show      Print an item's fields and description.
  context   Read item, ancestors, descendants, dependencies and activity as
            JSON.
  update    Replace an open item's description using --body-file.
  depend    Replace dependencies with repeated --on keys, or --clear.
  comment   Add a decision, progress, handoff, summary or verification
            comment. `record` is an alias.
  assign    Set the assignee of an open item; `unassign` removes it.
  label     Add or remove labels of an open item.
  activity  Show the field changes of an item.
  reorder   Move an open item to another position among its siblings.
  discard   Remove an unreferenced draft leaf.
  install   Install the shared plugin using repeated --platform targets.
  validate  Check the dependency graph: unknown dependencies and cycles.

================================================================================
INVARIANTS
================================================================================

  - Closed items are immutable. A done item accepts no change, no comment
    and no new position. Subsequent changes require a new linked item.
    Open items may change; description, dependency, type, decision and
    progress changes, and child changes, invalidate prior completion
    evidence. Summarize decisions and results before closing. Taskman
    records checks but does not run them.

  - task = leaf. A `task` item has no children. To add a child to a task,
    first `convert` it to `feat` or `epic`.

  - Free type ladder. Any nesting is allowed: epic > feat > task is one
    shape; epic > task and feat > task and feat > feat are equally valid.
    The epic > feat > task ladder is not enforced.

================================================================================
ENVIRONMENT
================================================================================

  Taskman reads these variables from the environment and from a .env file
  at the repository root. A variable in the environment wins over .env.
  Taskman ignores a .env file that git tracks. Keep .env out of git.

  TASKMAN_BACKEND      sqlite (default) or postgres.
  TASKMAN_DATABASE_URL sqlite: sqlite:///absolute/path.db (or sqlite://~/...).
                       Default: ~/.local/share/taskman/taskman.db.
                       postgres: postgresql://user@host:port/dbname.
  TASKMAN_DB_SOCKET    postgres without a URL: the directory of the unix
                       socket, for example /cloudsql/project:region:instance.
  TASKMAN_DB_NAME      postgres without a URL: the database name.
  TASKMAN_DB_USER      postgres without a URL: the database user.
  TASKMAN_DB_PASSWORD  postgres: the password. Optional.
  TASKMAN_DB_PASSWORD_FILE
                       postgres: a file that holds the password, for example
                       a mounted secret. Surrounding whitespace is removed.
                       Set this or TASKMAN_DB_PASSWORD, not both.

  The postgres backend needs the `postgres` extra.

  TASKMAN_SERVER_URL   The address of a Taskman server, for example
                       https://taskman.example.com. When set, every command
                       works through the server and opens no database.
                       https:// is required unless the server runs on this
                       computer.
  TASKMAN_TOKEN        The token for that server. Without it, Taskman uses the
                       token of `taskman auth login`.
  PORT                 `taskman serve` only: the port to listen on.
                       Default: 8080.

  Taskman reads TASKMAN_SERVER_URL and TASKMAN_TOKEN from the environment
  only, never from .env: a repository file must not send a token to a host.

  `taskman auth login --server URL` signs in through a browser and saves the
  server and a token in ~/.config/taskman/credentials.json. Later commands
  use that server. `--with-token` reads a personal access token from stdin
  instead. `taskman auth status` and `taskman auth logout` manage the login.

  Browser login on the server needs TASKMAN_GOOGLE_CLIENT_ID,
  TASKMAN_GOOGLE_CLIENT_SECRET (or _FILE), TASKMAN_PUBLIC_URL, and
  TASKMAN_ALLOWED_EMAILS or TASKMAN_ALLOWED_DOMAINS.

  Examples (.env):

    # SQLite, one shared file for all repositories (no settings needed)
    TASKMAN_BACKEND=sqlite

    # Local Postgres
    TASKMAN_BACKEND=postgres
    TASKMAN_DATABASE_URL=postgresql://taskman@localhost:5432/taskman
    TASKMAN_DB_PASSWORD=change-me

    # Cloud SQL through its unix socket (Cloud Run)
    TASKMAN_BACKEND=postgres
    TASKMAN_DB_SOCKET=/cloudsql/project:region:instance
    TASKMAN_DB_NAME=taskman
    TASKMAN_DB_USER=taskman_app
    TASKMAN_DB_PASSWORD_FILE=/secrets/taskman-db-password
"""


def help_cmd() -> None:
    """Print the taskman persistence model documentation."""
    typer.echo(_HELP_TEXT)
