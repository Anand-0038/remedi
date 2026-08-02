from __future__ import annotations

import json
import re
import textwrap
from typing import Any

from remedi.models.incident import (
    BlastRadius,
    CatalogWriteBack,
    GeneratedArtifact,
    Incident,
    IncidentType,
    RemediationPlan,
    SchemaField,
)


def _field_names(fields: list[SchemaField]) -> list[str]:
    return [f.name for f in fields]


def _pick_field(fields: list[SchemaField], *needles: str, default: str | None = None) -> str | None:
    lowered = [(f.name, f.name.lower()) for f in fields]
    for needle in needles:
        for name, low in lowered:
            if needle in low:
                return name
    return default


def _numeric_fields(fields: list[SchemaField]) -> list[str]:
    out = []
    for f in fields:
        t = f.type.lower()
        if any(x in t for x in ("number", "int", "float", "double", "numeric", "decimal")):
            if "id" not in f.name.lower() and "count" not in f.name.lower():
                out.append(f.name)
    return out


def _timestamp_fields(fields: list[SchemaField]) -> list[str]:
    return [
        f.name
        for f in fields
        if any(x in f.type.lower() for x in ("timestamp", "datetime", "date"))
        or any(x in f.name.lower() for x in ("time", "date", "at"))
    ]


def _source_ref(entity_name: str, platform: str | None) -> str:
    """Build a dbt source() call from entity name like analytics.nyc_taxi.yellow_trips."""
    parts = entity_name.split(".")
    if len(parts) >= 2:
        source_name = parts[-2] if len(parts) >= 3 else parts[0]
        table = parts[-1]
        return f"{{{{ source('{source_name}', '{table}') }}}}"
    return f"{{{{ source('{platform or 'raw'}', '{entity_name}') }}}}"


def _extract_columns_from_queries(queries: list[str], known: list[str]) -> list[str]:
    found: list[str] = []
    for q in queries:
        for col in known:
            if re.search(rf"\b{re.escape(col)}\b", q, re.IGNORECASE) and col not in found:
                found.append(col)
    return found


def _python_string_list(values: list[str]) -> str:
    """Render a deterministic string list that is already Ruff-formatted."""
    if len(values) <= 1:
        return f"[{', '.join(json.dumps(value) for value in values)}]"
    lines = "\n".join(f"    {json.dumps(value)}," for value in values)
    return f"[\n{lines}\n]"


def _python_string_assignment(name: str, value: str) -> str:
    literal = json.dumps(value)
    one_line = f"{name} = {literal}"
    if len(one_line) <= 100:
        return one_line
    return f"{name} = (\n    {literal}\n)"


def _python_keyword_string(keyword: str, value: str, *, indent: int = 8) -> str:
    spaces = " " * indent
    literal = json.dumps(value)
    one_line = f"{spaces}{keyword}=({literal}),"
    if len(one_line) <= 100:
        return one_line
    literal_indent = f"{spaces}    "
    max_value_width = 100 - len(literal_indent) - 2
    chunks = textwrap.wrap(
        value,
        width=max_value_width,
        break_long_words=False,
        break_on_hyphens=False,
    )
    if len(chunks) == 1:
        body = f"{literal_indent}{literal}"
    else:
        body = "\n".join(
            f"{literal_indent}{json.dumps(chunk + (' ' if index < len(chunks) - 1 else ''))}"
            for index, chunk in enumerate(chunks)
        )
    return f"{spaces}{keyword}=(\n{body}\n{spaces}),"


class CodeGenerationError(RuntimeError):
    """A configured LLM request failed or returned an unusable plan."""


