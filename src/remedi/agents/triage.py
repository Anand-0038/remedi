from __future__ import annotations

from remedi.agents.detector import DetectorAgent
from remedi.agents.lineage import LineageAgent
from remedi.models.incident import (
    IncidentSeverity,
    IncidentType,
    TriageItem,
    TriageReport,
)


class TriageAgent:
    """Rank incidents using explainable risk grounded in DataHub context."""

    _SEVERITY_SCORE = {
        IncidentSeverity.CRITICAL: 45,
        IncidentSeverity.HIGH: 35,
        IncidentSeverity.MEDIUM: 22,
        IncidentSeverity.LOW: 10,
    }

    def __init__(self, detector: DetectorAgent, lineage: LineageAgent) -> None:
        self.detector = detector
        self.lineage = lineage

    def rank(self) -> TriageReport:
        items: list[TriageItem] = []
        for listed in self.detector.list():
            incident = self.detector.get(listed.id)
            blast = self.lineage.analyze(incident)
            items.append(self._score(incident, blast))

        items.sort(
            key=lambda item: (
                item.incident.status == "resolved",
                -item.risk_score,
                item.incident.id,
            )
        )
        return TriageReport(
            items=items,
            top_incident_id=next(
                (item.incident.id for item in items if item.incident.status == "open"),
                None,
            ),
        )

    def _score(self, incident, blast) -> TriageItem:
        if incident.status == "resolved":
            return TriageItem(
                incident=incident,
                blast_radius=blast,
                risk_score=0,
                priority="P3",
                reasons=["Already resolved in DataHub"],
                recommended_action="Monitor the resolution and retain its catalog document.",
            )

        severity = self._SEVERITY_SCORE[incident.severity]
        downstream = min(blast.total_impacted * 5, 25)
        dashboards = min(blast.dashboard_count * 5, 10)
        ml_models = min(blast.ml_model_count * 8, 16)
        ml_features = min(blast.ml_feature_count * 4, 8)
        ownership = 6 if not blast.owner_notify else min(len(blast.owner_notify), 4)
        score = min(severity + downstream + dashboards + ml_models + ml_features + ownership, 100)

        reasons = [
            f"{incident.severity.value} severity +{severity}",
            f"{blast.total_impacted} downstream assets +{downstream}",
        ]
        if blast.dashboard_count:
            reasons.append(f"{blast.dashboard_count} dashboards +{dashboards}")
        if blast.ml_model_count:
            reasons.append(f"{blast.ml_model_count} production ML models +{ml_models}")
        if blast.ml_feature_count:
            reasons.append(f"{blast.ml_feature_count} ML features +{ml_features}")
        reasons.append(
            f"{len(blast.owner_notify)} owners identified +{ownership}"
            if blast.owner_notify
            else f"No owner found +{ownership}"
        )

        priority = "P0" if score >= 70 else "P1" if score >= 50 else "P2" if score >= 30 else "P3"
        action = {
            IncidentType.FRESHNESS: "Restore the freshness guard before the next scheduled refresh.",
            IncidentType.DATA_QUALITY: "Quarantine invalid rows and protect downstream features.",
            IncidentType.SCHEMA_DRIFT: "Generate a compatibility patch before downstream deploys.",
            IncidentType.LINEAGE_BREAK: "Repair the lineage edge and validate affected consumers.",
        }[incident.type]
        if blast.ml_model_count:
            action += f" Block {blast.ml_model_count} affected model refresh(es) until verified."

        return TriageItem(
            incident=incident,
            blast_radius=blast,
            risk_score=score,
            priority=priority,
            reasons=reasons,
            recommended_action=action,
        )
