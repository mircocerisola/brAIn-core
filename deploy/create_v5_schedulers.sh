#!/bin/bash
# brAIn v5.0 — Crea 14 nuovi Cloud Scheduler jobs
# Eseguire con: bash deploy/create_v5_schedulers.sh

BASE_URL="https://agents-runner-402184600300.europe-west3.run.app"
SA="402184600300-compute@developer.gserviceaccount.com"
PROJECT="brain-core-487914"
REGION="europe-west3"
TZ="Europe/Rome"

create_job() {
    local NAME=$1 SCHEDULE=$2 ENDPOINT=$3 BODY=$4 DEADLINE=${5:-300s}
    echo "Creating: $NAME ($SCHEDULE → $ENDPOINT)"
    gcloud scheduler jobs create http "$NAME" \
        --location="$REGION" \
        --schedule="$SCHEDULE" \
        --uri="${BASE_URL}${ENDPOINT}" \
        --http-method=POST \
        --message-body="$BODY" \
        --headers="Content-Type=application/json" \
        --oidc-service-account-email="$SA" \
        --oidc-token-audience="$BASE_URL" \
        --time-zone="$TZ" \
        --attempt-deadline="$DEADLINE" \
        --project="$PROJECT" 2>&1 | grep -E "name:|state:|error" || true
}

# 1. C-Suite: tutti i briefing settimanali (un job all-in-one)
create_job "brain-csuite-all-briefings" "0 7 * * 1" "/csuite/briefing" '{}' "600s"

# 2. C-Suite: anomaly check mattutino
create_job "brain-csuite-anomalies-morning" "30 6 * * *" "/csuite/anomalies" '{}' "300s"

# 3. C-Suite: anomaly check serale
create_job "brain-csuite-anomalies-evening" "30 20 * * *" "/csuite/anomalies" '{}' "300s"

# 4-11. C-Suite: briefing individuale per ogni Chief (lun, orari scaglionati)
create_job "brain-csuite-cso" "0 7 * * 1"  "/csuite/briefing" '{"domain":"strategy"}' "300s"
create_job "brain-csuite-cfo" "10 7 * * 1" "/csuite/briefing" '{"domain":"finance"}' "300s"
create_job "brain-csuite-cmo" "20 7 * * 1" "/csuite/briefing" '{"domain":"marketing"}' "300s"
create_job "brain-csuite-cto" "30 7 * * 1" "/csuite/briefing" '{"domain":"tech"}' "300s"
create_job "brain-csuite-coo" "40 7 * * 1" "/csuite/briefing" '{"domain":"ops"}' "300s"
create_job "brain-csuite-cpo" "50 7 * * 1" "/csuite/briefing" '{"domain":"product"}' "300s"
create_job "brain-csuite-clo" "0 8 * * 1"  "/csuite/briefing" '{"domain":"legal"}' "300s"
create_job "brain-csuite-cpeo" "10 8 * * 1" "/csuite/briefing" '{"domain":"people"}' "300s"

# 12. Ethics: check 3x al giorno (ogni ~8h)
create_job "brain-ethics-morning"  "0 9  * * *" "/ethics/check-active" '{}' "300s"
create_job "brain-ethics-afternoon" "0 15 * * *" "/ethics/check-active" '{}' "300s"
create_job "brain-ethics-evening"  "0 21 * * *" "/ethics/check-active" '{}' "300s"

echo "Done — 14 jobs created (check above for errors)"
