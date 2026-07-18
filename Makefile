.PHONY: install dev api web test smoke
install:
	cd apps/api && uv sync --dev
	cd apps/web && npm install
api:
	cd apps/api && uv run uvicorn app.main:app --reload --port 8888
web:
	cd apps/web && npm run dev
dev:
	./scripts/dev.sh
test:
	cd apps/api && uv run pytest
	cd apps/web && npm test && npm run typecheck && npm run build
smoke:
	cd apps/api && uv run python ../../scripts/smoke.py

