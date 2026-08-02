#!/usr/bin/env bash
# Optional: load a verified DataHub sample datapack once GMS is up.
# Usage: ./scripts/load-datapack.sh [showcase-ecommerce|bootstrap]
set -euo pipefail
PACK="${1:-showcase-ecommerce}"
export DATAHUB_GMS_URL="${DATAHUB_GMS_URL:-http://localhost:8080}"

if command -v uv >/dev/null 2>&1; then
  DATAHUB_CMD=(uv run --extra live datahub)
elif command -v datahub >/dev/null 2>&1; then
  DATAHUB_CMD=(datahub)
else
  echo "Install uv, or install the DataHub CLI with: pip install acryl-datahub"
  exit 1
fi

echo "Loading datapack '${PACK}' into ${DATAHUB_GMS_URL}"
"${DATAHUB_CMD[@]}" datapack load "${PACK}"
echo "This loads catalog metadata; Remedi discovers incidents only when DataHub has failing assertions."
echo "Done. Set REMEDI_MODE=live DATAHUB_GMS_URL=${DATAHUB_GMS_URL} and restart Remedi."
