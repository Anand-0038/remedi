"""Prefect flow — Remedi freshness-ecommerce-orders."""

import subprocess

from prefect import flow, task


@task
def dbt_run() -> None:
    subprocess.run(["dbt", "run", "--select", "mart_raw_orders_daily"], check=True)


@task
def dbt_test() -> None:
    subprocess.run(["dbt", "test", "--select", "mart_raw_orders_daily"], check=True)


@flow(name="airflow_ecommerce_orders_hourly_remedi")
def remedi_freshness_flow() -> None:
    dbt_run()
    dbt_test()


if __name__ == "__main__":
    remedi_freshness_flow()
