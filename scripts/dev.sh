#!/usr/bin/env bash
set -euo pipefail
trap 'kill 0' EXIT
(cd apps/api && uv run uvicorn app.main:app --reload --port 8000) &
(cd apps/web && npm run dev) &
wait

