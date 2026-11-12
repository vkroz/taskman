# Run the Taskman server on Google Cloud

This guide deploys the Taskman server to Cloud Run, with its database in Cloud SQL for PostgreSQL. After these steps, agents and people use the `taskman` command-line interface (CLI) with a Taskman token. They need no Google Cloud credentials.

There are two deploy paths. The main path is GitHub Actions: each push to the `staging` branch tests, builds and deploys the server. The other path deploys by hand with `gcloud` commands. Both paths use the resources that "Prepare the project" creates.

You need:

- A Google Cloud project with billing turned on.
- The Google Cloud CLI (`gcloud`), logged in as an Owner of the project.
- A checkout of this repository. Run all commands from its root, in bash or zsh, on a MacBook or in Cloud Shell.
- Python 3.11 or later (`python3`). The setup steps use it to make the database password.
- For GitHub Actions: admin access to the GitHub repository, to set its variables.
- To deploy by hand: Docker, to build the image.
- For operator commands: [uv](https://docs.astral.sh/uv/) and the [Cloud SQL Auth Proxy version 2](https://github.com/GoogleCloudPlatform/cloud-sql-proxy).

Without the Owner role, you need the admin roles for Service Usage, Artifact Registry, Cloud SQL, Secret Manager, service accounts, project IAM (Identity and Access Management) and Cloud Run. You also need `roles/iam.serviceAccountUser` on the runtime service account. To create the OAuth (Open Authorization) client, you need OAuth Config Editor (`roles/oauthconfig.editor`). For GitHub Actions, you also need Workload Identity Pool Admin (`roles/iam.workloadIdentityPoolAdmin`).

The database user that this guide creates gets the `cloudsqlsuperuser` role, with the `CREATEROLE` and `CREATEDB` attributes on the whole instance ([About PostgreSQL users and roles](https://cloud.google.com/sql/docs/postgres/users)). On an instance that other applications share, make sure that the owners of the instance accept this.

## What you create

- An Artifact Registry repository for the image.
- A Cloud SQL for PostgreSQL 16 instance. You can use an instance that exists.
- A database and a database user.
- Secret Manager secrets for the database password and the OAuth client secret.
- A runtime service account for the job and the service.
- A Cloud Run job that upgrades the database schema.
- A Cloud Run service that runs the server.
- A Google OAuth client for browser login.
- For GitHub Actions: a Workload Identity Federation pool and provider, and a deployer service account.

## Set the variables

Replace the example values with yours. Set the variables again in each new terminal.

```sh
PROJECT_ID=example-project
REGION=us-central1
INSTANCE=taskman-db
DB_NAME=taskman
DB_USER=taskman
REPO=taskman
SERVICE=taskman
JOB=taskman-db-upgrade
SA_NAME=taskman-server
RUNTIME_SA="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/taskman:$(git rev-parse --short HEAD)"
ALLOWED_DOMAINS=example.com
ALLOWED_EMAILS=
gcloud config set project "$PROJECT_ID"
```

`IMAGE` names the image after the current git commit. The name does not show uncommitted changes, so build only from a clean tree: `git status --short` must print nothing.

`ALLOWED_DOMAINS` and `ALLOWED_EMAILS` are for browser login. They are comma-separated lists of Google Workspace domains and of email addresses that may sign in. Set one of them or both.

Later steps set `CONNECTION_NAME`, `DB_ENV`, `DB_SECRET` and `URL`. In a new terminal, run those commands again too.

## Prepare the project

### Turn on the Google Cloud services

The steps use these application programming interfaces (APIs).

```sh
gcloud services enable run.googleapis.com sqladmin.googleapis.com \
  secretmanager.googleapis.com artifactregistry.googleapis.com iam.googleapis.com
```

### Create the image repository

```sh
gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION"
```

### Create the Cloud SQL instance

```sh
gcloud sql instances create "$INSTANCE" \
  --database-version=POSTGRES_16 \
  --edition=enterprise \
  --tier=db-g1-small \
  --region="$REGION"
```

For PostgreSQL 16, Cloud SQL creates an Enterprise Plus instance when you do not set `--edition`. The shared-core tiers `db-f1-micro` and `db-g1-small` are available only in the Enterprise edition ([Cloud SQL editions](https://cloud.google.com/sql/docs/postgres/editions-intro)). For more capacity, set `--cpu` and `--memory` instead of `--tier`.

This guide uses an instance with a public IP (Internet Protocol) address and no authorized networks. An instance with only a private IP address needs VPC (Virtual Private Cloud) access from Cloud Run. This guide does not cover that.

To use an instance that exists, do not run the command above. Set `INSTANCE` to its name. The instance must run PostgreSQL 16. If it is in another project, add `--project` with that project to the `gcloud sql` commands, and grant the Cloud SQL Client role in that project.

Get the connection name of the instance:

```sh
CONNECTION_NAME="$(gcloud sql instances describe "$INSTANCE" --format='value(connectionName)')"
```

### Create the database, the user and the password secret

```sh
gcloud sql databases create "$DB_NAME" --instance="$INSTANCE"
python3 -c "import secrets; print(secrets.token_urlsafe(24), end='')" |
  gcloud secrets create taskman-db-password --data-file=-
CLOUDSDK_CORE_DISABLE_FILE_LOGGING=1 gcloud sql users create "$DB_USER" --instance="$INSTANCE" \
  --password="$(gcloud secrets versions access latest --secret=taskman-db-password)"
```

Python makes a random password and sends it to Secret Manager through a pipe. The terminal and the shell history do not show the password. While `gcloud sql users create` runs, the process list of your computer shows the password. `CLOUDSDK_CORE_DISABLE_FILE_LOGGING=1` stops gcloud from writing the command, with the password, to its log files in `~/.config/gcloud/logs`.

### Create the runtime service account

The Cloud Run job and the service run as this account. It can connect to Cloud SQL and read the Taskman secrets only.

```sh
gcloud iam service-accounts create "$SA_NAME" --display-name="Taskman server"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/cloudsql.client --condition=None
gcloud secrets add-iam-policy-binding taskman-db-password \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor
```

A new service account can take about one minute to become usable. If a binding fails because the account does not exist, wait one minute and run the binding again.

## Deploy with GitHub Actions

This is the main deploy path. The workflow "CI" (continuous integration) in `.github/workflows/ci.yml` has two jobs:

- `test` runs on each pull request to `staging`, on each push to `staging`, and when you start the workflow by hand. It runs the test suite against a PostgreSQL 16 container, then builds the image. A pull request gets only this job.
- `deploy` runs after `test` passes, on a push to `staging` or on a run that you start on `staging`. The job is skipped while the repository variable `GCP_PROJECT_ID` is empty.

The `deploy` job does these steps:

1. It gets access to Google Cloud as the deployer service account, through Workload Identity Federation. GitHub gives the job a short-lived identity token, and Google Cloud accepts that token. GitHub stores no service account key.
2. It builds the image for `linux/amd64` and pushes it to Artifact Registry. The image tag is the commit hash.
3. It deploys the Cloud Run job `<service>-db-upgrade` with the new image and runs it. A failed upgrade stops the deploy.
4. It deploys the service with the new image and with the settings of "Deploy the service".
5. It calls `/healthz` and `/readyz` of the service, with retries. The deploy fails if they do not answer with status 200.

The runs of `staging` go one at a time, in push order, so an older commit cannot deploy after a newer one. A new push does not cancel a running deploy. When several pushes wait, only the newest runs. The workflow does not change IAM policies, and it deploys only from `staging`. There is no deploy from `main`.

Each deploy sets the image, the environment variables and the secrets of the job and the service from the repository variables. It replaces the changes that you make to these settings by hand.

### Set up GitHub Actions one time

Do the steps of "Prepare the project" first. Then set these variables too:

```sh
GITHUB_REPO=example-owner/taskman
POOL=github
PROVIDER=taskman-staging
DEPLOYER_SA_NAME=taskman-deployer
DEPLOYER_SA="$DEPLOYER_SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
```

Create a Workload Identity Federation pool and an OIDC (OpenID Connect) provider for GitHub. The provider accepts only tokens of this repository on `refs/heads/staging`. If your organization has a provider that already trusts this repository on `refs/heads/staging`, set `POOL` and `PROVIDER` to its IDs. Then do not run the `create` and `create-oidc` commands. The provider must map `attribute.repository=assertion.repository`, because the binding of the deployer service account uses this attribute. If the pool is in another project, add `--project` with that project to the two `describe` commands.

```sh
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com
gcloud iam workload-identity-pools create "$POOL" --location=global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --location=global --workload-identity-pool="$POOL" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == '$GITHUB_REPO' && assertion.ref == 'refs/heads/staging'"
POOL_NAME="$(gcloud iam workload-identity-pools describe "$POOL" --location=global --format='value(name)')"
WIF_PROVIDER="$(gcloud iam workload-identity-pools providers describe "$PROVIDER" \
  --location=global --workload-identity-pool="$POOL" --format='value(name)')"
```

Create the deployer service account. It can deploy Cloud Run jobs and services, push images to the image repository, and run the job and the service as the runtime service account. The last command lets the workflows of this repository use the deployer service account.

```sh
gcloud iam service-accounts create "$DEPLOYER_SA_NAME" --display-name="Taskman deployer"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$DEPLOYER_SA" --role=roles/run.developer --condition=None
gcloud artifacts repositories add-iam-policy-binding "$REPO" --location="$REGION" \
  --member="serviceAccount:$DEPLOYER_SA" --role=roles/artifactregistry.writer
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:$DEPLOYER_SA" --role=roles/iam.serviceAccountUser
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" \
  --member="principalSet://iam.googleapis.com/$POOL_NAME/attribute.repository/$GITHUB_REPO" \
  --role=roles/iam.workloadIdentityUser
```

If a binding fails because the new account does not exist, wait one minute and run the binding again. The runtime service account keeps its roles: Cloud SQL Client on the project, and Secret Manager Secret Accessor on each secret that it mounts. "Turn on browser login" gives it access to the OAuth client secret.

Create the service one time with a placeholder image, and make it public. The first deploy of the workflow replaces the image. If an organization policy requires the invoker check, this command fails. Ask your organization administrator for an exception for this project.

```sh
gcloud run deploy "$SERVICE" --image=us-docker.pkg.dev/cloudrun/container/hello \
  --region="$REGION" --service-account="$RUNTIME_SA" --no-invoker-iam-check
URL="$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"
```

`--no-invoker-iam-check` needs the permission `run.services.setIamPolicy` ([Allowing public access](https://cloud.google.com/run/docs/authenticating/public)). The Cloud Run Admin role has it, and the Cloud Run Developer role of the deployer does not. A later `gcloud run deploy` without the flag keeps the setting, so the workflow does not use the flag. If the "Deploy service" step of the workflow fails with a permission error for `run.services.setIamPolicy`, give the deployer the Cloud Run Admin role on this service only: `gcloud run services add-iam-policy-binding "$SERVICE" --region="$REGION" --member="serviceAccount:$DEPLOYER_SA" --role=roles/run.admin`.

Set the repository variables. In GitHub, open Settings > Secrets and variables > Actions > Variables. You can also use the GitHub CLI (`gh`):

```sh
gh variable set GCP_REGION --repo "$GITHUB_REPO" --body "$REGION"
gh variable set GCP_WIF_PROVIDER --repo "$GITHUB_REPO" --body "$WIF_PROVIDER"
gh variable set GCP_DEPLOYER_SA --repo "$GITHUB_REPO" --body "$DEPLOYER_SA"
gh variable set GCP_RUNTIME_SA --repo "$GITHUB_REPO" --body "$RUNTIME_SA"
gh variable set GCP_CLOUDSQL_INSTANCE --repo "$GITHUB_REPO" --body "$CONNECTION_NAME"
gh variable set GCP_PROJECT_ID --repo "$GITHUB_REPO" --body "$PROJECT_ID"
```

Set `GCP_PROJECT_ID` last: the `deploy` job runs only when it is set. The variables hold names and addresses only. The passwords stay in Secret Manager.

If you changed `REPO`, `SERVICE`, `DB_NAME` or `DB_USER`, or use another secret for the database password, also set the optional variables of the table. The workflow names the job `<service>-db-upgrade`; it does not use `JOB`.

| Variable | Need | Default | Value |
| --- | --- | --- | --- |
| `GCP_PROJECT_ID` | Required | | The project ID. |
| `GCP_REGION` | Required | | The region of the image repository, the job and the service. |
| `GCP_WIF_PROVIDER` | Required | | The full name of the provider: `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/POOL/providers/PROVIDER`. |
| `GCP_DEPLOYER_SA` | Required | | The email address of the deployer service account. |
| `GCP_RUNTIME_SA` | Required | | The email address of the runtime service account. |
| `GCP_CLOUDSQL_INSTANCE` | Required | | The connection name of the instance: `PROJECT:REGION:INSTANCE`. |
| `GCP_ARTIFACT_REPO` | Optional | `taskman` | The Artifact Registry repository. |
| `TASKMAN_SERVICE` | Optional | `taskman` | The Cloud Run service. The job is `<service>-db-upgrade`. |
| `TASKMAN_DB_NAME` | Optional | `taskman` | The database. |
| `TASKMAN_DB_USER` | Optional | `taskman` | The database user. |
| `TASKMAN_DB_PASSWORD_SECRET` | Optional | `taskman-db-password` | The Secret Manager secret that holds the database password. |
| `TASKMAN_GOOGLE_CLIENT_ID` | Browser login | | The OAuth client ID. Browser login is on only when this variable is set. |
| `TASKMAN_GOOGLE_CLIENT_SECRET_SECRET` | Browser login | `taskman-google-client-secret` | The Secret Manager secret that holds the OAuth client secret. |
| `TASKMAN_PUBLIC_URL` | Browser login | | The address of the service, the value of `URL`. |
| `TASKMAN_ALLOWED_DOMAINS` | Browser login | | Comma-separated Google Workspace domains. |
| `TASKMAN_ALLOWED_EMAILS` | Browser login | | Comma-separated email addresses. Set this variable, `TASKMAN_ALLOWED_DOMAINS`, or both. |

### Run the workflow

Merge a pull request into `staging`, or push to `staging`. To deploy `staging` again without a new commit, open the Actions tab in GitHub, select "CI", then "Run workflow", and select the `staging` branch. GitHub shows "Run workflow" only when `.github/workflows/ci.yml` is on the default branch of the repository. Otherwise, push a commit to `staging`. On another branch, the workflow runs only the tests.

## Deploy by hand

Use these steps when you do not use GitHub Actions. If you use GitHub Actions, its next deploy replaces the image, the environment variables and the secrets that you set by hand.

### Build and push the image

Cloud Run needs a `linux/amd64` image. On a MacBook with Apple silicon, Docker builds `linux/arm64` images unless you set the platform.

```sh
gcloud auth configure-docker "$REGION-docker.pkg.dev"
docker build --platform linux/amd64 -t "$IMAGE" taskman
docker push "$IMAGE"
```

### Upgrade the schema

The server does not start when the database schema is older than its code. A Cloud Run job runs `taskman db upgrade` with the same image, database connection and secret. The job creates the schema in an empty database and does nothing when the schema is current. Run it before every deploy of a new image.

```sh
DB_ENV="TASKMAN_BACKEND=postgres,TASKMAN_DB_SOCKET=/cloudsql/$CONNECTION_NAME,TASKMAN_DB_NAME=$DB_NAME,TASKMAN_DB_USER=$DB_USER,TASKMAN_DB_PASSWORD_FILE=/secrets/db/password"
DB_SECRET="/secrets/db/password=taskman-db-password:latest"
gcloud run jobs deploy "$JOB" \
  --image="$IMAGE" \
  --args=db,upgrade \
  --region="$REGION" \
  --service-account="$RUNTIME_SA" \
  --set-cloudsql-instances="$CONNECTION_NAME" \
  --set-env-vars="$DB_ENV" \
  --set-secrets="$DB_SECRET" \
  --max-retries=0
gcloud run jobs execute "$JOB" --region="$REGION" --wait
```

- `--set-cloudsql-instances` makes Cloud Run open the Cloud SQL unix socket in the directory `/cloudsql/<connection name>`.
- `--set-secrets` mounts the password as the file `/secrets/db/password`.
- `--max-retries=0` stops the job after one failure. The upgrade runs in one transaction, so a failed upgrade changes nothing.
- `--wait` returns when the upgrade is complete. The command fails when the upgrade fails.

### Deploy the service

```sh
gcloud run deploy "$SERVICE" \
  --image="$IMAGE" \
  --region="$REGION" \
  --service-account="$RUNTIME_SA" \
  --set-cloudsql-instances="$CONNECTION_NAME" \
  --set-env-vars="$DB_ENV" \
  --set-secrets="$DB_SECRET" \
  --no-invoker-iam-check \
  --cpu=1 \
  --memory=512Mi \
  --max-instances=3 \
  --liveness-probe=httpGet.path=/readyz,httpGet.port=8080,periodSeconds=30,timeoutSeconds=5,failureThreshold=3
```

The image runs `taskman serve --host 0.0.0.0` on the port in `PORT`, which Cloud Run sets to 8080.

- `--no-invoker-iam-check` lets every caller reach the server ([Cloud Run access control](https://cloud.google.com/run/docs/securing/managing-access)). Taskman checks the token of each call itself, so agents need no Google credentials. Google recommends this flag instead of `--allow-unauthenticated`, which fails when the domain restricted sharing organization policy blocks `allUsers`.
- Do not turn on IAP (Identity-Aware Proxy). IAP requires a Google identity for each call, and agent machines have none.
- `--max-instances=3`: each instance keeps one database connection. The limit keeps the number of connections and the cost low.
- `--cpu=1 --memory=512Mi`: the server used approximately 50 MB of memory in a local test.
- `--liveness-probe` calls `/readyz` every 30 seconds. The server keeps one database connection and does not connect again after it breaks, for example after a Cloud SQL restart. `/readyz` then answers 503. After 3 failures, Cloud Run replaces the instance, and the new instance connects to the database.

The default startup probe of Cloud Run waits until the server listens on its port. The server listens only after it connects to the database and finds the current schema. Thus a deploy without the schema upgrade fails, and the previous revision continues to serve.

If an organization policy also requires the invoker check, the deploy fails. Ask your organization administrator for an exception for this project.

## Check the server

```sh
URL="$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"
curl -fsS "$URL/healthz"
curl -fsS "$URL/readyz"
```

`URL` is the address of the service, a URL (Uniform Resource Locator) that starts with `https://`. Each command prints `{"ok":true}`. `/healthz` answers while the process runs. `/readyz` answers 503 when the server cannot query the database. These two addresses need no token. Each call under `/api/v1/` needs a token: turn on browser login, or create a token as "Run operator commands" shows.

## Turn on browser login

Browser login lets each person get a token with a Google account. Create the OAuth client in Google Auth Platform, in the Google Cloud console:

1. Open Google Auth Platform. Under Branding, enter the app name and a support email address.
2. Under Audience, select Internal if the project belongs to a Google Workspace organization. Then only members of the organization can sign in. Otherwise, select External and add each person as a test user.
3. Under Clients, create a client of type "Web application". Add the output of `echo "$URL/auth/callback"` as an authorized redirect URI (Uniform Resource Identifier).
4. Copy the client ID and the client secret.

Store the client secret, and let the runtime service account read it. Before you run the commands, replace the example client ID with yours. `read` waits for you to paste the client secret and press Enter. The terminal does not show the secret.

```sh
CLIENT_ID=example-client-id.apps.googleusercontent.com
read -rs CLIENT_SECRET
printf %s "$CLIENT_SECRET" | gcloud secrets create taskman-google-client-secret --data-file=-
unset CLIENT_SECRET
gcloud secrets add-iam-policy-binding taskman-google-client-secret \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor
```

With GitHub Actions, set the browser login variables of the table in "Set up GitHub Actions one time", then run the workflow. To deploy by hand, update the service:

```sh
gcloud run services update "$SERVICE" --region="$REGION" \
  --update-secrets=/secrets/google/client-secret=taskman-google-client-secret:latest \
  --update-env-vars="^;^TASKMAN_GOOGLE_CLIENT_ID=$CLIENT_ID;TASKMAN_GOOGLE_CLIENT_SECRET_FILE=/secrets/google/client-secret;TASKMAN_PUBLIC_URL=$URL;TASKMAN_ALLOWED_DOMAINS=$ALLOWED_DOMAINS;TASKMAN_ALLOWED_EMAILS=$ALLOWED_EMAILS"
```

`^;^` at the start makes `;` the separator between the variables, so a value can hold a comma-separated list. `gcloud topic escaping` explains this syntax.

When browser login is on, anyone can open `/auth/token` and `/auth/cli` on the public service. Each request adds a row to the `logins` table under the database write lock, and Taskman does not limit the rate of these requests.

Each person needs a `taskman` CLI from the branch that you deploy, because an older CLI has no `auth` command. For example, with the install command of the README, add `@staging` after `taskman.git`. On a computer with a browser, each person logs in:

```sh
taskman auth login --server "$URL"
```

On a machine without a browser, open `$URL/auth/token` on any computer and sign in. The page shows a personal access token. On the machine, run `taskman auth login --server "$URL" --with-token` and paste the token. An agent sandbox can set `TASKMAN_SERVER_URL` and `TASKMAN_TOKEN` instead.

## Run operator commands

`taskman token list`, `taskman token revoke` and `taskman token create` open the database directly. Run them on your computer through the Cloud SQL Auth Proxy. The proxy uses your Application Default Credentials. On a MacBook, run `gcloud auth application-default login` one time.

```sh
cloud-sql-proxy --port 15432 "$CONNECTION_NAME" &
export TASKMAN_BACKEND=postgres
export TASKMAN_DATABASE_URL="postgresql://$DB_USER@127.0.0.1:15432/$DB_NAME"
export TASKMAN_DB_PASSWORD="$(gcloud secrets versions access latest --secret=taskman-db-password)"
cd taskman
uv run --extra postgres taskman token list
```

In the same terminal, `uv run --extra postgres taskman token revoke ID` stops a token. `uv run --extra postgres taskman token create --person "ada@example.com"` creates a token in an emergency. When you are done:

```sh
cd ..
unset TASKMAN_BACKEND TASKMAN_DATABASE_URL TASKMAN_DB_PASSWORD
kill %1
```

- `uv run` uses the Taskman code of this checkout. Check out the commit of the deployed image: Taskman refuses a database with a different schema version.
- Do not run `token create` as a Cloud Run job. Cloud Logging keeps the output of a job, and the token would stay there.

## Deploy a new version

With GitHub Actions, merge or push to `staging`: the workflow does these steps. By hand, build the new image, upgrade the schema, then deploy the service. The job and the service keep their other settings.

```sh
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/taskman:$(git rev-parse --short HEAD)"
docker build --platform linux/amd64 -t "$IMAGE" taskman
docker push "$IMAGE"
gcloud run jobs update "$JOB" --image="$IMAGE" --region="$REGION"
gcloud run jobs execute "$JOB" --region="$REGION" --wait
gcloud run deploy "$SERVICE" --image="$IMAGE" --region="$REGION"
```

Deploy the service immediately after the job. When the job applied a new schema version, a new instance of the previous image cannot start.

## Roll back

Send all traffic to an earlier revision. Find its name in the list and set `PREVIOUS` to it:

```sh
gcloud run revisions list --service="$SERVICE" --region="$REGION"
PREVIOUS=taskman-00001-abc
gcloud run services update-traffic "$SERVICE" --region="$REGION" --to-revisions="$PREVIOUS=100"
```

Schema upgrades go forward only. A revision with an older schema version than the database does not start. Roll back only to a revision with the current schema version.

While the traffic goes to `PREVIOUS`, a new deploy creates a revision that gets no traffic. The smoke test of the workflow then fails. After you deploy a fixed image, send the traffic to the latest revision again:

```sh
gcloud run services update-traffic "$SERVICE" --region="$REGION" --to-latest
```

## Other clouds

The image runs on a platform that gives:

- One container from the image, with environment variables.
- Secrets as files or as environment variables. Taskman reads both.
- PostgreSQL 16, through a connection URL or a unix socket.
- A one-time task that runs `taskman db upgrade` before a new image serves traffic.
- HTTPS (Hypertext Transfer Protocol Secure) in front of the server.

The table shows the equivalent services on AWS (Amazon Web Services). The Taskman maintainers have not tested the AWS path.

| Google Cloud | AWS |
| --- | --- |
| Artifact Registry | Amazon ECR (Elastic Container Registry) |
| Cloud Run service | Amazon ECS (Elastic Container Service) service on AWS Fargate |
| HTTPS address of the Cloud Run service | Application Load Balancer with an AWS Certificate Manager certificate |
| Cloud SQL for PostgreSQL | Amazon RDS (Relational Database Service) for PostgreSQL |
| Secret Manager | AWS Secrets Manager |
| Cloud Run job for `db upgrade` | ECS task that `aws ecs run-task` starts |

On AWS, set `TASKMAN_DATABASE_URL` without the password, for example `postgresql://taskman@<RDS endpoint>:5432/taskman`. ECS gives a secret to the container as an environment variable, so put the password in `TASKMAN_DB_PASSWORD`.
