"""`help` command — print the taskman persistence model documentation.

This is the in-tool source of truth for the storage layout, name grammar,
ID scheme, lifecycle, and invariants. For per-command syntax, use
``taskman <command> --help``.

Module named ``help_cmd`` (not ``help``) to avoid shadowing the builtin.
"""
from __future__ import annotations

import typer

_HELP_TEXT = """\
TASKMAN — Uniform recursive work-item management

This help documents the persistence model. For per-command syntax, run
`taskman <command> --help`. For the command list, run `taskman --help`.

The STORAGE LAYOUT, NAME GRAMMAR and ID GENERATION sections describe the file
backend. SQL KEYS describes the sqlite and postgres backends.

================================================================================
STORAGE LAYOUT (file backend)
================================================================================

  tasks/
    backlog/                                — top-level status folders.
    active/                                   Only top-level items live here
    done/                                     directly.
      <root-item>/                          — nested item directory
        _<id>.md                            — special body file (bare ID)
        <child-item>/                       — nested child (directory form)
          _<child-id>.md
          <grandchild>.md
        <leaf-child>.md                     — nested child (file form)

  Top-level items live directly under one of the status folders. Nested items
  sit inside their parent's directory. The top-level folder reflects the
  root's status; each nested item carries its own status in its name.

================================================================================
NAME GRAMMAR (file backend)
================================================================================

  Directory item:  <priority>.<type>-<id>.<status>.<slug>
  File item:       <priority>.<type>-<id>.<status>.<slug>.md
  Special file:    _<id>.md      (inside a directory item; bare ID, no type)

  Fields:
    priority   2-digit numeric. Currently always "00"; reserved for ranking.
    type       epic | feat | task. Visual label, convertible via `convert`.
    id         9-digit YYMMDDnnn. The bare number is the stable identity.
    status     backlog | active | done.
    slug       [a-z0-9_]+. Lowercase, digits, underscores. No dots, no hyphens.

  The `<type>-<id>` form in the name renders the current type label for
  human scannability; on `convert`, the name is renamed but the underlying
  ID does not change. References between items (`depends_on:`) use the bare ID.

================================================================================
ID GENERATION (file backend)
================================================================================

  IDs are YYMMDDnnn — today's date (YYMMDD) + a 3-digit per-day sequence
  shared across all types. Gaps are allowed: deleting an item leaves a hole;
  allocation returns the max-used-today + 1. Once 999 is allocated for a
  given day, the day's counter is exhausted.

================================================================================
SQL KEYS (sqlite and postgres backends)
================================================================================

  An item key is <PROJECT>-<number>, for example TM-42. The project key has
  2-10 uppercase letters or digits and starts with a letter. Numbers count up
  per project and are never used again, even after a discard.

  A new top-level item needs --project KEY; Taskman creates the project on
  first use. A child gets the project of its parent. Dependencies stay inside
  one project. A command finds the item and its project from the key.

  Each change to a field writes an activity entry with its author and time.
  The author is the --actor value, or the git user.name. Consolidation is not
  available, and a done item keeps its position.

================================================================================
LIFECYCLE
================================================================================

  new       Create a draft work item (top-level or nested via --parent).
  finalize  Clear `draft: true` after the user reviews.
  move      Change status; top-level items move with their subtree.
  convert   Relabel an item's type. Cannot relabel a directory item to `task`.
  close     Freeze a completed item. Requires all descendants/dependencies done,
            a current summary, and a current passing verification.
            Top-level items move their whole subtree to `done/`.
  list      Walk the tree and print items; --json for agent consumption.
  show      Print an item's frontmatter and body.
  context   Read item, ancestors, descendants and dependencies as JSON.
  update    Replace an open item's body using --body-file.
  depend    Replace dependencies with repeated --on IDs, or --clear.
  comment   Add a decision, progress, handoff, summary or verification
            comment. `record` is an alias.
  assign    Set the assignee of an open item; `unassign` removes it.
  label     Add or remove labels of an open item.
  activity  Show the field changes of an item (sqlite and postgres).
  discard   Remove an unreferenced draft leaf.
  install   Install the shared plugin using repeated --platform targets.
  validate  Integrity check: name↔frontmatter drift, parent references,
            grammar, task=leaf invariant.

================================================================================
INVARIANTS
================================================================================

  - Closed items are immutable. Subsequent changes require a new linked item.
    Open items may change; body, dependency, type, implementation-journal and
    child changes invalidate prior completion evidence. Summarize decisions
    and results before closing. Taskman records checks but does not run them.

  - task = leaf. A `task` item is always a file, never a directory. To add a
    child to a task, first `convert` it to `feat` or `epic`.

  - Free type ladder. Any nesting is allowed: epic > feat > task is one
    shape; epic > task and feat > task and feat > feat are equally valid.
    The epic > feat > task ladder is not enforced.

  - Subtree moves with root. Moving a top-level item moves all its
    descendants; nested children keep their own in-name status tokens.

  - Frontmatter is the source of truth for content (title, body). The
    on-disk name is a generated cache of (priority, type, id, status, slug).
    `validate` reconciles them and errors on drift.

================================================================================
ENVIRONMENT
================================================================================

  Taskman reads these variables from the environment and from a .env file
  at the repository root. A variable in the environment wins over .env.
  Taskman ignores a .env file that git tracks. Keep .env out of git.

  TASKMAN_BACKEND      Storage backend: file (default), sqlite or postgres.
                       Run `taskman db upgrade` once before you use sqlite
                       or postgres.
  TASKMAN_TASKS_DIR    file backend. Selects the shared tasks tree. If unset,
                       searches from the working directory up to the
                       repository root for tasks/. Multiple agents must use
                       the same tree.
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

  `taskman db upgrade` creates or upgrades the database schema of the sqlite
  and postgres backends. The postgres backend needs the `postgres` extra.

  Examples (.env):

    # SQLite, one shared file for all repositories
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
