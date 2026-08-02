# Remedi generated artifacts — `lineage-break-customer-dim`

Merge-ready outputs + PR package from DataHub schemas, queries, and lineage.

Start with `PR_DESCRIPTION.md`, then review code files.

- `dim_customer_repaired.sql` — Restores `customer_id` from `retail.customers` via `email` — grounded in `context_schema:customer_id, schema:email, upstream:urn:li:dataset:(urn:li:dataPlatform:snowflake,retail.customers,PROD), downstream:retail.fct_orders_enriched`
- `lineage_repair.py` — Daily repair + test until edge is healthy — grounded in `incident:lineage-break-customer-dim`
- `PR_DESCRIPTION.md` — Merge-ready PR description for data team review — grounded in `urn:urn:li:dataset:(urn:li:dataPlatform:dbt,retail.dim_customer,PROD), downstream:1`
- `remedi.patch` — Git-apply-ready patch bundle of generated files — grounded in `artifacts:all`
