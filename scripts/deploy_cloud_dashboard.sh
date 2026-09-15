#!/usr/bin/env bash
# Publishes ONLY the isolated static dashboard; never the GPU worker or repository root.
set -euo pipefail
cd "$(dirname "$0")/.."
project="${1:?Usage: scripts/deploy_cloud_dashboard.sh PROJECT_ID [REGION]}"
region="${2:-us-central1}"
gcloud_bin="${GCLOUD_BIN:-gcloud}"
service=fork-microscope
registry="${region}-docker.pkg.dev"
repository=fork-dashboard
identity="fork-dashboard-web@${project}.iam.gserviceaccount.com"
python3 scripts/build_cloud_dashboard.py
asset_hash=$(sha256sum dist/dashboard/build-manifest.json | cut -c1-12)
tag="$(date -u +%Y%m%d%H%M%S)-${asset_hash}"
image="${registry}/${project}/${repository}/dashboard:${tag}"
# Existing project APIs, repository and role-free runtime identity are prerequisites.
"$gcloud_bin" artifacts repositories describe "$repository" --project="$project" --location="$region" --format='value(name)' >/dev/null
"$gcloud_bin" iam service-accounts describe "$identity" --project="$project" --format='value(email)' >/dev/null
docker build -t "$image" dist/cloud-run
# Temporary credentials: never modify global Docker auth or put a token in the build.
auth_dir=$(mktemp -d)
trap 'rm -rf "$auth_dir"' EXIT
"$gcloud_bin" auth print-access-token | docker --config "$auth_dir" login -u oauth2accesstoken --password-stdin "https://${registry}"
docker --config "$auth_dir" push "$image"
"$gcloud_bin" run deploy "$service" --project="$project" --region="$region" \
  --image="$image" --port=8080 --service-account="$identity" \
  --allow-unauthenticated --ingress=all --execution-environment=gen1 \
  --cpu=1 --memory=256Mi --concurrency=80 --timeout=30s \
  --min=0 --max=2 --cpu-throttling --no-cpu-boost --quiet
"$gcloud_bin" run services describe "$service" --project="$project" --region="$region" \
  --format=json > dist/cloud-run-service.json
python3 - <<'PY'
import json
s=json.load(open('dist/cloud-run-service.json'))
print('Dashboard URL:',s['status']['url'])
print('Ready revision:',s['status']['latestReadyRevisionName'])
PY
