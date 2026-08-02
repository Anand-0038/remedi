"""Prefect flow — Remedi freshness-nyc-taxi."""

import subprocess

from prefect import flow, task


@task
def dbt_run() -> None:
    subprocess.run(["dbt", "run", "--select", "mart_yellow_trips_daily"], check=True)


@task
def dbt_test() -> None:
    subprocess.run(["dbt", "test", "--select", "mart_yellow_trips_daily"], check=True)


@flow(name="airflow_nyc_taxi_daily_remedi")
def remedi_freshness_flow() -> None:
    dbt_run()
    dbt_test()


if __name__ == "__main__":
    remedi_freshness_flow()