class CoderAgent:
    """Metadata-aware codegen — every column/table comes from DataHub schema or queries."""

    def __init__(
        self, *, llm_enabled: bool = False, openai_api_key: str = "", openai_model: str = ""
    ) -> None:
        self.llm_enabled = llm_enabled and bool(openai_api_key)
        self.openai_api_key = openai_api_key
        self.openai_model = openai_model or "gpt-4o-mini"

    def plan(self, incident: Incident, blast: BlastRadius) -> RemediationPlan:
        if self.llm_enabled:
            try:
                plan = self._llm_plan(incident, blast)
                if plan is not None:
                    return plan
                raise ValueError("response contained no remediation artifacts")
            except Exception as exc:
                raise CodeGenerationError(
                    "Configured OpenAI code generation failed; no template fallback was used. "
                    f"Underlying error: {exc}"
                ) from exc

        if incident.type == IncidentType.FRESHNESS:
            return self._freshness_plan(incident, blast)
        if incident.type == IncidentType.DATA_QUALITY:
            return self._dq_plan(incident, blast)
        if incident.type == IncidentType.SCHEMA_DRIFT:
            return self._schema_plan(incident, blast)
        if incident.type == IncidentType.LINEAGE_BREAK:
            return self._lineage_break_plan(incident, blast)
        return self._generic_plan(incident, blast)

    def _context_banner(self, incident: Incident) -> str:
        cols = ", ".join(_field_names(incident.entity.schema_fields)) or "(no schema)"
        q_preview = ""
        if incident.sample_queries:
            q_preview = "\n-- Observed query (DataHub):\n-- " + incident.sample_queries[0].replace(
                "\n", "\n-- "
            )
        return (
            f"-- Generated by Remedi for incident: {incident.id}\n"
            f"-- Grounded in DataHub URN: {incident.entity.urn}\n"
            f"-- Schema columns: {cols}"
            f"{q_preview}\n"
        )

    def _freshness_plan(self, incident: Incident, blast: BlastRadius) -> RemediationPlan:
        fields = incident.entity.schema_fields
        names = _field_names(fields)
        from_queries = _extract_columns_from_queries(incident.sample_queries, names)
        partition = next(
            (
                name
                for name in from_queries
                if any(token in name.lower() for token in ("date", "day", "dt"))
                and "datetime" not in name.lower()
            ),
            None,
        ) or _pick_field(
            fields,
            "date",
            "day",
            "dt",
            default=names[0] if names else "event_date",
        )
        assert partition is not None
        ts_fields = _timestamp_fields(fields)
        watermark = next(
            (name for name in from_queries if name in ts_fields and name != partition),
            next((name for name in ts_fields if name != partition), partition),
        )
        metrics = _numeric_fields(fields)
        metric_cols = [c for c in from_queries if c in metrics] or metrics
        sum_col = metric_cols[0] if metric_cols else None
        source_sql = _source_ref(incident.entity.name, incident.entity.platform)
        sla = incident.details.get("sla_hours", 6)
        pipeline = incident.details.get("pipeline", "airflow.pipeline")
        table = incident.entity.name
        short = table.split(".")[-1].replace("-", "_")
        mart_name = f"mart_{short}_daily"
        dag_file = f"{pipeline.replace('.', '_')}_remedi_guard.py"
        table_parts = table.split(".")
        source_name = (
            table_parts[-2] if len(table_parts) >= 2 else (incident.entity.platform or "raw")
        )
        source_table = table_parts[-1]
        grounded = [
            f"schema:{partition}",
            f"schema:{watermark}",
            f"urn:{incident.entity.urn}",
        ]
        if sum_col:
            grounded.append(f"schema:{sum_col}")
        if incident.sample_queries:
            grounded.append("queries:datahub")

        select_metrics = "COUNT(*) AS row_count"
        if sum_col:
            select_metrics += f",\n    SUM({sum_col}) AS metric_sum"

        dbt_sql = f"""{{{{
  config(
    materialized='incremental',
    unique_key='{partition}',
    on_schema_change='append_new_columns',
    tags=['remedi', 'freshness-guard']
  )
}}}}

{self._context_banner(incident)}
WITH source AS (
  SELECT
    {partition},
    {select_metrics},
    MAX({watermark}) AS last_event
  FROM {source_sql}
  WHERE {partition} >= DATEADD('day', -7, CURRENT_DATE)
  {{% if is_incremental() %}}
    AND {partition} > (SELECT COALESCE(MAX({partition}), '1900-01-01') FROM {{{{ this }}}})
  {{% endif %}}
  GROUP BY 1
),
freshness_check AS (
  SELECT
    *,
    DATEDIFF('hour', last_event, CURRENT_TIMESTAMP()) AS lag_hours
  FROM source
)
SELECT
  {partition} AS event_date,
  row_count,
  {"metric_sum," if sum_col else ""}
  last_event,
  lag_hours,
  IFF(lag_hours > {sla}, 'STALE', 'OK') AS freshness_status
FROM freshness_check
"""

        airflow = f'''"""Airflow patch generated by Remedi — incident {incident.id}.

Owners discovered through DataHub lineage are retained in remediation evidence.
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator

default_args = {{
    "owner": "data-oncall",
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
}}

with DAG(
    dag_id="{pipeline.replace(".", "_")}_remedi_guard",
    start_date=datetime(2026, 1, 1),
    schedule="0 */{max(1, int(sla) // 2)} * * *",
    catchup=False,
    tags=["remedi", "freshness"],
    default_args=default_args,
) as dag:
    start = EmptyOperator(task_id="start")

    check_source = BashOperator(
        task_id="check_source_freshness",
{_python_keyword_string("bash_command", f"dbt source freshness --select source:{source_name}.{source_table}")}
    )

    run_dbt = BashOperator(
        task_id="dbt_run_freshness_mart",
{_python_keyword_string("bash_command", f"dbt run --select {mart_name} --vars '{{incident_id: {incident.id}}}'")}
    )

    assert_fresh = BashOperator(
        task_id="assert_freshness_sla",
        bash_command=(
            "dbt test --select {mart_name},test_type:freshness "
            "|| (echo 'Remedi: freshness still breached for "
            "{table}' && exit 1)"
        ),
    )

    start >> check_source >> run_dbt >> assert_fresh
'''

        test_yml = f"""version: 2
models:
  - name: {mart_name}
    description: >
      Daily mart with Remedi freshness guard for {table}.
      Partition key `{partition}` and watermark `{watermark}` taken from DataHub schema.
    columns:
      - name: event_date
        tests: [not_null, unique]
      - name: freshness_status
        tests:
          - accepted_values:
              values: ['OK']
              config:
                severity: error
                where: "event_date = CURRENT_DATE - 1"
"""

        artifacts = [
            GeneratedArtifact(
                path=f"dbt/models/{mart_name}.sql",
                kind="dbt_model",
                description=f"Incremental mart using schema columns {partition}/{watermark}"
                + (f"/{sum_col}" if sum_col else ""),
                content=dbt_sql,
                grounded_in=grounded,
            ),
            GeneratedArtifact(
                path="dbt/models/schema.yml",
                kind="dbt_test",
                description="Fail-closed freshness test on freshness_status",
                content=test_yml,
                grounded_in=[f"schema:{partition}", f"sla_hours:{sla}"],
            ),
            GeneratedArtifact(
                path=f"airflow/dags/{dag_file}",
                kind="airflow_dag",
                description=(
                    "Fail-closed source freshness gate + dbt run/test; "
                    "retains DataHub owner URNs in evidence"
                ),
                content=airflow,
                grounded_in=[f"owners:{len(blast.owner_notify)}", f"pipeline:{pipeline}"],
            ),
        ]
        artifacts.extend(self._ml_guard_artifacts(incident, blast, partition, watermark, sla))
        artifacts.extend(self._orchestrator_artifacts(incident, blast, mart_name, pipeline))

        impacted = ", ".join(d.entity.name for d in blast.downstream) or "none"
        return RemediationPlan(
            summary=(
                f"Restore freshness SLA ({sla}h) for {table} using schema keys "
                f"`{partition}`/`{watermark}`; protect {blast.total_impacted} downstream "
                f"({impacted})."
            ),
            root_cause=str(
                incident.details.get(
                    "failure_hint",
                    "Upstream partition delay without alerting / sensor guardrails",
                )
            ),
            steps=[
                "Add a fail-closed dbt source-freshness gate before downstream work",
                f"Rebuild mart with lag on `{watermark}` and fail-closed dbt test",
                *(
                    ["Emit ML feature-freshness guard for impacted production models"]
                    if blast.ml_model_count
                    else []
                ),
                "Notify lineage owners and tag the approved remediation pending validation",
                "Save the approved-remediation document so next on-call inherits context",
            ],
            artifacts=artifacts,
            write_back=CatalogWriteBack(
                urn=incident.entity.urn,
                actions=[
                    "add_tags:remedi-applied,remediation-pending-validation,freshness-guard",
                    "update_description",
                    "save_document:Remedi Approved Remediation",
                    f"notify_owners:{len(blast.owner_notify)}",
                ],
            ),
            codegen_mode="template",
        )

    def _ml_guard_artifacts(
        self,
        incident: Incident,
        blast: BlastRadius,
        partition: str,
        watermark: str,
        sla: Any,
    ) -> list[GeneratedArtifact]:
        models = blast.ml_models
        if not models:
            return []
        model_names = [m.entity.name for m in models]
        owners = sorted({o for m in models for o in m.entity.owners} | set(blast.owner_notify))
        py = f'''"""ML feature freshness guard — generated by Remedi ({incident.id}).

Blocks training/serving refresh when upstream feature source is stale.
Grounded in DataHub ML lineage for: {", ".join(model_names)}
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# DataHub ML models in blast radius
PROTECTED_MODELS = {_python_string_list(model_names)}
FEATURE_SOURCE_URN = (
    {json.dumps(incident.entity.urn)}
)
PARTITION_COL = {json.dumps(partition)}
WATERMARK_COL = {json.dumps(watermark)}
SLA_HOURS = {int(sla)}
OWNERS = {_python_string_list(owners)}


def feature_lag_hours(last_event: datetime, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    if last_event.tzinfo is None:
        last_event = last_event.replace(tzinfo=timezone.utc)
    return (now - last_event).total_seconds() / 3600.0


def assert_features_fresh(last_event: datetime) -> None:
    lag = feature_lag_hours(last_event)
    if lag > SLA_HOURS:
        raise RuntimeError(
            f"Remedi ML guard: feature source lag {{lag:.1f}}h exceeds SLA {{SLA_HOURS}}h "
            f"for models {{PROTECTED_MODELS}}. Notify {{OWNERS}}."
        )


def should_skip_training(last_event: datetime) -> bool:
    """Return True if training job should no-op until features catch up."""
    return feature_lag_hours(last_event) > SLA_HOURS


if __name__ == "__main__":
    # Example: wire into training entrypoint before fit()
    stale_example = datetime.now(timezone.utc) - timedelta(hours=SLA_HOURS + 1)
    if should_skip_training(stale_example):
        print("SKIP training — upstream features stale (Remedi)")
'''
        yml = f"""# Model card patch — Remedi incident {incident.id}
models:
{chr(10).join(f"  - name: {m.entity.name}" + chr(10) + f"    owners: {m.entity.owners}" + chr(10) + f"    upstream_feature_urn: {incident.entity.urn}" + chr(10) + f"    freshness_sla_hours: {sla}" + chr(10) + "    remedi_guard: ml/feature_freshness_guard.py" for m in models)}
"""
        return [
            GeneratedArtifact(
                path="ml/feature_freshness_guard.py",
                kind="ml_guard",
                description=f"Skip/fail training when features stale — protects {', '.join(model_names)}",
                content=py,
                grounded_in=[
                    f"ml_models:{len(models)}",
                    f"schema:{partition}",
                    f"schema:{watermark}",
                    *[f"urn:{m.entity.urn}" for m in models],
                ],
            ),
            GeneratedArtifact(
                path="ml/model_card_patch.yml",
                kind="ml_doc",
                description="Model card freshness contract from DataHub ML lineage",
                content=yml,
                grounded_in=[f"ml_models:{len(models)}"],
            ),
        ]

    def _dq_ml_guard_artifacts(
        self, incident: Incident, blast: BlastRadius, feature_col: str
    ) -> list[GeneratedArtifact]:
        models = blast.ml_models
        features = [d for d in blast.downstream if d.entity.type.lower() == "mlfeature"]
        if not models and not features:
            return []
        model_names = [m.entity.name for m in models]
        feature_names = [f.entity.name for f in features]
        threshold = incident.details.get("threshold", 0.02)
        py = f'''"""ML feature quality gate — generated by Remedi ({incident.id}).

Blocks inference/training when feature `{feature_col}` null-rate exceeds threshold.
Protects models: {", ".join(model_names) or "(none)"}
Features: {", ".join(feature_names) or "(derived from source)"}
"""

from __future__ import annotations

PROTECTED_MODELS = {_python_string_list(model_names)}
FEATURE_NAMES = {_python_string_list(feature_names)}
FEATURE_COL = {json.dumps(feature_col)}
SOURCE_URN = {json.dumps(incident.entity.urn)}
NULL_RATE_THRESHOLD = float({threshold})


def null_rate(values: list) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if v is None) / len(values)


def assert_feature_quality(batch_values: list) -> None:
    rate = null_rate(batch_values)
    if rate > NULL_RATE_THRESHOLD:
        raise RuntimeError(
            f"Remedi DQ ML gate: {{FEATURE_COL}} null_rate={{rate:.3f}} > {{NULL_RATE_THRESHOLD}} "
            f"for models {{PROTECTED_MODELS}} / features {{FEATURE_NAMES}}"
        )


def should_skip_scoring(batch_values: list) -> bool:
    return null_rate(batch_values) > NULL_RATE_THRESHOLD
'''
        return [
            GeneratedArtifact(
                path="ml/feature_quality_gate.py",
                kind="ml_guard",
                description=f"Null-rate gate on `{feature_col}` for ML models/features in blast radius",
                content=py,
                grounded_in=[
                    f"schema:{feature_col}",
                    f"ml_models:{len(models)}",
                    f"ml_features:{len(features)}",
                    f"urn:{incident.entity.urn}",
                ],
            )
        ]

    def _orchestrator_artifacts(
        self,
        incident: Incident,
        blast: BlastRadius,
        mart_name: str,
        pipeline: str,
    ) -> list[GeneratedArtifact]:
        """Emit Dagster + Prefect variants so stacks beyond Airflow can merge."""
        dagster = f'''"""Dagster asset job — Remedi {incident.id}."""

import subprocess

from dagster import AssetExecutionContext, Definitions, asset

{_python_string_assignment("DATAHUB_SOURCE_URN", incident.entity.urn)}
DATAHUB_OWNER_URNS = {_python_string_list(blast.owner_notify)}


@asset(name="{mart_name}", group_name="remedi")
def {mart_name.replace("-", "_")}(context: AssetExecutionContext) -> None:
    """Freshness-guarded mart grounded in DataHub metadata."""
    context.log.info("DataHub source: %s", DATAHUB_SOURCE_URN)
    context.log.info("DataHub owners: %s", DATAHUB_OWNER_URNS)
    subprocess.run(["dbt", "run", "--select", "{mart_name}"], check=True)
    subprocess.run(["dbt", "test", "--select", "{mart_name}"], check=True)


defs = Definitions(assets=[{mart_name.replace("-", "_")}])
'''
        prefect = f'''"""Prefect flow — Remedi {incident.id}."""

import subprocess

from prefect import flow, task


@task
def dbt_run() -> None:
    subprocess.run(["dbt", "run", "--select", "{mart_name}"], check=True)


@task
def dbt_test() -> None:
    subprocess.run(["dbt", "test", "--select", "{mart_name}"], check=True)


@flow(name="{pipeline.replace(".", "_")}_remedi")
def remedi_freshness_flow() -> None:
    dbt_run()
    dbt_test()


if __name__ == "__main__":
    remedi_freshness_flow()
'''
        return [
            GeneratedArtifact(
                path=f"dagster/assets/{mart_name}.py",
                kind="dagster",
                description="Dagster asset variant of freshness remediation",
                content=dagster,
                grounded_in=[f"urn:{incident.entity.urn}", f"owners:{len(blast.owner_notify)}"],
            ),
            GeneratedArtifact(
                path=f"prefect/flows/{mart_name}_flow.py",
                kind="prefect",
                description="Prefect flow variant of freshness remediation",
                content=prefect,
                grounded_in=[f"urn:{incident.entity.urn}", f"pipeline:{pipeline}"],
            ),
        ]

    def _dq_plan(self, incident: Incident, blast: BlastRadius) -> RemediationPlan:
        fields = incident.entity.schema_fields
        names = _field_names(fields)
        assertion = str(incident.details.get("assertion", ""))
        if "null" not in assertion.lower():
            return self._generic_plan(incident, blast)

        # Prefer the field named by the assertion, then a nullable schema field.
        col = None
        for f in fields:
            if f.name.lower() in assertion.lower():
                col = f.name
                break
        if not col:
            col = next(
                (f.name for f in fields if f.nullable and f.name.lower() not in ("id",)), None
            )
        if not col:
            col = names[0] if names else "value"
        timestamp_fields = _timestamp_fields(fields)
        ts_col = _pick_field(
            fields,
            "measured",
            "time",
            "at",
            "date",
            default=timestamp_fields[0] if timestamp_fields else None,
        )
        table = incident.entity.name
        short = re.sub(r"[^A-Za-z0-9_]+", "_", table.split(".")[-1]).strip("_") or "dataset"
        null_rate = incident.details.get("null_rate", "unknown")
        grounded = [f"schema:{col}", f"urn:{incident.entity.urn}"]
        time_filter = ""
        test_filter = ""
        if ts_col:
            grounded.append(f"schema:{ts_col}")
            time_filter = f"\n  AND {ts_col} > NOW() - INTERVAL '1 day'"
            test_filter = (
                "\n              config:"
                "\n                severity: error"
                f"\n                where: \"{ts_col} > CURRENT_TIMESTAMP - INTERVAL '1 day'\""
            )

        sql = f"""{self._context_banner(incident)}
-- Remedi DQ quarantine + clean view for {table}
-- Assertion: {assertion or col + "_not_null"} (null_rate={null_rate})

CREATE OR REPLACE VIEW {table}_quarantine AS
SELECT *
FROM {table}
WHERE {col} IS NULL{time_filter};

CREATE OR REPLACE VIEW {table}_clean AS
SELECT *
FROM {table}
WHERE {col} IS NOT NULL;
"""
        dbt_test = f"""version: 2
models:
  - name: {short}_clean
    description: Fail-closed clean view for {table}; column `{col}` from DataHub schema.
    columns:
      - name: {col}
        tests:
          - not_null:{test_filter}
"""
        notice = ""
        if blast.downstream:
            notice = f"""# DQ impact notice — {table}

## Column
`{col}` null-rate spike ({null_rate}).

## Blast radius ({blast.total_impacted} assets)
{chr(10).join(f"- {d.entity.name} ({d.entity.type}): {d.impact_reason}" for d in blast.downstream)}

## Owners
{chr(10).join(f"- {o}" for o in blast.owner_notify)}
"""
        artifacts = [
            GeneratedArtifact(
                path=f"sql/{short}_quarantine_and_clean.sql",
                kind="sql",
                description=f"Quarantine null `{col}` rows and publish a fail-closed clean view",
                content=sql,
                grounded_in=grounded,
            ),
            GeneratedArtifact(
                path=f"dbt/models/{short}_clean.yml",
                kind="dbt_test",
                description=f"Fail-closed not_null on `{col}`",
                content=dbt_test,
                grounded_in=[f"schema:{col}"],
            ),
        ]
        if notice:
            artifacts.append(
                GeneratedArtifact(
                    path="docs/dq_impact_notice.md",
                    kind="doc",
                    description="Owner notice from DataHub lineage blast radius",
                    content=notice,
                    grounded_in=[f"downstream:{blast.total_impacted}"],
                )
            )
        # Protect ML models / features in blast radius with DQ-specific guard
        artifacts.extend(self._dq_ml_guard_artifacts(incident, blast, col))
        return RemediationPlan(
            summary=f"Quarantine null `{col}` rows and publish a fail-closed clean view "
            f"(rate={null_rate}); "
            f"{blast.total_impacted} downstream assets in blast radius.",
            root_cause=str(
                incident.details.get(
                    "failure_hint",
                    "The assertion identifies null rows; the upstream source cause requires investigation.",
                )
            ),
            steps=[
                f"Land violating rows in `{table}_quarantine`",
                f"Publish `{table}_clean` with invalid `{col}` rows excluded",
                "Re-enable the assertion on the clean view and keep the source pending validation",
                *(
                    ["Emit ML feature guard for models consuming this feature"]
                    if blast.ml_model_count
                    else []
                ),
                *(["Notify clinical/dashboard owners from lineage"] if blast.downstream else []),
            ],
            artifacts=artifacts,
            write_back=CatalogWriteBack(
                urn=incident.entity.urn,
                actions=[
                    "add_tags:remedi-applied,remediation-pending-validation,dq-guard",
                    "update_description",
                    "save_document",
                ],
            ),
        )

    def _schema_plan(self, incident: Incident, blast: BlastRadius) -> RemediationPlan:
        fields = incident.entity.schema_fields
        names = _field_names(fields)
        col = incident.details.get("column") or _pick_field(
            fields, "amount", "value", "price", default=names[-1] if names else "amount"
        )
        before = incident.details.get("before", "NUMBER(10,2)")
        after = incident.details.get("after") or next(
            (f.type for f in fields if f.name == col), "NUMBER(18,4)"
        )
        id_col = _pick_field(fields, "order", "id", default=names[0] if names else "id")
        ts_col = _pick_field(
            fields,
            "ordered",
            "time",
            "at",
            "date",
            default=_timestamp_fields(fields)[0] if _timestamp_fields(fields) else "ordered_at",
        )
        source_sql = _source_ref(incident.entity.name, incident.entity.platform)
        assert col and id_col and ts_col

        sql = f"""{{{{ config(materialized='view', tags=['remedi', 'schema-compat']) }}}}

{self._context_banner(incident)}
-- Upstream widened {before} -> {after}; protect downstream casts.

SELECT
  {id_col},
  {ts_col},
  {col} AS {col}_native,
  {col}::{before} AS {col}_legacy,
  {col}::{after} AS {col}_full
FROM {source_sql}
"""
        notice = f"""# Schema change notice — {incident.entity.name}

## Change
Column `{col}` widened from `{before}` to `{after}` (types from DataHub / incident details).

## Blast radius ({blast.total_impacted} assets)
{chr(10).join(f"- {d.entity.name} ({d.entity.type}): {d.impact_reason}" for d in blast.downstream)}

## Owners to notify
{chr(10).join(f"- {o}" for o in blast.owner_notify)}

## Recommended action
Merge the compat view, update Looker explores to `{col}_full`, then remove `{col}_legacy`.
"""
        return RemediationPlan(
            summary=f"Ship schema-compat layer for `{col}` ({before} → {after}).",
            root_cause="Type widening without coordinated downstream contract update",
            steps=[
                "Deploy compat view with legacy + full precision columns",
                "Notify dashboard/mart owners from lineage",
                "Document change in DataHub for future agents",
            ],
            artifacts=[
                GeneratedArtifact(
                    path="dbt/models/orders_amount_compat.sql",
                    kind="dbt_model",
                    description=f"Compat view for `{col}` using DataHub schema types",
                    content=sql,
                    grounded_in=[f"schema:{col}", f"type:{after}", f"urn:{incident.entity.urn}"],
                ),
                GeneratedArtifact(
                    path="docs/schema_change_notice.md",
                    kind="doc",
                    description="Owner-facing impact notice from DataHub lineage",
                    content=notice,
                    grounded_in=[f"downstream:{blast.total_impacted}"],
                ),
            ],
            write_back=CatalogWriteBack(
                urn=incident.entity.urn,
                actions=[
                    "add_tags:remedi-applied,remediation-pending-validation,schema-compat",
                    "update_description",
                    "save_document",
                ],
            ),
        )

    def _lineage_break_plan(self, incident: Incident, blast: BlastRadius) -> RemediationPlan:
        broken_edge = incident.details.get("broken_edge", {})
        upstream = broken_edge.get("upstream", incident.entity.urn)
        downstream = broken_edge.get("downstream", "unknown")
        expected_col = broken_edge.get("missing_column") or (
            _field_names(incident.entity.schema_fields)[0]
            if incident.entity.schema_fields
            else "id"
        )
        upstream_entity = next(
            (impact.entity for impact in blast.upstream if impact.entity.urn == upstream),
            None,
        )
        if upstream_entity is None:
            return self._generic_plan(incident, blast)
        upstream_fields = {field.name for field in upstream_entity.schema_fields}
        primary_fields = {field.name for field in incident.entity.schema_fields}
        join_candidates = sorted(
            (primary_fields & upstream_fields) - {expected_col},
            key=lambda name: (
                0 if any(token in name.lower() for token in ("email", "key", "id")) else 1,
                name,
            ),
        )
        if expected_col not in upstream_fields or not join_candidates:
            return self._generic_plan(incident, blast)
        join_col = join_candidates[0]
        primary_model = incident.entity.name.split(".")[-1]
        repaired_model = f"{primary_model}_repaired"
        dbt = f"""{{{{
  config(materialized='view', tags=['remedi', 'lineage-repair'])
}}}}

{self._context_banner(incident)}
-- Repair broken lineage edge:
--   upstream:   {upstream}
--   downstream: {downstream}
-- Restore missing column `{expected_col}` from `{upstream_entity.name}`.
-- Join key `{join_col}` exists in both DataHub schemas.

SELECT
  d.*,
  u.{expected_col} AS {expected_col}
FROM {{{{ ref('{primary_model}') }}}} d
LEFT JOIN {upstream_entity.name} u
  ON d.{join_col} = u.{join_col}
WHERE u.{expected_col} IS NOT NULL
"""
        airflow = f'''"""Lineage repair DAG — Remedi {incident.id}."""

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="remedi_lineage_repair_{incident.id.replace("-", "_")}",
    start_date=datetime(2026, 1, 1),
    schedule="@daily",
    catchup=False,
    tags=["remedi", "lineage-break"],
) as dag:
    BashOperator(
        task_id="dbt_run_lineage_repair",
{_python_keyword_string("bash_command", f"dbt run --select {repaired_model} && dbt test --select {repaired_model}")}
    )
'''
        return RemediationPlan(
            summary=(
                f"Restore `{expected_col}` from `{upstream_entity.name}` using schema-grounded "
                f"join key `{join_col}`, then validate downstream `{downstream}`."
            ),
            root_cause=str(
                incident.details.get(
                    "failure_hint", "Join key dropped or renamed without lineage update"
                )
            ),
            steps=[
                f"Restore `{expected_col}` from the upstream source via `{join_col}`",
                "Re-run downstream dbt + tests",
                "Tag the approved lineage repair pending validation and document the edge",
            ],
            artifacts=[
                GeneratedArtifact(
                    path=f"dbt/models/{repaired_model}.sql",
                    kind="dbt_model",
                    description=(
                        f"Restores `{expected_col}` from `{upstream_entity.name}` via `{join_col}`"
                    ),
                    content=dbt,
                    grounded_in=[
                        f"context_schema:{expected_col}",
                        f"schema:{join_col}",
                        f"upstream:{upstream}",
                        f"downstream:{downstream}",
                    ],
                ),
                GeneratedArtifact(
                    path="airflow/dags/lineage_repair.py",
                    kind="airflow_dag",
                    description="Daily repair + test until edge is healthy",
                    content=airflow,
                    grounded_in=[f"incident:{incident.id}"],
                ),
            ],
            write_back=CatalogWriteBack(
                urn=incident.entity.urn,
                actions=[
                    "add_tags:remedi-applied,remediation-pending-validation,lineage-repair",
                    "update_description",
                    "save_document",
                ],
            ),
        )

    def _generic_plan(self, incident: Incident, blast: BlastRadius) -> RemediationPlan:
        cols = ", ".join(_field_names(incident.entity.schema_fields)) or "*"
        body = f"{self._context_banner(incident)}SELECT {cols}\nFROM {incident.entity.name}\nLIMIT 100;\n"
        return RemediationPlan(
            summary=incident.title,
            root_cause="See incident details",
            steps=["Inspect lineage", "Generate fix", "Write back to DataHub"],
            artifacts=[
                GeneratedArtifact(
                    path="sql/generic_fix.sql",
                    kind="sql",
                    description="Schema-grounded inspection query",
                    content=body,
                    grounded_in=[f"urn:{incident.entity.urn}"],
                )
            ],
            write_back=CatalogWriteBack(
                urn=incident.entity.urn,
                actions=["add_tags:remedi-applied,remediation-pending-validation", "save_document"],
            ),
        )

    def _llm_plan(self, incident: Incident, blast: BlastRadius) -> RemediationPlan | None:
        """Generate a plan through OpenAI when that provider is explicitly configured."""
        import json
        import urllib.request

        schema = [
            {"name": f.name, "type": f.type, "nullable": f.nullable}
            for f in incident.entity.schema_fields
        ]
        payload = {
            "model": self.openai_model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Remedi, a data remediation agent. "
                        "Return ONLY JSON with keys: summary, root_cause, steps (array), "
                        "artifacts (array of {path, kind, description, content}). "
                        "Only use columns present in the provided schema. No invented fields."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "incident": incident.model_dump(mode="json"),
                            "schema": schema,
                            "sample_queries": incident.sample_queries,
                            "blast_radius": [
                                {
                                    "name": d.entity.name,
                                    "type": d.entity.type,
                                    "reason": d.impact_reason,
                                }
                                for d in blast.downstream
                            ],
                        }
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.openai_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        artifacts = [
            GeneratedArtifact(
                path=a["path"],
                kind=a.get("kind", "sql"),
                description=a.get("description", "LLM-generated artifact"),
                content=a["content"],
                grounded_in=["llm", f"urn:{incident.entity.urn}", "schema:datahub"],
            )
            for a in parsed.get("artifacts", [])
        ]
        if not artifacts:
            return None
        return RemediationPlan(
            summary=parsed.get("summary", incident.title),
            root_cause=parsed.get("root_cause", "See details"),
            steps=list(parsed.get("steps", [])),
            artifacts=artifacts,
            write_back=CatalogWriteBack(
                urn=incident.entity.urn,
                actions=[
                    "add_tags:remedi-applied,remediation-pending-validation",
                    "update_description",
                    "save_document",
                ],
            ),
            codegen_mode="llm",
        )
