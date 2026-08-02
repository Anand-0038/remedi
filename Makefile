.PHONY: demo test serve samples incidents clean-state selftest verify-local

demo: verify-local samples
	@echo ""
	@echo "Demo ready. Start UI with: make serve"
	@echo "Then open http://localhost:8790"

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
	uv run remedi run -i freshness-nyc-taxi --dry-run
	uv run remedi run -i dq-healthcare-vitals --dry-run
	uv run remedi run -i schema-orders-amount --dry-run
	uv run remedi run -i lineage-break-customer-dim --dry-run
	uv run remedi run -i freshness-ecommerce-orders --dry-run

serve:
	uv run remedi serve --port 8790

clean-state:
	rm -f examples/fixtures/catalog.state.json examples/fixtures/writeback_log.json
	rm -rf examples/selftest examples/proposals
