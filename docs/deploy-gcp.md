# Run the Taskman server on Google Cloud

Each push to `staging`, including each merged pull request, deploys the Taskman server to Google Cloud project `nemos-infra` in region `us-central1`. [ADR (Architecture Decision Record) 0001](adrs/0001-deploy-the-server-to-google-cloud.md) gives the design. [`deploy/gcp/nemos-infra.env`](../deploy/gcp/nemos-infra.env) names every resource. Agents and people use the `taskman` command-line interface (CLI) with a Taskman token. They need no Google Cloud credentials.

## How a deployment runs

The workflow [`deploy-nemos-infra.yml`](../.github/workflows/deploy-nemos-infra.yml):

1. Runs the checks of [`ci.yml`](../.github/workflows/ci.yml): the tests with a PostgreSQL 16 service container, a check of the deployment scripts, and the image build.
2. Signs in to Google Cloud as the service account `taskman-deployer` through Workload Identity Federation. The provider `taskman` admits only the repository `vkroz/taskman` on branch `staging`. GitHub stores no service account key.
3. Builds the image of [`taskman/Dockerfile`](../taskman/Dockerfile) and pushes it to `us-central1-docker.pkg.dev/nemos-infra/taskman/taskman`, tagged with the commit and `staging`.
4. Runs [`deploy/gcp/deploy.sh`](../deploy/gcp/deploy.sh) with the image digest. The Cloud Run job `taskman-db-upgrade` runs `taskman db upgrade`, and the script waits for it. Then the script deploys the Cloud Run service `taskman`.

One deployment runs at a time, in push order. A new push does not cancel a running deployment. A failure at any step stops the deployment. A failed check or build changes nothing in Google Cloud. A failed schema upgrade leaves the new image on the job only: the service keeps its previous image. The server listens only after it connects to the database and finds the current schema. Thus `gcloud run deploy` fails when the new revision cannot start, and the previous revision continues to serve.

After a fix, push to `staging`. To run a deployment again without a new commit, open its run in the Actions tab and select "Re-run all jobs". GitHub shows "Run workflow" only when the workflow file is on the default branch, and `main` has no deployment files.

The service runs `taskman serve` on port 8080, with 1 CPU (central processing unit), 512 MiB (mebibytes) of memory, 0 to 3 instances and a request timeout of 120 seconds. Each instance keeps one database connection and does not connect again after it breaks, for example after a Cloud SQL restart. `/readyz` then answers 503. A liveness probe calls `/readyz` every 30 seconds, and after 3 failures Cloud Run replaces the instance.

## Set up the environment once

A project owner of `nemos-infra` does these steps before the first deployment. You need the Google Cloud CLI (`gcloud`), signed in as an Owner, Python 3 (`python3`, which makes the database password), and a checkout that holds `deploy/gcp/`. Before the first deployment, this is the branch of the pull request that adds `deploy/gcp/`; after it, `staging`. Run the commands from the root of the checkout.

