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
- `taskman token create --person NAME` creates a token and shows it one time. `taskman token list` and `taskman token revoke ID` manage tokens. These commands open the database directly, so they run on a machine that reaches the database, for example through the Cloud SQL Auth Proxy. A token expires after 90 days unless `--days` sets another period.
- The CLI uses a server when `TASKMAN_SERVER_URL` is set, or after `taskman auth login`. It sends the token from `TASKMAN_TOKEN`, or the token of the saved login. Taskman reads these two variables from the environment only, never from a `.env` file, so a repository cannot send the token to another host.
- The saved login is `~/.config/taskman/credentials.json`, with mode 0600 in a directory with mode 0700. No command prints a token.
- The CLI sends a token over HTTPS, or over HTTP to the same machine. It does not follow redirects.
- A change is one operation, for example `close`. The server has no call that writes a field directly, so no caller can skip a lifecycle rule.
- The server stops cleanly on SIGTERM.

## Browser login

`taskman auth login` signs a person in with Google and gives this computer its own token. The server must have a Google OAuth client (`TASKMAN_GOOGLE_CLIENT_ID`).

1. The CLI starts a listener on `127.0.0.1` and opens `/auth/cli` in the browser. It sends a random state and the hash of a secret of its own (PKCE, RFC 7636).
2. The server records the login and sends the browser to Google.
3. Google returns the browser to `/auth/callback` with a code. The server trades the code with Google for the person's ID token over HTTPS. It checks the issuer, the audience, the expiry, the nonce and a verified email address. The email address must be in `TASKMAN_ALLOWED_EMAILS`, or the account must belong to a Google Workspace organization in `TASKMAN_ALLOWED_DOMAINS`. Google reports that membership in the `hd` claim of the ID token.
4. The server sends the browser to the CLI's listener with a one-time code. The CLI trades that code and its secret at `/auth/exchange` for a token.

Each sign-in works one time and for 10 minutes. The one-time code works for 2 minutes. The person's email address is the person name. The server keeps the login steps in the `logins` table, so any server instance can serve any step.

`/auth/token` signs a person in the same way and shows a personal access token on the page. A machine without a browser uses it with `taskman auth login --with-token`. A copy of a token works on any machine; the owner accepted this risk for machines without a browser.

## Deployment

- One container image runs the server. The entry point is `taskman`, and the default command is `serve --host 0.0.0.0`. Other arguments run another command with the same image, for example `db upgrade`.
- The image holds no settings. All settings come from environment variables and secret files, so the same variables work for the CLI, Docker Compose and Cloud Run.
- On Google Cloud, the server runs on Cloud Run, and the database is Cloud SQL for PostgreSQL. Cloud Run opens the Cloud SQL unix socket in `/cloudsql/<connection name>` and mounts each secret as a file.
- A Cloud Run job runs `taskman db upgrade` before a new image serves traffic. The server does not start with an older schema, so a deploy without the upgrade fails and the previous revision continues to serve.
- The service accepts calls from any address, and Cloud Run does not check the caller's Google identity. Taskman checks the token of each call. Thus agent machines need no cloud credentials.
- The local Docker Compose stack has the same layout. Postgres shares its unix socket through a volume at `/cloudsql/local`, and the password comes from a secret file.
- Another cloud needs a container service with environment variables and secret files, PostgreSQL 16, a one-time task for `db upgrade`, and HTTPS in front of the server.

[Run the Taskman server on Google Cloud](deploy-gcp.md) gives the steps and the equivalent AWS (Amazon Web Services) services.

## Workspace UI

`taskman ui` serves the page at `127.0.0.1`. The server accepts only requests with a local `Host` header. Requests that change data must also send the `X-Taskman` header. The page polls the server every 5 seconds. All server threads share one store, and the store serializes access to its connection.

## Limits

- Browser login supports Google only.
- The server has no page to list or revoke tokens. An operator does this with `taskman token list` and `taskman token revoke` on a machine that reaches the database.
- The server does not serve the workspace page. `taskman ui` serves it on the computer of the user.
- The server uses one database connection for all calls and does not connect again after the connection breaks. After a database restart, `/readyz` answers 503 until the server process restarts. On Cloud Run, a liveness probe on `/readyz` replaces such an instance.
- Taskman does not read `tasks/` Markdown files from earlier versions, and imports nothing from them (decision D4).
- There is no consolidation of completed work (decision D8).
