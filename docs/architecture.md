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
| Workspace UI | One web page and its data calls, served by the server in local mode, or after a Google sign-in on a hosted server. It uses the same lifecycle and store as the CLI. |
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
- **Postgres 16.** The driver is psycopg 3, installed with the `postgres` extra. Taskman connects with a URL, or through a unix socket directory, for example the Cloud SQL socket `/cloudsql/<connection name>` on Cloud Run. The password comes from an environment variable or a mounted secret file. No error message shows a password or a URL.
- **Schema versions.** `taskman db upgrade` applies the missing schema versions in order, in one transaction, and records each version. A version below the highest recorded one is also applied when it is missing. A store refuses a database with an older or a newer schema version, or with a missing version.

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
- `taskman token create --person NAME` creates a token and shows it one time. With a server set, it needs `--database`; without it, it refuses and names Settings > Tokens of the server's workspace page. `taskman token list --database` and `taskman token revoke ID --database` manage the tokens of every person. These commands open the database directly, so they run on a machine that reaches the database, for example through the Cloud SQL Auth Proxy. A token expires after 90 days unless `--days` sets another period.
- Without `--database`, and with a server set, `taskman token list` and `taskman token revoke ID` call `/api/v1/tokens`: a person lists and revokes only their own tokens. Another person's token gets the same answer as a token that does not exist. A revoked token is refused on the next call, also when it is the token of the revoking call.
- The CLI uses a server when `TASKMAN_SERVER_URL` is set, or after `taskman auth login`. It sends the token from `TASKMAN_TOKEN`, or the token of the saved login. Taskman reads these two variables from the environment only, never from a `.env` file, so a repository cannot send the token to another host.
- The saved login is `~/.config/taskman/credentials.json`, with mode 0600 in a directory with mode 0700. No command prints a token.
- The CLI sends a token over HTTPS, or over HTTP to the same machine. It does not follow redirects.
- A change is one operation, for example `close`. The server has no call that writes a field directly, so no caller can skip a lifecycle rule.
- The server stops cleanly on SIGTERM.

## Browser login

`taskman auth login` signs a person in with Google and gives this computer its own token. The server must have a Google OAuth (Open Authorization) client (`TASKMAN_GOOGLE_CLIENT_ID`).

1. The CLI starts a listener on `127.0.0.1` and opens `/auth/cli` in the browser. It sends a random state and the hash of a secret of its own (PKCE, RFC 7636).
2. The server records the login and sends the browser to Google.
3. Google returns the browser to `/auth/callback` with a code. The server trades the code with Google for the person's ID token over HTTPS. It checks the issuer, the audience, the expiry, the nonce and a verified email address. The email address must be in `TASKMAN_ALLOWED_EMAILS`, or the account must belong to a Google Workspace organization in `TASKMAN_ALLOWED_DOMAINS`. Google reports that membership in the `hd` claim of the ID token.
4. The server sends the browser to the CLI's listener with a one-time code. The CLI trades that code and its secret at `/auth/exchange` for a token.

Each sign-in works one time and for 10 minutes. The one-time code works for 2 minutes. The person's email address is the person name. The server keeps the login steps in the `logins` table, so any server instance can serve any step.

A person gets a personal access token for a machine without a browser on Settings > Tokens of the workspace page (`/settings/tokens`). The machine uses it with `taskman auth login --with-token`. A copy of a token works on any machine; the owner accepted this risk for machines without a browser. `/auth/token` sends the browser to `/settings/tokens` and creates no token. `/auth/token/revoke` does not exist.

Settings > Tokens lists, creates and revokes the tokens of the page's person only, through the data calls `/api/tokens`, `/api/tokens/create` and `/api/tokens/revoke`. These calls have the checks of the other data calls of the page: in hosted mode the session, the `Origin` and the CSRF key; in local mode the `Host` and the `X-Taskman: 1` header. Thus a request with a token alone cannot create a token. A new token has a name of 1 to 60 characters and expires after 7, 30, 90 or 365 days. Only the answer of the create call holds the value, with `Cache-Control: no-store` and `Referrer-Policy: no-referrer`. The page keeps the value in memory and shows it one time: the address, the history and the browser storage never hold it. The list leaves out revoked tokens; the database keeps their rows. The revoke of the ID of another person's token gets the same answer as an unknown ID.

## Deployment

- One container image runs the server. The entry point is `taskman`, and the default command is `serve --host 0.0.0.0`. Other arguments run another command with the same image, for example `db upgrade`.
- The image holds no settings. All settings come from environment variables and secret files, so the same variables work for the CLI, Docker Compose and Cloud Run.
- On Google Cloud, the server runs on Cloud Run, and the database is Cloud SQL for PostgreSQL. Cloud Run opens the Cloud SQL unix socket in `/cloudsql/<connection name>`. It sets the database password, and the OAuth client secret of browser login, as environment variables from Secret Manager.
- A Cloud Run job runs `taskman db upgrade` before a new image serves traffic. The server does not start with an older schema, so a deploy without the upgrade fails and the previous revision continues to serve.
- The service accepts calls from any address, and Cloud Run does not check the caller's Google identity. Taskman checks the token of each call. Thus agent machines need no cloud credentials.
- [`deploy/gcp/bootstrap.sh`](../deploy/gcp/bootstrap.sh) sets up a Google Cloud project once: the database and its user, the secrets, the service accounts and their roles, Workload Identity Federation, and the public service. [`deploy/gcp/deploy.sh`](../deploy/gcp/deploy.sh) runs the upgrade job with one image, then deploys the service with the same image.
- The GitHub Actions workflow `ci.yml` (continuous integration) tests each pull request. The workflow `deploy-nemos-infra.yml` runs the same checks on each push to `staging`, builds and pushes the image, and runs `deploy.sh` with the image digest. It gets access to Google Cloud through Workload Identity Federation, with no stored key.
- The Taskman code and tests hold no organization names. The scripts and the workflow read them from `deploy/gcp/<environment>.env` ([ADR (Architecture Decision Record) 0001](adrs/0001-deploy-the-server-to-google-cloud.md)).
- The local Docker Compose stack has the same layout. Postgres shares its unix socket through a volume at `/cloudsql/local`, and the password comes from a secret file.
- Another cloud needs a container service with environment variables, PostgreSQL 16, a one-time task for `db upgrade`, and HTTPS in front of the server. Secrets can come as files or as environment variables.