1. Create the resources. The script turns on the APIs (application programming interfaces) and creates the Artifact Registry repository, the service accounts and their roles, and the database and the user on the Cloud SQL instance `nemos-pg`. It also creates the secrets, Workload Identity Federation, and the public service with a placeholder image. It creates the instance too when it is missing. A repeated run creates only what is missing and never replaces a password.

   ```bash
   deploy/gcp/bootstrap.sh deploy/gcp/nemos-infra.env
   ```

   Cloud SQL gives the user `taskman` the `cloudsqlsuperuser` role, with `CREATEROLE` and `CREATEDB` on the whole instance ([About PostgreSQL users and roles](https://cloud.google.com/sql/docs/postgres/users)). Other applications share `nemos-pg`.

   When the secret `taskman-db-password` has no value but the database `taskman` or the user `taskman` exists on `nemos-pg`, the script stops. At that point, it has turned on the APIs and set up no resource. These names belong to another application: choose other `DB_NAME` and `DB_USER` values in the environment file, and run the script again.

   The service accepts calls without a Google identity: Cloud Run does not check the invoker, and Taskman checks the token of each call. If an organization policy blocks this setting, the script prints what to ask of an organization administrator.

   The script lists the providers of the pool `github`. The deployer trusts every provider of this pool that maps `attribute.repository`, also the earlier provider `github-actions`. Check that each listed condition admits only branches that you trust. `taskman-deployer` also keeps its earlier roles; `gcloud iam service-accounts get-iam-policy taskman-deployer@nemos-infra.iam.gserviceaccount.com --project nemos-infra` shows who can use it.

2. In GitHub, create the environment `nemos-infra` (Settings, Environments). Add the two variables that the script prints: `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_DEPLOYER_SERVICE_ACCOUNT`. Limit its deployment branches to `staging`.

3. Merge to `staging`. The first deployment creates the schema and replaces the placeholder image. If the files reached `staging` before this setup, the first run fails at the check of the one-time setup: open that run and select "Re-run all jobs".

The script also prints the address of the service and the OAuth (Open Authorization) redirect URI (Uniform Resource Identifier) for browser login. To show the address again:

```bash
gcloud run services describe taskman --project nemos-infra --region us-central1 --format 'value(status.url)'
```

The setup does not use or change the earlier resources: the services `taskman-staging` and `taskman-prod`, the databases `taskman_staging` and `taskman_prod`, their secrets, and the provider `github-actions`.

## Turn on browser login

Browser login lets each person get a token with a Google account. Create the OAuth client in Google Auth Platform, in the Google Cloud console of `nemos-infra`. You need the Owner role or OAuth Config Editor (`roles/oauthconfig.editor`).

1. Under Branding, enter the app name and a support email address.
2. Under Audience, select Internal only when each person signs in with an account of the Google Workspace organization of the project. For a personal Gmail account, or another account outside the organization, select External and add each person as a test user.
3. Under Clients, create a client of type "Web application". Add the redirect URI that `bootstrap.sh` printed, `<service address>/auth/callback`, as an authorized redirect URI.
4. Copy the client ID and the client secret.

Put the client secret into the secret `taskman-google-client-secret`, which `bootstrap.sh` created without a value. Do this before you set the client ID: a deployment with a client ID fails while this secret has no value. `read` waits for you to paste the client secret and press Enter. The terminal does not show it.

```bash
read -rs CLIENT_SECRET
printf %s "$CLIENT_SECRET" | gcloud secrets versions add taskman-google-client-secret --project nemos-infra --data-file -
unset CLIENT_SECRET
```

In `deploy/gcp/nemos-infra.env`, set `GOOGLE_CLIENT_ID` to the client ID. Also set `ALLOWED_DOMAINS`, `ALLOWED_EMAILS`, or both, as comma-separated lists without spaces. Merge the change to `staging`. The deployment turns on browser login.

`ALLOWED_DOMAINS` admits the members of a Google Workspace organization. A personal Gmail account has no Workspace domain: put its address in `ALLOWED_EMAILS`. `deploy.sh` stops before it changes anything when `GOOGLE_CLIENT_ID` is set and both lists are empty. To turn browser login off, empty `GOOGLE_CLIENT_ID` and merge to `staging`.

When browser login is on, anyone can open `/auth/token` and `/auth/cli` on the public service. Each request adds a row to the `logins` table under the database write lock, and Taskman does not limit the rate of these requests.

## Use the server

Each person installs the CLI from `staging`, because the CLI on `main` has no `auth` command. Then they log in on a computer with a browser:

```bash
uv tool install --force 'git+https://github.com/vkroz/taskman.git@staging#subdirectory=taskman'
taskman auth login --server <service address>
```

On a machine without a browser, open `<service address>/auth/token` on any computer and sign in. The page shows a personal access token one time. On the machine, run `taskman auth login --server <service address> --with-token` and paste the token. An agent sandbox can set `TASKMAN_SERVER_URL` and `TASKMAN_TOKEN` instead. Without browser login, an operator creates the token, as "Run operator commands" shows.

## Run operator commands

`taskman token list`, `taskman token revoke` and `taskman token create` open the database directly. Run them on your computer through the [Cloud SQL Auth Proxy version 2](https://github.com/GoogleCloudPlatform/cloud-sql-proxy), with [uv](https://docs.astral.sh/uv/) and a checkout of this repository. You need the roles Cloud SQL Client and Secret Manager Secret Accessor; the Owner role has them. The proxy uses your Application Default Credentials: run `gcloud auth application-default login` one time.

```bash
cloud-sql-proxy --port 15432 nemos-infra:us-central1:nemos-pg &
export TASKMAN_BACKEND=postgres
export TASKMAN_DATABASE_URL=postgresql://taskman@127.0.0.1:15432/taskman
export TASKMAN_DB_PASSWORD="$(gcloud secrets versions access latest --secret taskman-db-password --project nemos-infra)"
cd taskman
uv run --extra postgres taskman token list
```

In the same terminal, `uv run --extra postgres taskman token revoke ID` stops a token. `uv run --extra postgres taskman token create --person "ada@example.com"` creates a token in an emergency. When you are done:

```bash
cd ..
unset TASKMAN_BACKEND TASKMAN_DATABASE_URL TASKMAN_DB_PASSWORD
kill %1
```

- `uv run` uses the Taskman code of this checkout. Check out the commit of the deployed image: Taskman refuses a database with a different schema version.
- Do not run `token create` as a Cloud Run job. Cloud Logging keeps the output of a job, and the token would stay there.

## Check deployments

Each deployment is a run of the workflow **Deploy to nemos-infra** in the [Actions tab](https://github.com/vkroz/taskman/actions/workflows/deploy-nemos-infra.yml). The summary of a successful run names the commit and the image digest. A failed run names the failed step: the checks, the check of the one-time setup, the sign-in, the build, or the deploy. The log of the schema upgrade job is in Cloud Logging:

```bash
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="taskman-db-upgrade"' --project nemos-infra --limit 50 --format 'value(textPayload)'
```

The [Cloud Run page](https://console.cloud.google.com/run?project=nemos-infra) shows the image of the job and of the service. `curl -fsS <service address>/readyz` prints `{"ok":true}` when the server can query the database. `/readyz` needs no token. Do not use `/healthz` on Cloud Run: Google's front end reserves some paths that end in `z`, and it answers `/healthz` with its own 404 page. The request does not reach Taskman.

## Deploy by hand or roll back

`deploy.sh` deploys one image with your own `gcloud` sign-in. You need the roles of the deployer: Cloud Run Developer, Cloud SQL Viewer, and Service Account User on `taskman-server`. The Owner role has them. The script takes every setting from the environment file of your checkout, so deploy from a current checkout of `staging`.

To roll back, deploy an earlier image by its digest. The summary of each workflow run names the image as a full `...@sha256:...` reference. The column `IMAGE_DIGEST` of the revisions shows the same reference:

```bash
gcloud run revisions list --service taskman --project nemos-infra --region us-central1 --format 'table(metadata.name,status.imageDigest,metadata.creationTimestamp)'
deploy/gcp/deploy.sh deploy/gcp/nemos-infra.env <image reference>
```

Schema upgrades go forward only. With an image that has an older schema version than the database, the upgrade job fails, and the service keeps its image. Roll back only to an image with the current schema version. The next push to `staging` deploys its own image again.

## Another project or cloud

Another Google Cloud project needs its own environment file. Copy `deploy/gcp/nemos-infra.env`, change the names, run `bootstrap.sh` with the new file, and add a workflow like `deploy-nemos-infra.yml` that reads it.

The same image runs on another platform that gives:

- One container from the image, with environment variables.
- Secrets as files or as environment variables. Taskman reads both.
- PostgreSQL 16, through a connection URL (Uniform Resource Locator) or a unix socket.
- A one-time task that runs `taskman db upgrade` before a new image serves traffic.
- HTTPS (Hypertext Transfer Protocol Secure) in front of the server.

The table shows the equivalent services on AWS (Amazon Web Services). The Taskman maintainers have not tested the AWS path.

| Google Cloud | AWS |
| --- | --- |
| Artifact Registry | Amazon ECR (Elastic Container Registry) |
| Cloud Run service | Amazon ECS (Elastic Container Service) service on AWS Fargate |
| HTTPS address of the Cloud Run service | Application Load Balancer with an AWS Certificate Manager (ACM) certificate |
| Cloud SQL for PostgreSQL | Amazon RDS (Relational Database Service) for PostgreSQL |
| Secret Manager | AWS Secrets Manager |
| Cloud Run job for `db upgrade` | ECS task that `aws ecs run-task` starts |

On AWS, set `TASKMAN_DATABASE_URL` without the password, for example `postgresql://taskman@<RDS endpoint>:5432/taskman`. ECS gives a secret to the container as an environment variable, so put the password in `TASKMAN_DB_PASSWORD`.
