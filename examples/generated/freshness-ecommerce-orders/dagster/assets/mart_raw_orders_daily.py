"""Dagster asset job — Remedi freshness-ecommerce-orders."""

import subprocess

from dagster import AssetExecutionContext, Definitions, asset

DATAHUB_SOURCE_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.raw_orders,PROD)"
DATAHUB_OWNER_URNS = [
    "urn:li:corpuser:analytics-eng",
    "urn:li:corpuser:commerce-lead",
    "urn:li:corpuser:commerce-platform",
    "urn:li:corpuser:exec-bi",
]


@asset(name="mart_raw_orders_daily", group_name="remedi")
def mart_raw_orders_daily(context: AssetExecutionContext) -> None:
    """Freshness-guarded mart grounded in DataHub metadata."""
    context.log.info("DataHub source: %s", DATAHUB_SOURCE_URN)
    context.log.info("DataHub owners: %s", DATAHUB_OWNER_URNS)
    subprocess.run(["dbt", "run", "--select", "mart_raw_orders_daily"], check=True)
    subprocess.run(["dbt", "test", "--select", "mart_raw_orders_daily"], check=True)


defs = Definitions(assets=[mart_raw_orders_daily])
