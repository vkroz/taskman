# Taskman server architecture

Postgres is the system of record for backlog, active, and completed tasks, and for the events agents report. The web process is a gunicorn WSGI app (`taskman serve`) that listens on `0.0.0.0` and `PORT`. On startup it waits until Postgres accepts connections, then creates tables that are not already there.

The process does not open a git checkout, import `tasks/` files, or connect to an agent. Agents call the HTTP API through the `taskman` CLI. `taskman pull` is the one place a task is written into a repository, and that write happens in the CLI on the agent machine (`.taskman/tasks/<id>.md` and `.taskman/active`).

The flight-control page is static HTML plus a browser poll of `GET /api/dashboard` every three seconds. Rows come from the latest event on each task: who reported it, the event kind, and the commit, pull request, or completion detail.

Access is a single bearer token from `TASKMAN_ACCESS_TOKEN`. `GET /health` does not require it and runs `SELECT 1`.

Docker Compose runs that app next to Postgres and stores the database on the named volume `taskman-db`. The Compose file has no cloud-vendor fields. Hosting it somewhere else means running the same two containers and injecting the same environment variables.
