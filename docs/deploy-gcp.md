# Run the Taskman server on Google Cloud

This guide deploys the Taskman server to Cloud Run, with its database in Cloud SQL for PostgreSQL. After these steps, agents and people use the `taskman` command-line interface (CLI) with a Taskman token. They need no Google Cloud credentials.

You need:

- The Google Cloud CLI (`gcloud`), logged in as an Owner of the project.
- Docker, to build the image.
- A checkout of this repository. Run all commands from its root, in bash or zsh, on a MacBook or in Cloud Shell.

Without the Owner role, you need the admin roles for Service Usage, Artifact Registry, Cloud SQL, Secret Manager, service accounts, project IAM (Identity and Access Management) and Cloud Run. You also need `roles/iam.serviceAccountUser` on the runtime service account.

## What you create

- An Artifact Registry repository for the image.
- A Cloud SQL for PostgreSQL 16 instance. You can use an instance that exists.
- A database and a database user.
- Secret Manager secrets for the database password and the OAuth client secret.
- A runtime service account for the job and the service.
- A Cloud Run job that upgrades the database schema.
- A Cloud Run service that runs the server.
- A Google OAuth client for browser login.

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
gcloud config set project "$PROJECT_ID"
```

`IMAGE` names the image after the current git commit. Later steps set `CONNECTION_NAME`, `DB_ENV`, `DB_SECRET` and `URL`. In a new terminal, run those commands again too.

## Prepare the project

### Turn on the APIs

These are the application programming interfaces (APIs) that the steps use.

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

For PostgreSQL 16, Cloud SQL creates an Enterprise Plus instance when you do not set `--edition`. The shared-core tiers `db-f1-micro` and `db-g1-small` are available only in the Enterprise edition. For more capacity, set `--cpu` and `--memory` instead of `--tier`.

This guide uses an instance with a public IP address and no authorized networks. An instance with only a private IP address needs VPC (Virtual Private Cloud) access from Cloud Run. This guide does not cover that.

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
gcloud sql users create "$DB_USER" --instance="$INSTANCE" \
  --password="$(gcloud secrets versions access latest --secret=taskman-db-password)"
```

Python makes a random password and sends it to Secret Manager through a pipe. The terminal and the shell history do not show the password.

### Create the runtime service account

The Cloud Run job and the service run as this account. It can connect to Cloud SQL and read the Taskman secrets only.

```sh
gcloud iam service-accounts create "$SA_NAME" --display-name="Taskman server"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/cloudsql.client --condition=None
gcloud secrets add-iam-policy-binding taskman-db-password \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor
```

## Build and push the image

Cloud Run needs a `linux/amd64` image. On a MacBook with Apple silicon, Docker builds `linux/arm64` images unless you set the platform.

```sh
gcloud auth configure-docker "$REGION-docker.pkg.dev"
docker build --platform linux/amd64 -t "$IMAGE" taskman
docker push "$IMAGE"
```

Without Docker, turn on `cloudbuild.googleapis.com` and run `gcloud builds submit taskman --tag "$IMAGE"`. In a new project, Cloud Build runs as the Compute Engine default service account; if the build fails with a permission error, grant that account `roles/cloudbuild.builds.builder`.

## Upgrade the schema

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

## Deploy the service

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

- `--no-invoker-iam-check` lets every caller reach the server. Taskman checks the token of each call itself, so agents need no Google credentials. Google recommends this flag instead of `--allow-unauthenticated`, which fails when the domain restricted sharing organization policy blocks `allUsers`.
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

Each command prints `{"ok":true}`. `/healthz` answers while the process runs. `/readyz` answers 503 when the server cannot query the database. These two addresses need no token. Each call under `/api/v1/` needs a token: turn on browser login, or create a token as "Run operator commands" shows.

## Turn on browser login

Browser login lets each person get a token with a Google account. Create the OAuth client in the Google Cloud console:

