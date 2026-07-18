#!/usr/bin/env bash
set -euo pipefail
trap 'kill 0' EXIT
(cd apps/api && uv run uvicorn app.main:app --reload --port 8888) &
(cd apps/web && npm run dev) &
wait

