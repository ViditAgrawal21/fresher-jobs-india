#!/usr/bin/env bash
# Deploy to GCP: Cloud Run service (site + API), Cloud Run job (pipeline), Cloud Scheduler (daily trigger).
#
#   PROJECT_ID=my-project ./infra/deploy.sh setup     # once: APIs, registry, service accounts
#   PROJECT_ID=my-project ./infra/deploy.sh secrets   # push .env / .env.local values to Secret Manager
#   PROJECT_ID=my-project ./infra/deploy.sh all       # build image + deploy service, job, schedule
#   PROJECT_ID=my-project ./infra/deploy.sh run-now   # trigger the pipeline job once
#   PROJECT_ID=my-project ./infra/deploy.sh domain    # map beep.viditagrawal.in to the service
#
# Region is asia-southeast1 (Singapore): Cloud Run domain mapping isn't available in asia-south1 (Mumbai).
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-southeast1}"
DOMAIN="${DOMAIN:-beep.viditagrawal.in}"
REPO=fresher-jobs
SERVICE=fresher-jobs-web
JOB=fresher-jobs-pipeline
SCHEDULE_NAME=fresher-jobs-daily
RUNTIME_SA="fresher-jobs-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SA="fresher-jobs-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"
SECRETS=(DATABASE_URL DATABASE_URL_UNPOOLED ANTHROPIC_API_KEY ADZUNA_APP_ID ADZUNA_APP_KEY JOOBLE_API_KEY)

TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/app:${TAG}"

gc() { gcloud --project "$PROJECT_ID" "$@"; }

# Secrets that actually exist in Secret Manager, as KEY=KEY:latest pairs.
secret_flags() {
  local pairs=() s
  for s in "${SECRETS[@]}"; do
    if gc secrets describe "$s" >/dev/null 2>&1; then pairs+=("${s}=${s}:latest"); fi
  done
  (IFS=,; echo "${pairs[*]}")
}

# Read KEY from .env.local then .env without printing it.
env_value() {
  local key="$1" f line
  for f in .env.local .env; do
    [[ -f "$f" ]] || continue
    line="$(grep -E "^${key}=" "$f" | tail -1 || true)"
    if [[ -n "$line" ]]; then
      line="${line#*=}"; line="${line%\"}"; line="${line#\"}"; line="${line%\'}"; line="${line#\'}"
      printf '%s' "$line"; return
    fi
  done
}

setup() {
  gc services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    cloudscheduler.googleapis.com secretmanager.googleapis.com
  gc artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 ||
    gc artifacts repositories create "$REPO" --repository-format docker --location "$REGION"
  gc iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1 ||
    gc iam service-accounts create fresher-jobs-runtime --display-name "Fresher Jobs runtime"
  gc iam service-accounts describe "$SCHEDULER_SA" >/dev/null 2>&1 ||
    gc iam service-accounts create fresher-jobs-scheduler --display-name "Fresher Jobs scheduler"
}

secrets() {
  local s v
  for s in "${SECRETS[@]}"; do
    v="$(env_value "$s")"
    if [[ -z "$v" ]]; then echo "skip $s (empty)"; continue; fi
    if gc secrets describe "$s" >/dev/null 2>&1; then
      printf '%s' "$v" | gc secrets versions add "$s" --data-file=- >/dev/null
    else
      printf '%s' "$v" | gc secrets create "$s" --replication-policy automatic --data-file=- >/dev/null
    fi
    gc secrets add-iam-policy-binding "$s" --member "serviceAccount:${RUNTIME_SA}" \
      --role roles/secretmanager.secretAccessor >/dev/null
    echo "set $s"
  done
}

build() {
  gc builds submit --config infra/cloudbuild.yaml --substitutions "_IMAGE=${IMAGE}" .
}

deploy() {
  local flags; flags="$(secret_flags)"

  gc run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" \
    --service-account "$RUNTIME_SA" --allow-unauthenticated \
    --set-secrets "$flags" \
    --memory 512Mi --cpu 1 --concurrency 40 --min-instances 0 --max-instances 3

  gc run jobs deploy "$JOB" --image "$IMAGE" --region "$REGION" \
    --service-account "$RUNTIME_SA" \
    --set-secrets "$flags" \
    --command python --args=-m,pipeline.run \
    --memory 1Gi --cpu 1 --task-timeout 30m --max-retries 1

  gc run jobs add-iam-policy-binding "$JOB" --region "$REGION" \
    --member "serviceAccount:${SCHEDULER_SA}" --role roles/run.invoker >/dev/null

  local uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
  local verb=create
  gc scheduler jobs describe "$SCHEDULE_NAME" --location "$REGION" >/dev/null 2>&1 && verb=update
  gc scheduler jobs "$verb" http "$SCHEDULE_NAME" --location "$REGION" \
    --schedule "0 6 * * *" --time-zone "Asia/Kolkata" \
    --uri "$uri" --http-method POST \
    --oauth-service-account-email "$SCHEDULER_SA"

  echo "service: $(gc run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')"
}

run_now() {
  gc run jobs execute "$JOB" --region "$REGION" --wait
}

domain() {
  echo "1) Verify ownership of ${DOMAIN#*.} once: gcloud domains verify ${DOMAIN#*.}"
  gc beta run domain-mappings create --service "$SERVICE" --domain "$DOMAIN" --region "$REGION" || true
  echo "2) Add the DNS record(s) below at your DNS provider:"
  gc beta run domain-mappings describe --domain "$DOMAIN" --region "$REGION" --format 'table(status.resourceRecords)'
}

case "${1:-all}" in
  setup) setup ;;
  secrets) secrets ;;
  build) build ;;
  deploy) deploy ;;
  all) build; deploy ;;
  run-now) run_now ;;
  domain) domain ;;
  *) echo "usage: $0 {setup|secrets|build|deploy|all|run-now|domain}"; exit 1 ;;
esac
