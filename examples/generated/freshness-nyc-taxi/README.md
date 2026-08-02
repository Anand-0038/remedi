# Remedi generated artifacts — `freshness-nyc-taxi`

Merge-ready outputs + PR package from DataHub schemas, queries, and lineage.

Start with `PR_DESCRIPTION.md`, then review code files.

- `mart_yellow_trips_daily.sql` — Incremental mart using schema columns pickup_date/pickup_datetime/trip_distance — grounded in `schema:pickup_date, schema:pickup_datetime, urn:urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.nyc_taxi.yellow_trips,PROD), schema:trip_distance, queries:datahub`
- `schema.yml` — Fail-closed freshness test on freshness_status — grounded in `schema:pickup_date, sla_hours:6`
- `airflow_nyc_taxi_daily_remedi_guard.py` — Fail-closed source freshness gate + dbt run/test; retains DataHub owner URNs in evidence — grounded in `owners:5, pipeline:airflow.nyc_taxi_daily`
- `feature_freshness_guard.py` — Skip/fail training when features stale — protects demand_forecast_v3 — grounded in `ml_models:1, schema:pickup_date, schema:pickup_datetime, urn:urn:li:mlModel:(urn:li:dataPlatform:sagemaker,demand_forecast_v3,PROD)`
- `model_card_patch.yml` — Model card freshness contract from DataHub ML lineage — grounded in `ml_models:1`
- `mart_yellow_trips_daily.py` — Dagster asset variant of freshness remediation — grounded in `urn:urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.nyc_taxi.yellow_trips,PROD), owners:5`
- `mart_yellow_trips_daily_flow.py` — Prefect flow variant of freshness remediation — grounded in `urn:urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.nyc_taxi.yellow_trips,PROD), pipeline:airflow.nyc_taxi_daily`
- `PR_DESCRIPTION.md` — Merge-ready PR description for data team review — grounded in `urn:urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.nyc_taxi.yellow_trips,PROD), downstream:3`
- `remedi.patch` — Git-apply-ready patch bundle of generated files — grounded in `artifacts:all`
