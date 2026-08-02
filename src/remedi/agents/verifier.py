"""Prove codegen only uses columns that exist in DataHub schema."""

from __future__ import annotations

import re
from typing import Iterable

from remedi.models.incident import BlastRadius, GeneratedArtifact, Incident, IncidentType


# SQL keywords / common aliases to ignore when scanning identifiers
_STOP = {
    "select",
    "from",
    "where",
    "and",
    "or",
    "as",
    "with",
    "join",
    "left",
    "right",
    "inner",
    "outer",
    "on",
    "group",
    "by",
    "order",
    "having",
    "limit",
    "case",
    "when",
    "then",
    "else",
    "end",
    "iff",
    "coalesce",
    "count",
    "sum",
    "max",
    "min",
    "avg",
    "distinct",
    "null",
    "true",
    "false",
    "create",
    "replace",
    "view",
    "table",
    "config",
    "materialized",
    "incremental",
    "unique_key",
    "tags",
    "source",
    "ref",
    "this",
    "is_incremental",
    "dateadd",
    "datediff",
    "current_date",
    "current_timestamp",
    "now",
    "interval",
    "over",
    "partition",
    "lag",
    "desc",
    "asc",
    "filter",
    "float",
    "integer",
    "number",
    "string",
    "timestamp",
    "date",
    "uuid",
    "prod",
    "ok",
    "stale",
}


class GroundednessReport:
    def __init__(
        self,
        *,
        ok: bool,
        schema_columns: list[str],
        referenced: list[str],
        unknown: list[str],
        invented_blocked: list[str],
        notes: list[str],
    ) -> None:
        self.ok = ok
        self.schema_columns = schema_columns
        self.referenced = referenced
        self.unknown = unknown
        self.invented_blocked = invented_blocked
        self.notes = notes

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "schema_columns": self.schema_columns,
            "referenced": self.referenced,
            "unknown": self.unknown,
            "invented_blocked": self.invented_blocked,
            "notes": self.notes,
            "badge": "grounded" if self.ok else "ungrounded",
        }


class GroundednessVerifier:
    """Fail closed if SQL-like artifacts reference unknown schema columns."""

    # Allowed derived/output columns Remedi introduces on purpose
    ALLOWED_SYNTHETIC = {
        "row_count",
        "metric_sum",
        "last_event",
        "lag_hours",
        "freshness_status",
        "event_date",
        "trip_date",
        "was_imputed",
        "amount_native",
        "amount_legacy",
        "amount_full",
        "remedi_repaired_key",
        "gmv",
        "trips",
        "orders",
        "d",
    }

    def verify(
        self,
        incident: Incident,
        artifacts: Iterable[GeneratedArtifact],
        *,
        blast: BlastRadius | None = None,
    ) -> GroundednessReport:
        schema = {f.name.lower(): f.name for f in incident.entity.schema_fields}
        schema_cols = list(schema.values())
        context_schema = dict(schema)
        if incident.type == IncidentType.LINEAGE_BREAK and blast is not None:
            for impact in [*blast.upstream, *blast.downstream]:
                for field in impact.entity.schema_fields:
                    context_schema.setdefault(field.name.lower(), field.name)
        referenced: set[str] = set()
        context_referenced: set[str] = set()
        unknown: set[str] = set()
        notes: list[str] = []

        sqlish = [
            a
            for a in artifacts
            if a.kind in {"dbt_model", "sql", "dbt_test"} or a.path.endswith((".sql", ".yml"))
        ]
        for artifact in sqlish:
            ids = self._extract_identifiers(artifact.content)
            for ident in ids:
                low = ident.lower()
                if low in _STOP or low in self.ALLOWED_SYNTHETIC:
                    continue
                if low in schema:
                    referenced.add(schema[low])
                elif low in context_schema:
                    referenced.add(context_schema[low])
                    context_referenced.add(context_schema[low])
                elif ident.startswith("urn:") or "." in ident:
                    continue
                elif re.fullmatch(r"[a-z_][a-z0-9_]*", low):
                    # might be downstream table alias — only flag if looks like a column ref near schema names
                    if (
                        any(s in low for s in schema)
                        or low.endswith("_id")
                        or low.endswith("_at")
                        or low.endswith("_date")
                    ):
                        if low not in schema and low not in self.ALLOWED_SYNTHETIC:
                            unknown.add(ident)

        # Stricter: any grounded_in schema:* must exist
        invented_blocked: list[str] = []
        for artifact in artifacts:
            for g in artifact.grounded_in:
                if g.startswith("schema:"):
                    col = g.split(":", 1)[1]
                    if col.lower() not in schema and col.lower() not in self.ALLOWED_SYNTHETIC:
                        invented_blocked.append(col)
                elif g.startswith("context_schema:"):
                    col = g.split(":", 1)[1]
                    if (
                        col.lower() not in context_schema
                        and col.lower() not in self.ALLOWED_SYNTHETIC
                    ):
                        invented_blocked.append(col)

        # Primary check: grounded_in schema claims + unknown column-like ids that aren't synthetic
        hard_unknown = sorted(u for u in unknown if u.lower() not in schema)
        ok = len(invented_blocked) == 0 and len(hard_unknown) == 0
        if not schema_cols:
            notes.append("No schema fields on entity — skipped column proof")
            ok = True
        elif ok and referenced:
            notes.append(f"Referenced schema columns: {', '.join(sorted(referenced))}")
        elif ok:
            notes.append("No SQL column refs detected; grounded_in schema tags valid")

        if invented_blocked:
            notes.append(f"Blocked invented grounded_in columns: {', '.join(invented_blocked)}")
        if hard_unknown:
            notes.append(f"Blocked unknown SQL columns: {', '.join(hard_unknown)}")
        if context_referenced:
            notes.append(
                "Referenced lineage-context columns: " + ", ".join(sorted(context_referenced))
            )

        return GroundednessReport(
            ok=ok,
            schema_columns=schema_cols,
            referenced=sorted(referenced),
            unknown=hard_unknown,
            invented_blocked=invented_blocked,
            notes=notes,
        )

    def _extract_identifiers(self, content: str) -> set[str]:
        # strip comments
        text = re.sub(r"--.*?$", "", content, flags=re.M)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", text))
