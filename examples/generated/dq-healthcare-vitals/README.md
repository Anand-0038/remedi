# Remedi generated artifacts — `dq-healthcare-vitals`

Merge-ready outputs + PR package from DataHub schemas, queries, and lineage.

Start with `PR_DESCRIPTION.md`, then review code files.

- `vitals_quarantine_and_clean.sql` — Quarantine null `heart_rate` rows and publish a fail-closed clean view — grounded in `schema:heart_rate, urn:urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.vitals,PROD), schema:measured_at`
- `vitals_clean.yml` — Fail-closed not_null on `heart_rate` — grounded in `schema:heart_rate`
- `dq_impact_notice.md` — Owner notice from DataHub lineage blast radius — grounded in `downstream:4`
- `feature_quality_gate.py` — Null-rate gate on `heart_rate` for ML models/features in blast radius — grounded in `schema:heart_rate, ml_models:1, ml_features:1, urn:urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.vitals,PROD)`
- `PR_DESCRIPTION.md` — Merge-ready PR description for data team review — grounded in `urn:urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.vitals,PROD), downstream:4`
- `remedi.patch` — Git-apply-ready patch bundle of generated files — grounded in `artifacts:all`
