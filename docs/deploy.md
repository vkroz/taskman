# Deploying Taskman

Local development stays on Docker Compose. Google Cloud is only the hosted
copy, and GitHub Actions is the only thing that deploys it. A pull request
never talks to Google Cloud. Nothing in this repository has credentials that
could deploy from a laptop.

The Google Cloud project is `nemos-infra` in `us-central1`. The app image is
`us-central1-docker.pkg.dev/nemos-infra/taskman/taskman:<git sha>`.

## What a push to staging does

A push to `staging` runs the existing CI jobs first: tests on Python 3.11 and
3.12, then `docker build`. The deploy job starts only after both succeed, and
only when the event is a push to `refs/heads/staging`.

1. The job refuses to continue unless the event is that push. It does not
   request Google credentials before that check.
2. It authenticates as `taskman-deployer@nemos-infra.iam.gserviceaccount.com`
   through Workload Identity Federation. There is no service-account key.
   The provider is
   `projects/1037430670088/locations/global/workloadIdentityPools/github/providers/github-actions`.
   GitHub must present `id-token: write`. The provider accepts this repository
   only on `refs/heads/staging` and `refs/heads/main`.
3. It builds the image and pushes it, tagged with the full git SHA.
4. It creates or updates the Cloud Run job `taskman-migrate-staging` from that
   image and runs it to completion. The job uses the runtime service account
   `taskman-run-staging@nemos-infra.iam.gserviceaccount.com`, the Cloud SQL
   instance `nemos-infra:us-central1:nemos-pg`, and the secret
   `taskman-staging-db-password`. The command is `taskman migrate`.
5. If the migration fails, the workflow stops. The existing
   `taskman-staging` revision stays in place.
6. It deploys that image to the Cloud Run service `taskman-staging`. The
   service keeps Identity-Aware Proxy on (`--iap`) and does not allow public
   access (`--no-allow-unauthenticated`). It attaches the same Cloud SQL
   instance, runs as the staging runtime service account, and mounts
   `taskman-staging-db-password` and `taskman-staging-api-token`.
7. It checks that the Cloud Run Ready condition is True.
8. It calls `GET /health` and `GET /ready` on
   `https://taskman-staging-1037430670088.us-central1.run.app`.
   Both requests send two headers:
   `Proxy-Authorization: Bearer <Google access token>` for IAP, and
   `Authorization: Bearer <app token>` for Taskman. The Google token is the
   `access_token` output of `google-github-actions/auth` (`token_format:
   access_token`). The app token is read from Secret Manager. `/health` must
   return `{"status":"ok"}` without depending on the database. `/ready` must
   return the same body, which means Postgres accepted `SELECT 1`.

A second push to `staging` waits for the in-progress run to finish. The
workflow does not cancel a deploy that is already migrating.

## What a merge to main does

A push to `main`, including a merge, runs the same tests and image build, then
the same deploy job with production values.

1. It deploys the image tagged with that commit's SHA. It builds and pushes
   only when Artifact Registry does not already have that tag. A fast-forward
   that reuses a SHA already built from `staging` does not build again.
2. It runs `taskman-migrate-prod` before it updates `taskman-prod`. The job
   uses `taskman-run-prod@nemos-infra.iam.gserviceaccount.com`, database
   `taskman_prod`, and secret `taskman-prod-db-password`.
3. It deploys `taskman-prod` with IAP still on and public access still off,
   then checks the Ready condition.
4. It does **not** call `/health` or `/ready` on production. IAP in front of
   `taskman-prod` does not allow `taskman-deployer`, so that request would be
   rejected even when the app is healthy. Treating the rejection as success
   would also hide a real failure. The production check is the Cloud Run Ready
   condition only. Staging is where the HTTP smoke test runs.

The production URL is
`https://taskman-prod-1037430670088.us-central1.run.app`.

## Why a pull request does not deploy

The deploy job's `if` requires a push to `staging` or `main`. Pull requests
fail that condition, so the job is skipped and GitHub never issues an identity
token for it. A shell check in front of the auth step fails the job if a later
edit starts the job from any other event or ref. The Workload Identity
provider would reject those refs anyway.

## How the app reads its configuration

The container listens on `0.0.0.0:$PORT`. Cloud Run sets `PORT`. Locally and in
Compose the default is `8080`.

| Variable | Staging | Production |
| --- | --- | --- |
| `TASKMAN_DB_SOCKET` | `/cloudsql/nemos-infra:us-central1:nemos-pg` | same |
| `TASKMAN_DB_NAME` | `taskman_staging` | `taskman_prod` |
| `TASKMAN_DB_USER` | `taskman_staging` | `taskman_prod` |
| `TASKMAN_DB_PASSWORD` | secret `taskman-staging-db-password` | secret `taskman-prod-db-password` |
| `TASKMAN_ACCESS_TOKEN` | secret `taskman-staging-api-token` | secret `taskman-prod-api-token` |

`TASKMAN_DATABASE_URL`, when set, wins over the four socket variables. Compose
sets that URL and does not set the socket variables, so the local stack is
unchanged. The password's surrounding whitespace is stripped because a secret
often carries a trailing newline.

`taskman serve` still migrates on startup, under the same database lock, after
the job has already migrated. The job is what stops a bad migration from
replacing the live revision.

The workflow does not verify the `X-Goog-IAP-JWT-Assertion` header. Checking
that ES256 signature means fetching Google's IAP keys, pinning the audience,
and caching the keys. IAP already blocks unauthenticated callers. The app
keeps its own bearer token for the API.

## Permissions that must already exist

The workflow does not change IAM. If a deploy fails with a permission error,
these are the grants to check. The runtime accounts can already read their own
secrets.

`taskman-deployer@nemos-infra.iam.gserviceaccount.com` needs:

- Artifact Registry writer on the `taskman` repository, so it can push an
  image and check whether a SHA tag exists.
- Cloud Run developer, including creating, updating, and executing the two
  migration jobs and deploying the two services.
- `iam.serviceAccounts.actAs` (`roles/iam.serviceAccountUser`) on
  `taskman-run-staging` and `taskman-run-prod`.
- Secret Manager accessor on `taskman-staging-api-token` only. The smoke test
  reads that secret. The deployer does not need the database passwords or the
  production API token. The migration jobs and the services read those as the
  runtime accounts.
- Permission to deploy with `--iap`. The services already have IAP. The
  workflow does not edit the IAP member list.

Each runtime service account needs:

- `roles/cloudsql.client` on `nemos-infra:us-central1:nemos-pg`.
- Secret Manager accessor on that environment's password and API token
  secrets.

The Cloud Run service agent
`service-1037430670088@serverless-robot-prod.iam.gserviceaccount.com` needs
Artifact Registry reader on the `taskman` repository so Cloud Run can pull the
image. The IAP service agent
`service-1037430670088@gcp-sa-iap.iam.gserviceaccount.com` needs
`roles/run.invoker` on both services. Those bindings should already be there.
This workflow does not remove them.
