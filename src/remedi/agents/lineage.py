from __future__ import annotations

from remedi.connectors.datahub import DataHubConnector
from remedi.models.incident import BlastRadius, DownstreamImpact, Incident


class LineageAgent:
    """Traces blast radius via BFS (downstream + upstream) with column impact."""

    def __init__(self, connector: DataHubConnector) -> None:
        self.connector = connector

    def analyze(self, incident: Incident, max_hops: int = 3) -> BlastRadius:
        raw_down = self.connector.get_lineage_downstream(incident.entity.urn, max_hops=max_hops)
        raw_up = self.connector.get_lineage_upstream(incident.entity.urn, max_hops=max_hops)
        _ = self.connector.get_dataset_queries(incident.entity.urn)

        downstream: list[DownstreamImpact] = []
        upstream: list[DownstreamImpact] = []
        owners: set[str] = set(incident.entity.owners)
        dashboard_count = dataset_count = ml_model_count = ml_feature_count = 0
        edges: list[dict] = []
        column_impacts: list[dict] = []

        for item in raw_down:
            entity = item["entity"]
            downstream.append(
                DownstreamImpact(
                    entity=entity, hop=item["hop"], impact_reason=item["impact_reason"]
                )
            )
            edges.append(
                {
                    "from": item.get("parent_urn", incident.entity.urn),
                    "to": entity.urn,
                    "to_name": entity.name,
                    "to_type": entity.type,
                    "hop": item["hop"],
                    "reason": item["impact_reason"],
                    "direction": "downstream",
                    "columns": item.get("columns", []),
                }
            )
            for col in item.get("columns", []):
                column_impacts.append(
                    {
                        "column": col,
                        "consumer": entity.name,
                        "consumer_type": entity.type,
                        "hop": item["hop"],
                        "reason": item["impact_reason"],
                    }
                )
            owners.update(entity.owners)
            t = entity.type.lower()
            if t == "dashboard":
                dashboard_count += 1
            elif t == "mlmodel":
                ml_model_count += 1
            elif t == "mlfeature":
                ml_feature_count += 1
            else:
                dataset_count += 1

        for item in raw_up:
            entity = item["entity"]
            upstream.append(
                DownstreamImpact(
                    entity=entity, hop=item["hop"], impact_reason=item["impact_reason"]
                )
            )
            edges.append(
                {
                    "from": entity.urn,
                    "to": item.get("parent_urn", incident.entity.urn),
                    "to_name": entity.name,
                    "to_type": entity.type,
                    "hop": item["hop"],
                    "reason": item["impact_reason"],
                    "direction": "upstream",
                    "columns": item.get("columns", []),
                }
            )
            owners.update(entity.owners)

        # Incident details may declare column-level breakage
        details_cols = incident.details.get("columns") or incident.details.get("breaking_for")
        if isinstance(details_cols, list):
            for c in details_cols:
                if isinstance(c, str) and not any(ci["column"] == c for ci in column_impacts):
                    column_impacts.append(
                        {
                            "column": c if "." not in c else c.split(".")[-1],
                            "consumer": "(incident details)",
                            "consumer_type": "note",
                            "hop": 0,
                            "reason": "declared in incident details",
                        }
                    )
        col = incident.details.get("column")
        if col:
            column_impacts.insert(
                0,
                {
                    "column": col,
                    "consumer": incident.entity.name,
                    "consumer_type": "source",
                    "hop": 0,
                    "reason": "primary affected column",
                },
            )

        return BlastRadius(
            source_urn=incident.entity.urn,
            downstream=downstream,
            upstream=upstream,
            owner_notify=sorted(owners),
            dashboard_count=dashboard_count,
            dataset_count=dataset_count,
            ml_model_count=ml_model_count,
            ml_feature_count=ml_feature_count,
            edges=edges,
            column_impacts=column_impacts,
        )
