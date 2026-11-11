# Taskman architecture

Taskman is one Python program. It runs as a command-line tool (CLI) and as a server. Coding agents and people use the CLI on their own computer. The CLI reaches work items in one of two ways: through a Taskman server, or directly in a database on the same computer.

## Components

| Component | Responsibility |
| --- | --- |
| CLI | Reads arguments, opens one store for each command, and reports results and exit codes. |
| Lifecycle | Checks every work-item rule: drafts, close gates, immutability, revisions, projects and dependencies. A backend does not check rules. |
| Store interface | The operations of the storage: read, create, save, change status or type, delete, reorder, and read activity. |
| SQL backend | Keeps items in SQLite or Postgres, with the model in [the data model](data-model.md). It changes item data from the lifecycle into rows: a changed field becomes a column update and an activity entry, a new journal entry becomes a comment, and dependencies become links. |
| Remote store | The store of a CLI that uses a server. It sends reads and whole operations to the server over HTTP. It does not know which database the server uses. |
| Server | `taskman serve`. It alone opens the database. It checks the token of each call, runs each operation through the lifecycle in one transaction, and answers in JSON. |
| Tokens | A token is a secret string that stands for one person. The database keeps only a SHA-256 fingerprint of each token. |
| Settings | Select the server, or the backend and the connection, from environment variables and from a `.env` file at the repository root. |
| Workspace UI | A local HTTP server and one web page. It uses the same lifecycle and store as the CLI. |
| Installer | Installs the shared plugin into Codex and Claude Code through platform adapters. |

## Flow of a change

1. The CLI reads the settings and opens the store.
2. The CLI starts one write transaction.
3. The lifecycle reads the items, checks the rules, and tells the store what to write.
4. The store writes the change. It also writes an activity entry with the author: the `--actor` value, or the git `user.name`.
5. The transaction commits. If a step fails, the transaction rolls back and the database does not change.

With a server, the CLI does none of these steps itself. It sends the operation and its arguments to the server. The server finds the person of the token, then does steps 2 to 5. The author of the change is that person. When the caller gives `--actor`, the author is `<actor> for <person>`, for example `claude for Vlad`.

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

Key numbers come from the project row in the same transaction, so two writers never get the same key. A discarded key is not used again.

## Server

`taskman serve` listens on `127.0.0.1` and on the port in the `PORT` variable, or 8080. `--host 0.0.0.0` makes it reachable from other machines, for example in a container.

- Each call under `/api/v1/` needs a token in the `Authorization: Bearer` header. A call without a valid token gets status 401 and changes nothing. `/healthz` and `/readyz` need no token.
- `taskman token create --person NAME` creates a token and shows it one time. `taskman token list` and `taskman token revoke ID` manage tokens. These commands run on the machine of the server. A token expires after 90 days unless `--days` sets another period.
- The CLI uses a server when `TASKMAN_SERVER_URL` is set. It sends the token from `TASKMAN_TOKEN`. Taskman reads these two variables from the environment only, never from a `.env` file, so a repository cannot send the token to another host.
- The CLI sends a token over HTTPS, or over HTTP to the same machine. It does not follow redirects.
- A change is one operation, for example `close`. The server has no call that writes a field directly, so no caller can skip a lifecycle rule.
- The server stops cleanly on SIGTERM.

## Workspace UI

`taskman ui` serves the page at `127.0.0.1`. The server accepts only requests with a local `Host` header. Requests that change data must also send the `X-Taskman` header. The page polls the server every 5 seconds. All server threads share one store, and the store serializes access to its connection.

## Limits

- The server has no sign-in page and no browser login. An operator creates each token on the machine of the server.
- The server does not serve the workspace page. `taskman ui` serves it on the computer of the user.
- The server uses one database connection for all calls.
- Taskman does not read `tasks/` Markdown files from earlier versions, and imports nothing from them (decision D4).
- There is no consolidation of completed work (decision D8).
