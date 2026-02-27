#!/bin/bash
# Deploy brain-code-executor Cloud Run Job
# Usa la stessa immagine di agents-runner con entry point diverso.
#
# Prerequisiti:
#   - Immagine agents-runner:latest gia' buildata
#   - Service account ha roles/run.developer
#
# Usage:
#   bash deploy-job.sh          # crea o aggiorna il job
#   bash deploy-job.sh --execute  # triggera un'esecuzione

PROJECT="brain-core-487914"
REGION="europe-west3"
IMAGE="europe-west3-docker.pkg.dev/${PROJECT}/brain-repo/agents-runner:latest"
JOB_NAME="brain-code-executor"

# Env vars — stessi valori di agents-runner
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}"
GITHUB_TOKEN="${GITHUB_TOKEN}"
SUPABASE_URL="${SUPABASE_URL}"
SUPABASE_KEY="${SUPABASE_KEY}"

if [ "$1" = "--execute" ]; then
    echo "[JOB] Triggering execution..."
    gcloud run jobs execute "$JOB_NAME" --region="$REGION"
    exit $?
fi

# Verifica se il job esiste
if gcloud run jobs describe "$JOB_NAME" --region="$REGION" > /dev/null 2>&1; then
    echo "[JOB] Aggiornamento job esistente..."
    gcloud run jobs update "$JOB_NAME" \
        --image="$IMAGE" \
        --region="$REGION" \
        --memory="2Gi" \
        --task-timeout="3600" \
        --command="python" \
        --args="cloud_code_runner.py" \
        --set-env-vars="ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY},GITHUB_TOKEN=${GITHUB_TOKEN},SUPABASE_URL=${SUPABASE_URL},SUPABASE_KEY=${SUPABASE_KEY}" \
        --max-retries=0
else
    echo "[JOB] Creazione nuovo job..."
    gcloud run jobs create "$JOB_NAME" \
        --image="$IMAGE" \
        --region="$REGION" \
        --memory="2Gi" \
        --task-timeout="3600" \
        --command="python" \
        --args="cloud_code_runner.py" \
        --set-env-vars="ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY},GITHUB_TOKEN=${GITHUB_TOKEN},SUPABASE_URL=${SUPABASE_URL},SUPABASE_KEY=${SUPABASE_KEY}" \
        --max-retries=0
fi

echo "[JOB] Done. Job: $JOB_NAME Region: $REGION"
