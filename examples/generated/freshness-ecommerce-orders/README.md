# Remedi generated artifacts — `freshness-ecommerce-orders`

Merge-ready outputs + PR package from DataHub schemas, queries, and lineage.

Start with `PR_DESCRIPTION.md`, then review code files.

- `mart_raw_orders_daily.sql` — Incremental mart using schema columns order_date/updated_at/gmv — grounded in `schema:order_date, schema:updated_at, urn:urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.raw_orders,PROD), schema:gmv, queries:datahub`
- `schema.yml` — Fail-closed freshness test on freshness_status — grounded in `schema:order_date, sla_hours:4`
- `airflow_ecommerce_orders_hourly_remedi_guard.py` — Fail-closed source freshness gate + dbt run/test; retains DataHub owner URNs in evidence — grounded in `owners:4, pipeline:airflow.ecommerce_orders_hourly`
- `mart_raw_orders_daily.py` — Dagster asset variant of freshness remediation — grounded in `urn:urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.raw_orders,PROD), owners:4`
- `mart_raw_orders_daily_flow.py` — Prefect flow variant of freshness remediation — grounded in `urn:urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.raw_orders,PROD), pipeline:airflow.ecommerce_orders_hourly`
- `PR_DESCRIPTION.md` — Merge-ready PR description for data team review — grounded in `urn:urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.raw_orders,PROD), downstream:3`
- `remedi.patch` — Git-apply-ready patch bundle of generated files — grounded in `artifacts:all`
