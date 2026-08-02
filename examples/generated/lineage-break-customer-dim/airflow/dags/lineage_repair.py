"""Lineage repair DAG — Remedi lineage-break-customer-dim."""

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="remedi_lineage_repair_lineage_break_customer_dim",
    start_date=datetime(2026, 1, 1),
    schedule="@daily",
    catchup=False,
    tags=["remedi", "lineage-break"],
) as dag:
    BashOperator(
        task_id="dbt_run_lineage_repair",
        bash_command=(
            "dbt run --select dim_customer_repaired && dbt test --select dim_customer_repaired"
        ),
    )