1. Open Google Auth Platform. Under Branding, enter the app name and a support email address.
2. Under Audience, select Internal if the project belongs to a Google Workspace organization. Then only members of the organization can sign in. Otherwise, select External and add each person as a test user.
3. Under Clients, create a client of type "Web application". Add the output of `echo "$URL/auth/callback"` as an authorized redirect URI.
4. Copy the client ID and the client secret.

Store the client secret and update the service. `read` waits for you to paste the client secret and press Enter. The terminal does not show the secret.

```sh
CLIENT_ID=example-client-id.apps.googleusercontent.com
read -rs CLIENT_SECRET
printf %s "$CLIENT_SECRET" | gcloud secrets create taskman-google-client-secret --data-file=-
unset CLIENT_SECRET
gcloud secrets add-iam-policy-binding taskman-google-client-secret \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor
gcloud run services update "$SERVICE" --region="$REGION" \
  --update-secrets=/secrets/google/client-secret=taskman-google-client-secret:latest \
  --update-env-vars="TASKMAN_GOOGLE_CLIENT_ID=$CLIENT_ID,TASKMAN_GOOGLE_CLIENT_SECRET_FILE=/secrets/google/client-secret,TASKMAN_PUBLIC_URL=$URL,TASKMAN_ALLOWED_DOMAINS=example.com"
```

Set `TASKMAN_ALLOWED_DOMAINS` to your Google Workspace domain, or set `TASKMAN_ALLOWED_EMAILS` to the addresses of the people. For more than one value, change the delimiter of the flag, as `gcloud topic escaping` shows.

On a computer with a browser, each person logs in:

```sh
taskman auth login --server "$URL"
```

On a machine without a browser, open `$URL/auth/token` on any computer and sign in. The page shows a personal access token. On the machine, run `taskman auth login --server "$URL" --with-token` and paste the token. An agent sandbox can set `TASKMAN_SERVER_URL` and `TASKMAN_TOKEN` instead.

## Run operator commands

`taskman token list`, `taskman token revoke` and `taskman token create` open the database directly. Run them on your computer through the Cloud SQL Auth Proxy:

1. Install the Cloud SQL Auth Proxy version 2 from <https://github.com/GoogleCloudPlatform/cloud-sql-proxy>.
2. The proxy uses your Application Default Credentials. On a MacBook, run `gcloud auth application-default login` one time.

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

Build the new image, upgrade the schema, then deploy the service. The job and the service keep their other settings.

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

Schema upgrades go forward only. A revision with an older schema version than the database does not start. Roll back only to a revision with the current schema version. After you deploy a fixed image, send the traffic to the latest revision again:

```sh
gcloud run services update-traffic "$SERVICE" --region="$REGION" --to-latest
```

## Other clouds

The image runs on a platform that gives:

- One container from the image, with environment variables and secret files.
- PostgreSQL 16, through a connection URL or a unix socket.
- A one-time task that runs `taskman db upgrade` before a new image serves traffic.
- HTTPS in front of the server.

| Google Cloud | AWS (Amazon Web Services) |
| --- | --- |
| Artifact Registry | Amazon ECR (Elastic Container Registry) |
| Cloud Run service | Amazon ECS (Elastic Container Service) service on AWS Fargate |
| HTTPS address of the Cloud Run service | Application Load Balancer with an AWS Certificate Manager certificate |
| Cloud SQL for PostgreSQL | Amazon RDS (Relational Database Service) for PostgreSQL |
| Secret Manager | AWS Secrets Manager |
| Cloud Run job for `db upgrade` | ECS task that `aws ecs run-task` starts |

On AWS, set `TASKMAN_DATABASE_URL` without the password, for example `postgresql://taskman@<RDS endpoint>:5432/taskman`. Put the password in `TASKMAN_DB_PASSWORD`, because ECS gives a secret to the container as an environment variable. On a platform that mounts secrets as files, use `TASKMAN_DB_PASSWORD_FILE`.
