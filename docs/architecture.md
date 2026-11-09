# Taskman server architecture

Postgres is the system of record for backlog, active, and completed tasks, and for the events agents report. The web process is a gunicorn WSGI app (`taskman serve`) that listens on `0.0.0.0` and `PORT`. On startup it waits until Postgres accepts connections, then creates tables that are not already there.

The process does not open a git checkout, import `tasks/` files, or connect to an agent. Agents call the HTTP API through the `taskman` CLI. `taskman pull` is the one place a task is written into a repository, and that write happens in the CLI on the agent machine (`.taskman/tasks/<id>.md` and `.taskman/active`).

The flight-control page is static HTML plus a browser poll of `GET /api/dashboard` every three seconds. Rows come from the latest event on each task: who reported it, the event kind, and the commit, pull request, or completion detail.

Access is a single bearer token from `TASKMAN_ACCESS_TOKEN`. `GET /health` and `GET /ready` do not require it. `/health` is liveness and does not open a database connection. `/ready` is the readiness check and runs `SELECT 1`.

`POST /api/tasks/{id}/events` returns 201 when it inserts a row. When the post matches an event already stored — the same commit sha, or a completion that repeats the latest completion for the same agent and session — the response is 200 and that stored event. 200 keeps a retry successful. 409 would make the CLI and the hooks treat a stored event as a failure. Every event requires a non-empty agent: the author of the changes, not an agent that Taskman controls. The CLI takes it only from the required `--agent` argument. The Claude Code hook passes `claude-code`; the git post-commit hook passes the commit author.

Docker Compose runs that app next to Postgres and stores the database on the named volume `taskman-db`. The Compose file has no cloud-vendor fields. Cloud Run uses the same image and reads the database from `TASKMAN_DATABASE_URL` or from the Cloud SQL socket settings. [docs/deploy.md](deploy.md) describes that pipeline. `taskman migrate` applies the schema without starting the server.
