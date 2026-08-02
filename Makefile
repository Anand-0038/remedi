.PHONY: demo-live test serve serve-live serve-offline seed-live-demo samples incidents clean-state selftest verify-local

demo-live: seed-live-demo serve-live

test:
	uv sync --extra dev
	uv run pytest -q

selftest:
	uv run remedi selftest

verify-local:
	chmod +x scripts/verify-local.sh
	./scripts/verify-local.sh

incidents:
	uv run remedi incidents

samples:
	ARTIFACTS_DIR=examples/generated uv run remedi run -i freshness-nyc-taxi --dry-run
	ARTIFACTS_DIR=examples/generated uv run remedi run -i dq-healthcare-vitals --dry-run
	ARTIFACTS_DIR=examples/generated uv run remedi run -i schema-orders-amount --dry-run
	ARTIFACTS_DIR=examples/generated uv run remedi run -i lineage-break-customer-dim --dry-run
	ARTIFACTS_DIR=examples/generated uv run remedi run -i freshness-ecommerce-orders --dry-run

serve:
	@if [ "$${REMEDI_MODE:-live}" != "live" ]; then echo "Use 'make serve-offline' for fixture mode."; exit 2; fi
	@$(MAKE) serve-live

serve-live:
	@if [ -z "$${REMEDI_API_KEY}" ]; then echo "Set REMEDI_API_KEY before starting live mode."; exit 2; fi
	REMEDI_MODE=live uv run remedi serve --port 8790

serve-offline:
	REMEDI_MODE=fixture uv run remedi serve --port 8790

seed-live-demo:
	uv run python scripts/seed-live-demo.py

clean-state:
	rm -rf .remedi
