#!/usr/bin/env bash
# Deploy one Taskman image to a Google Cloud environment.
#
#   deploy/gcp/deploy.sh deploy/gcp/nemos-infra.env IMAGE
#
# IMAGE is a pushed image reference; the workflow passes a digest. The steps:
#
# 1. Update the schema job and run it: taskman db upgrade creates or upgrades the
#    schema. When it fails, the deployment stops: the job has the new image, and the
#    service keeps its current image.
# 2. Deploy the service. It is public: Cloud Run does not check IAM for callers, and
#    every request needs a Taskman token or a browser login. bootstrap.sh created the
#    service and made it public.
#
# A failure in step 2 leaves the job on the new image and the service on its current
# image. Deploy again, with the new image after a fix or with the previous image.
# gcloud run deploy fails when the new revision does not start.
#
# The GitHub workflow .github/workflows/deploy-nemos-infra.yml runs this script. A
# person runs it the same way with their own gcloud sign-in.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 CONFIG IMAGE" >&2
  exit 2
fi
config=$1
image=$2
# shellcheck source=deploy/gcp/nemos-infra.env
source "$config"

connection="$PROJECT_ID:$REGION:$SQL_INSTANCE"
account() { echo "$1@$PROJECT_ID.iam.gserviceaccount.com"; }
# env_vars KEY=VALUE...: one --set-env-vars value. The delimiter is ";" (gcloud's
# ^;^ syntax, gcloud topic escaping), because the allow-lists hold commas.
env_vars() {
  local IFS=';'
  echo "^;^$*"
}

common=(--project "$PROJECT_ID" --region "$REGION" --quiet)
# The database settings of the job and the service. Cloud Run sets the password from
# Secret Manager as an environment variable.
database_env=(
  TASKMAN_BACKEND=postgres
  "TASKMAN_DB_SOCKET=/cloudsql/$connection"
  "TASKMAN_DB_NAME=$DB_NAME"
  "TASKMAN_DB_USER=$DB_USER"
)
database_secret="TASKMAN_DB_PASSWORD=$SECRET_DB_PASSWORD:latest"
runtime=(
  --image "$image"
  --service-account "$(account "$SERVER_SA")"
  --set-cloudsql-instances "$connection"
  --labels app=taskman
)

# Browser login is on when GOOGLE_CLIENT_ID is set. Check it before any change.
service_env=("${database_env[@]}")
service_secrets=$database_secret
if [[ -n ${GOOGLE_CLIENT_ID:-} ]]; then
  if [[ -z ${ALLOWED_DOMAINS:-} && -z ${ALLOWED_EMAILS:-} ]]; then
    echo "Browser login needs ALLOWED_DOMAINS or ALLOWED_EMAILS in $config." >&2
    exit 2
  fi
  # bootstrap.sh created the service, so its URL is known before the first deployment.
  url=$(gcloud run services describe "$SERVICE" "${common[@]}" --format 'value(status.url)')
  service_env+=("TASKMAN_GOOGLE_CLIENT_ID=$GOOGLE_CLIENT_ID" "TASKMAN_PUBLIC_URL=$url")
  if [[ -n ${ALLOWED_DOMAINS:-} ]]; then
    service_env+=("TASKMAN_ALLOWED_DOMAINS=$ALLOWED_DOMAINS")
  fi
  if [[ -n ${ALLOWED_EMAILS:-} ]]; then
    service_env+=("TASKMAN_ALLOWED_EMAILS=$ALLOWED_EMAILS")
  fi
  service_secrets+=",TASKMAN_GOOGLE_CLIENT_SECRET=$SECRET_GOOGLE_CLIENT:latest"
fi

echo "Upgrading the schema with $image"
gcloud run jobs deploy "$UPGRADE_JOB" "${common[@]}" "${runtime[@]}" \
  --set-env-vars "$(env_vars "${database_env[@]}")" \
  --set-secrets "$database_secret" \
  --args db,upgrade \
  --tasks 1 --max-retries 0 --task-timeout 10m
gcloud run jobs execute "$UPGRADE_JOB" "${common[@]}" --wait

echo "Deploying $SERVICE"
# Without --[no-]allow-unauthenticated and --[no-]invoker-iam-check, gcloud changes no
# IAM setting: the service keeps the public setting of bootstrap.sh.
gcloud run deploy "$SERVICE" "${common[@]}" "${runtime[@]}" \
  --set-env-vars "$(env_vars "${service_env[@]}")" \
  --set-secrets "$service_secrets" \
  --port 8080 --ingress all --cpu 1 --memory 512Mi --min-instances 0 --max-instances 3 \
  --timeout 120 \
  --liveness-probe httpGet.path=/readyz,httpGet.port=8080,periodSeconds=30,timeoutSeconds=5,failureThreshold=3

echo "Deployed $image to $PROJECT_ID"
