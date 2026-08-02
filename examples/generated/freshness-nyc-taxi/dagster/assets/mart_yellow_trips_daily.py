"""Dagster asset job — Remedi freshness-nyc-taxi."""

import subprocess

from dagster import AssetExecutionContext, Definitions, asset

DATAHUB_SOURCE_URN = (
    "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.nyc_taxi.yellow_trips,PROD)"
)
DATAHUB_OWNER_URNS = [
    "urn:li:corpuser:analytics-eng",
    "urn:li:corpuser:data-oncall",
    "urn:li:corpuser:ml-platform",
    "urn:li:corpuser:ops-lead",
    "urn:li:corpuser:taxi-platform",
]


@asset(name="mart_yellow_trips_daily", group_name="remedi")
def mart_yellow_trips_daily(context: AssetExecutionContext) -> None:
    """Freshness-guarded mart grounded in DataHub metadata."""
    context.log.info("DataHub source: %s", DATAHUB_SOURCE_URN)
    context.log.info("DataHub owners: %s", DATAHUB_OWNER_URNS)
    subprocess.run(["dbt", "run", "--select", "mart_yellow_trips_daily"], check=True)
    subprocess.run(["dbt", "test", "--select", "mart_yellow_trips_daily"], check=True)


defs = Definitions(assets=[mart_yellow_trips_daily])
