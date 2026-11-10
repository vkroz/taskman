# Taskman architecture

Taskman is one Python command-line program. Coding agents and people use it on their own computer. It has no server process except the local UI.

## Components

| Component | Responsibility |
| --- | --- |
| CLI | Reads arguments, opens one store for each command, and reports results and exit codes. |
| Lifecycle | Checks every work-item rule: drafts, close gates, immutability, revisions, projects and dependencies. A backend does not check rules. |
| Store interface | The operations that every backend provides: read, create, save, change status or type, delete, and reorder. Each backend also tells if it supports consolidation and if a done item can move. |
| File backend | Keeps items as Markdown files with frontmatter in the `tasks/` tree of the repository. It supports consolidation. |
| SQL backend | Keeps items in SQLite or Postgres, with the model in [the data model](data-model.md). It changes item data from the lifecycle into rows: a changed field becomes a column update and an activity entry, a new journal entry becomes a comment, and dependencies become links. |
| Settings | Select the backend and the connection from environment variables and from a `.env` file at the repository root. |
| Workspace UI | A local HTTP server and one web page. It uses the same lifecycle and store as the CLI. |
| Installer | Installs the shared plugin into Codex and Claude Code through platform adapters. |

## Flow of a change

1. The CLI reads the settings and opens the store.
2. The CLI starts one write transaction (SQL) or takes the file lock (file backend).
3. The lifecycle reads the items, checks the rules, and tells the store what to write.
4. The store writes the change. On a SQL backend, it also writes an activity entry with the author: the `--actor` value, or the git `user.name`.
5. The transaction commits. If a step fails, the transaction rolls back and the database does not change.

## Storage technology

- **SQLite.** One database file. The default is `~/.local/share/taskman/taskman.db`, shared by all repositories of the user.
- **Postgres 16.** The driver is psycopg 3, installed with the `postgres` extra. Taskman connects with a URL, or through a unix socket directory, for example the Cloud SQL socket `/cloudsql/<instance>` on Cloud Run. The password comes from an environment variable or a mounted secret file. No error message shows a password or a URL.
- **Schema versions.** `taskman db upgrade` applies the missing schema versions in order, in one transaction, and records each version. A store refuses a database with an older or a newer schema version.

## Concurrency

Many agents can change items at the same time. Each change is one transaction that holds the database write lock:

- SQLite takes the write lock with `BEGIN IMMEDIATE`.
- Postgres takes a transaction-scoped advisory lock as the first statement of a READ COMMITTED transaction.
- Both wait 30 seconds for the lock, then stop with an error.
- Reads use a snapshot transaction and do not wait for writers.
- The file backend uses a file lock in the `tasks/` tree.

Key numbers come from the project row in the same transaction, so two writers never get the same key. A discarded key is not used again.

## Workspace UI

`taskman ui` serves the page at `127.0.0.1`. The server accepts only requests with a local `Host` header. Requests that change data must also send the `X-Taskman` header. The page polls the server every 5 seconds. All server threads share one store, and the store serializes access to its connection.

## Limits

- There is no shared Taskman server. Each computer connects to the database directly.
- The file backend stays until feature 261109005 removes it (decision D1).
- Consolidation is available only with the file backend (decision D8).