[Run the Taskman server on Google Cloud](deploy-gcp.md) gives the steps and the equivalent AWS (Amazon Web Services) services. The Taskman maintainers have not tested the AWS path.

## Workspace UI

In local mode, `taskman serve` serves the page and its data calls without sign-in. `TASKMAN_LOCAL_MODE=1` turns local mode on. `TASKMAN_LOCAL_PERSON` names the author of each change that the page makes. Without that name, the server does not start in local mode. The Docker Compose stack runs the server in local mode, on `127.0.0.1:8080` of the computer.

In local mode, the server refuses with 403 each request of the page whose `Host` is not `127.0.0.1` or `localhost`, with any port or no port. Thus a page of another site that changes the address of its domain to 127.0.0.1 (DNS rebinding) cannot use the server, because the browser sends that domain. Requests that change data must also send the `X-Taskman: 1` header. The token API and the health checks work the same in local mode and in hosted mode.

In hosted mode, browser login is on and local mode is off. The server does not start when both `TASKMAN_LOCAL_MODE=1` and `TASKMAN_GOOGLE_CLIENT_ID` are set.

- A page request (`/`, `/items/KEY`, `/settings` or `/settings/tokens`) without a valid session goes to Google. The login remembers the requested address, path and query. The server returns the browser only to a path of its own: a path that starts with `//`, or that has a `\`, goes to `/`.
- The redirect to Google also sets the sign-in cookie `__Host-taskman_login` (HttpOnly, Secure, SameSite=Lax, Path=/, 10 minutes). The login keeps the SHA-256 fingerprint of its value. `/auth/callback` creates a session only for the browser that sends this cookie, so a callback address that a person sends to another browser gets 400 and no session. Two tabs of one browser share the cookie, so both can sign in at the same time.
- After the sign-in, the server creates a session and sets the cookie `__Host-taskman_session` (HttpOnly, Secure, SameSite=Lax, Path=/). The `sessions` table keeps the SHA-256 fingerprint of the session secret, the person, the start and the expiry. Every server instance accepts the session. A session ends 7 days after the sign-in or at sign-out; use does not renew it. A new sign-in deletes the sessions that ended or expired.
- A data call without a valid session gets 401 JSON. The page then reloads itself: the reload goes through the sign-in and returns to the same address.
- Each change of the page (each POST data call and `/auth/logout`) must send an `Origin` equal to the origin of `TASKMAN_PUBLIC_URL`, and the header `X-Taskman-CSRF` (cross-site request forgery). The value is an HMAC-SHA256 of the session secret, so only the session's own page knows it. The server puts it in a `<meta>` tag of the page. The server checks both before it runs any change; a failed check gets 403 and changes nothing. A key that is not the key of the cookie's session, from the server's own origin, gets 403 with the kind `stale`: the page holds the key of an earlier session, as in a second tab after a new sign-in. The page then reloads itself and gets the key of the current session.
- The page has a Content Security Policy: only its own inline scripts and styles run, by their SHA-256 hashes, and it connects only to its own server. Other sites cannot frame it. The `/auth/*` pages keep `default-src 'none'`.
- Sign out, in the account menu, posts to `/auth/logout`. The server ends the session, clears the cookie, and the page opens `/auth/signed-out`. When the session already ended, the page also opens `/auth/signed-out`.
- Copy link gives `TASKMAN_PUBLIC_URL/items/KEY`.

Without local mode and without browser login, `/`, `/items/KEY`, `/settings` and `/settings/tokens` answer a page that says that browser login is not set up and names its settings. The data calls answer 404.

The page polls the server every 5 seconds. All server threads share one store. The server sets the author of the store under the store lock, for each request: the person of local mode or of the session for the page, and the person of the token for the API. The CLI with a direct database has no UI.

## Limits

- Browser login supports Google only.
- The session cookie is `Secure`: a hosted server needs an `https://` public address. Browsers accept it over HTTP only on `localhost` and `127.0.0.1`.
- The server uses one database connection for all calls. On Postgres, the first statement of a transaction has a 5-second time limit. After it, the connection counts as lost. When a transaction starts on a closed, broken or lost connection, the server opens a new connection and tries the start again, at most 3 more times, after 0.5, 1 and 2 seconds. It does not try again a transaction that failed after its start, so a change is never written twice. After a database restart, `/readyz` answers 200 again at the first call after the database is back, in the same server process. While the database is away, `/readyz` answers 503. On Cloud Run, a liveness probe on `/readyz` replaces an instance after 3 failed checks.
- With browser login on, anyone can open `/auth/cli`, `/auth/ui` and the workspace page without a token or session. Each request adds a row to the `logins` table and deletes old rows, under the database write lock. A row stays 1 day when Google signed in a person who may use the server, and 1 hour in all other cases. A sign-in that returns with an error, or that fails a later check, stays 1 hour. Taskman does not limit the rate of these requests.
- Taskman does not read `tasks/` Markdown files from earlier versions, and imports nothing from them (decision D4).
- There is no consolidation of completed work (decision D8).
