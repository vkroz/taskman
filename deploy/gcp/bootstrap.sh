#!/usr/bin/env bash
# Set up the Google Cloud resources of a Taskman environment once, before the first
# deployment. Run it as a project owner:
#
#   deploy/gcp/bootstrap.sh deploy/gcp/nemos-infra.env
#
# A repeated run creates only what is missing and never replaces a password. It
# creates: the APIs, the Artifact Registry repository, the service accounts and their
# roles, the Cloud SQL instance, the database and its user, the Secret Manager
# secrets, Workload Identity Federation for the GitHub deployment, and the public
# Cloud Run service with a placeholder image. The first deployment replaces the
# placeholder.
#
# The database and the user get the password of the secret. When the secret has no
# password yet but the database or the user already exists, these names belong to
# something else: the script stops before it sets up any resource.
#
# It prints the two GitHub variables of the deployment workflow, the service URL, and
# the OAuth redirect URI at the end. docs/deploy-gcp.md, "Set up the environment
# once", gives the order of the setup steps.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 CONFIG" >&2
  exit 2
fi
config=$1
# shellcheck source=deploy/gcp/nemos-infra.env
source "$config"
# The GitHub environment has the name of the configuration file.
environment=$(basename "$config" .env)

project=(--project "$PROJECT_ID")
instance=(--instance "$SQL_INSTANCE" "${project[@]}")
common=(--project "$PROJECT_ID" --region "$REGION" --quiet)
placeholder=us-docker.pkg.dev/cloudrun/container/hello
account() { echo "$1@$PROJECT_ID.iam.gserviceaccount.com"; }
member() { echo "serviceAccount:$(account "$1")"; }
exists() { "$@" >/dev/null 2>&1; }
password() { python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")'; }
# enabled_version NAME: the name of an enabled version of the secret, or nothing.
enabled_version() {
  # stderr: gcloud warns about the filter key when the secret has no version yet.
  gcloud secrets versions list "$1" "${project[@]}" --filter state=ENABLED \
    --limit 1 --format 'value(name)' 2>/dev/null
}

echo "Enabling APIs"
gcloud services enable "${project[@]}" \
  run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  artifactregistry.googleapis.com iam.googleapis.com iamcredentials.googleapis.com \
  sts.googleapis.com cloudresourcemanager.googleapis.com

echo "Checking the database $DB_NAME and the user $DB_USER on $SQL_INSTANCE"
# Before any other change, so that a stop changes nothing. An assignment from a
# failed gcloud command stops the script: an error never reads as "absent".
had_password=
secrets=$(gcloud secrets list "${project[@]}" --format 'value(name.basename())')
if grep -qxF -- "$SECRET_DB_PASSWORD" <<<"$secrets"; then
  had_password=$(enabled_version "$SECRET_DB_PASSWORD")
fi
database_exists=false
user_exists=false
taken=()
instances=$(gcloud sql instances list "${project[@]}" --format 'value(name)')
if grep -qxF -- "$SQL_INSTANCE" <<<"$instances"; then
  databases=$(gcloud sql databases list "${instance[@]}" --format 'value(name)')
  users=$(gcloud sql users list "${instance[@]}" --format 'value(name)')
  if grep -qxF -- "$DB_NAME" <<<"$databases"; then
    database_exists=true
    taken+=("the database $DB_NAME")
  fi
  if grep -qxF -- "$DB_USER" <<<"$users"; then
    user_exists=true
    taken+=("the user $DB_USER")
  fi
fi
if [[ -z $had_password && ${#taken[@]} -gt 0 ]]; then
  {
    echo "Stopped before the setup of any resource. These names exist on $SQL_INSTANCE:"
    printf '  %s\n' "${taken[@]}"
    echo "The secret $SECRET_DB_PASSWORD has no password, so the names belong to something"
    echo "else. Choose other DB_NAME and DB_USER in $config, then run this script again."
  } >&2
  exit 1
fi

echo "Creating the Artifact Registry repository"
exists gcloud artifacts repositories describe "$REPOSITORY" "${project[@]}" --location "$REGION" ||
  gcloud artifacts repositories create "$REPOSITORY" "${project[@]}" --location "$REGION" \
    --repository-format docker --description "Taskman images"

echo "Creating the service accounts"
for name in "$DEPLOYER_SA" "$SERVER_SA"; do
  exists gcloud iam service-accounts describe "$(account "$name")" "${project[@]}" ||
    gcloud iam service-accounts create "$name" "${project[@]}" --display-name "Taskman $name"
done

if ! grep -qxF -- "$SQL_INSTANCE" <<<"$instances"; then
  echo "Creating the Cloud SQL instance $SQL_INSTANCE (this takes several minutes)"
  # The instance has no authorized networks: Cloud Run connects through the Cloud SQL
  # connector (the /cloudsql socket), which encrypts every connection.
  gcloud sql instances create "$SQL_INSTANCE" "${project[@]}" \
    --database-version POSTGRES_16 --edition ENTERPRISE --tier "$SQL_TIER" \
    --region "$REGION" --storage-auto-increase --backup-start-time 02:00 \
    --ssl-mode ENCRYPTED_ONLY
fi

# create_secret NAME: create the secret when it is absent, and give it a random value
# when it has no enabled version. An existing value is never replaced.
create_secret() {
  exists gcloud secrets describe "$1" "${project[@]}" ||
    gcloud secrets create "$1" "${project[@]}" --replication-policy automatic
  local enabled
  enabled=$(enabled_version "$1")
  if [[ -z $enabled ]]; then
    password | gcloud secrets versions add "$1" "${project[@]}" --data-file -
  fi
}

echo "Creating the database $DB_NAME and its user $DB_USER"
create_secret "$SECRET_DB_PASSWORD"
[[ $database_exists == true ]] || gcloud sql databases create "$DB_NAME" "${instance[@]}"
# The password of the user is the secret; setting it again keeps it. No file log: the
# password is a command argument. "=" keeps a password that starts with "-" a value.
db_password=$(gcloud secrets versions access latest --secret "$SECRET_DB_PASSWORD" "${project[@]}")
if [[ $user_exists == true ]]; then
  CLOUDSDK_CORE_DISABLE_FILE_LOGGING=1 gcloud sql users set-password "$DB_USER" \
    "${instance[@]}" --password="$db_password" >/dev/null
else
  CLOUDSDK_CORE_DISABLE_FILE_LOGGING=1 gcloud sql users create "$DB_USER" \
    "${instance[@]}" --password="$db_password" >/dev/null
fi
unset db_password

echo "Creating the secret of the Google OAuth client"
# Without a value: the project owner adds the client secret for browser login.
exists gcloud secrets describe "$SECRET_GOOGLE_CLIENT" "${project[@]}" ||
  gcloud secrets create "$SECRET_GOOGLE_CLIENT" "${project[@]}" --replication-policy automatic

# grant_secret NAME SERVICE_ACCOUNT...
grant_secret() {
  local secret=$1
  shift
  for name in "$@"; do
    gcloud secrets add-iam-policy-binding "$secret" "${project[@]}" \
      --member "$(member "$name")" --role roles/secretmanager.secretAccessor >/dev/null
  done
}

echo "Granting roles"
grant_secret "$SECRET_DB_PASSWORD" "$SERVER_SA"
grant_secret "$SECRET_GOOGLE_CLIENT" "$SERVER_SA"
# grant_project MEMBER ROLE
grant_project() {
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "$1" --role "$2" \
    --condition None >/dev/null
}
grant_project "$(member "$SERVER_SA")" roles/cloudsql.client
# The deployer updates the job and the service, and runs the job.
grant_project "$(member "$DEPLOYER_SA")" roles/run.developer
grant_project "$(member "$DEPLOYER_SA")" roles/cloudsql.viewer
gcloud artifacts repositories add-iam-policy-binding "$REPOSITORY" "${project[@]}" \
  --location "$REGION" --member "$(member "$DEPLOYER_SA")" \
  --role roles/artifactregistry.writer >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$(account "$SERVER_SA")" "${project[@]}" \
  --member "$(member "$DEPLOYER_SA")" --role roles/iam.serviceAccountUser >/dev/null

echo "Setting up Workload Identity Federation for $GITHUB_REPOSITORY"
exists gcloud iam workload-identity-pools describe "$WIF_POOL" "${project[@]}" --location global ||
  gcloud iam workload-identity-pools create "$WIF_POOL" "${project[@]}" --location global \
    --display-name "GitHub Actions"
exists gcloud iam workload-identity-pools providers describe "$WIF_PROVIDER" "${project[@]}" \
  --location global --workload-identity-pool "$WIF_POOL" ||
  gcloud iam workload-identity-pools providers create-oidc "$WIF_PROVIDER" "${project[@]}" \
    --location global --workload-identity-pool "$WIF_POOL" \
    --display-name "Taskman deployment" \
    --issuer-uri https://token.actions.githubusercontent.com \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition "assertion.repository == '$GITHUB_REPOSITORY' && assertion.ref == 'refs/heads/$DEPLOY_BRANCH'"
number=$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')
pool="projects/$number/locations/global/workloadIdentityPools/$WIF_POOL"
gcloud iam service-accounts add-iam-policy-binding "$(account "$DEPLOYER_SA")" "${project[@]}" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/$pool/attribute.repository/$GITHUB_REPOSITORY" \
  >/dev/null
# The binding names the pool, not the provider: every provider of the pool that maps
# attribute.repository can sign in as the deployer. The owner checks their conditions.
echo "Providers of the pool $WIF_POOL:"
gcloud iam workload-identity-pools providers list "${project[@]}" --location global \
  --workload-identity-pool "$WIF_POOL" --format 'table(name.basename(),attributeCondition)'

# The service is public: Cloud Run does not check IAM for callers, because agent
# machines have no Google identity. Taskman checks its own token on each request.
# deploy.sh changes no IAM setting, so the service stays public.
echo "Creating the public service $SERVICE"
# A list, not "exists ... describe": gcloud run deploy also replaces an existing service,
# so an error must stop the script instead of reading as "absent".
services=$(gcloud run services list "${project[@]}" --region "$REGION" --format 'value(metadata.name)')
if ! grep -qxF -- "$SERVICE" <<<"$services"; then
  if ! gcloud run deploy "$SERVICE" "${common[@]}" --image "$placeholder" \
    --service-account "$(account "$SERVER_SA")" --no-invoker-iam-check --labels app=taskman; then
    cat >&2 <<EOF

Could not create the public service $SERVICE. An organization policy
(constraints/run.managed.requireInvokerIam) or a missing permission blocked it. Ask an
organization admin to allow it. Or, if domain restricted sharing allows allUsers,
deploy the placeholder first and give allUsers the Cloud Run Invoker role:

  gcloud run deploy $SERVICE --project $PROJECT_ID --region $REGION \\
    --image $placeholder --service-account $(account "$SERVER_SA") --labels app=taskman
  gcloud run services add-iam-policy-binding $SERVICE --project $PROJECT_ID \\
    --region $REGION --member allUsers --role roles/run.invoker

Then run this script again.
EOF
    exit 1
  fi
else
  iam_disabled=$(gcloud run services describe "$SERVICE" "${common[@]}" \
    --format 'value(metadata.annotations."run.googleapis.com/invoker-iam-disabled")')
  if [[ $iam_disabled != true ]] &&
    ! gcloud run services update "$SERVICE" "${common[@]}" --no-invoker-iam-check; then
    # The fallback of an earlier run: allUsers has the Cloud Run Invoker role.
    invokers=$(gcloud run services get-iam-policy "$SERVICE" "${common[@]}" \
      --flatten 'bindings[].members' --filter 'bindings.role=roles/run.invoker' \
      --format 'value(bindings.members)')
    if ! grep -qxF allUsers <<<"$invokers"; then
      cat >&2 <<EOF

The service $SERVICE checks IAM for callers, so agent machines cannot reach it. An
organization policy (constraints/run.managed.requireInvokerIam) or a missing permission
blocked the change. Ask an organization admin to allow it. Or, if domain restricted
sharing allows allUsers, give allUsers the Cloud Run Invoker role:

  gcloud run services add-iam-policy-binding $SERVICE --project $PROJECT_ID \\
    --region $REGION --member allUsers --role roles/run.invoker

Then run this script again.
EOF
      exit 1
    fi
  fi
fi
url=$(gcloud run services describe "$SERVICE" "${common[@]}" --format 'value(status.url)')

cat <<EOF

Done. In GitHub, create the environment $environment of $GITHUB_REPOSITORY, limit its
deployment branches to $DEPLOY_BRANCH, and set these variables on it:

  GCP_WORKLOAD_IDENTITY_PROVIDER=$pool/providers/$WIF_PROVIDER
  GCP_DEPLOYER_SERVICE_ACCOUNT=$(account "$DEPLOYER_SA")

Service URL: $url
OAuth redirect URI of browser login: $url/auth/callback

Check the providers of the pool $WIF_POOL above: each one that admits $GITHUB_REPOSITORY
can sign in as the deployer, so its condition must admit only branches that you trust.

Next: set up the GitHub environment, then merge to $DEPLOY_BRANCH. The first
deployment replaces the placeholder image.
EOF
