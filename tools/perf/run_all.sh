#!/usr/bin/env bash
set -euo pipefail
PY=${PY:-python}
OUT=${OUT:-deliverables/perf/run_$(date -u +%Y%m%dT%H%M%SZ)}
mkdir -p "$OUT"
$PY tools/perf/inventory.py --out "$OUT/endpoints.json"
$PY tools/perf/scenarios.py --endpoints "$OUT/endpoints.json" --out "$OUT/scenarios.json"
if [[ -n "${BASE_URL:-}" ]]; then
  $PY tools/perf/load_run.py --base-url "$BASE_URL" --allow-remote --endpoints "$OUT/endpoints.json" --scenarios "$OUT/scenarios.json" ${FIXTURES:+--fixtures "$FIXTURES"} --concurrency "${CONCURRENCY:-10}" --duration "${DURATION:-30}" --output "$OUT/load"
else
  $PY tools/perf/load_run.py --plan --endpoints "$OUT/endpoints.json" --scenarios "$OUT/scenarios.json" --output "$OUT/load_plan"
fi
if [[ -n "${LOG_FILE:-}" ]]; then
  $PY tools/perf/apm_log_stats.py "$LOG_FILE" --format "${LOG_FORMAT:-app}" --out "$OUT/apm" --docx
fi
$PY tools/perf/db_perf.py --emit-sql --check ${POOL_TEST:+--pool-test} ${QUERIES:+--queries "$QUERIES"} --out "$OUT/db"
echo "OUT=$OUT"
