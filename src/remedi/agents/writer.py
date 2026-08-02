from __future__ import annotations

from remedi.connectors.datahub import DataHubConnector
from remedi.models.incident import (
    BlastRadius,
    CatalogWriteBack,
    Incident,
    RemediationPlan,
    WriteBackAction,
)


class WriterAgent:
    """Writes remediation knowledge back into DataHub so the next agent inherits it."""

    def __init__(self, connector: DataHubConnector) -> None:
        self.connector = connector

    def apply(
        self,
        incident: Incident,
        blast: BlastRadius,
        plan: RemediationPlan,
        *,
        dry_run: bool = False,
    ) -> CatalogWriteBack:
        before = {
            "tags": list(incident.entity.tags),
            "owners": list(incident.entity.owners),
            "description": incident.entity.description,
        }
        write = plan.write_back.model_copy(deep=True)
        write.dry_run = dry_run
        write.before = before

        tags = ["remedi-applied", "remediation-pending-validation"]
        if incident.type.value == "freshness":
            tags.append("freshness-guard")
        elif incident.type.value == "data_quality":
            tags.append("dq-guard")
        elif incident.type.value == "schema_drift":
            tags.append("schema-compat")
        elif incident.type.value == "lineage_break":
            tags.append("lineage-repair")

        if dry_run:
            resolution_action = (
                f"await_assertion_rerun:{incident.id}"
                if self.connector.requires_assertion_rerun
                else f"mark_resolved:{incident.id}"
            )
            write.results = [
                WriteBackAction(action=f"add_tags:{','.join(tags)}", status="dry_run"),
                WriteBackAction(action="update_description", status="dry_run"),
                WriteBackAction(
                    action=f"add_owners:{min(5, len(blast.owner_notify))}", status="dry_run"
                ),
                WriteBackAction(
                    action="add_glossary_terms:RemediationPendingValidation",
                    status="dry_run",
                ),
                WriteBackAction(action="save_document", status="dry_run"),
                WriteBackAction(action=resolution_action, status="dry_run"),
            ]
            write.actions = [r.action for r in write.results]
            write.after = before
            return write

        results: list[WriteBackAction] = []
        results.append(self.connector.add_tags(incident.entity.urn, tags))

        desc = (
            f"{incident.entity.description or incident.entity.name}\n\n"
            f"---\n**Remedi approved remediation** ({incident.id}): {plan.summary}\n"
            f"Root cause: {plan.root_cause}\n"
            f"Downstream impacted: {blast.total_impacted} "
            f"(datasets={blast.dataset_count}, dashboards={blast.dashboard_count}, "
            f"ml_models={blast.ml_model_count}, ml_features={blast.ml_feature_count}); "
            f"upstream={len(blast.upstream)}"
        )
        results.append(self.connector.update_description(incident.entity.urn, desc))

        if blast.owner_notify:
            results.append(self.connector.add_owners(incident.entity.urn, blast.owner_notify[:5]))

        glossary = ["RemediationPendingValidation"]
        if incident.type.value == "freshness":
            glossary.append("DataFreshness")
        elif incident.type.value == "data_quality":
            glossary.append("DataQuality")
        results.append(self.connector.add_glossary_terms(incident.entity.urn, glossary))

        doc_body = self._document(incident, blast, plan)
        doc_result = self.connector.save_document(
            title=f"Remedi Approved Remediation — {incident.id}",
            body=doc_body,
            related_urns=[incident.entity.urn, *[d.entity.urn for d in blast.downstream]],
        )
        results.append(doc_result)
        results.append(self.connector.mark_incident_resolved(incident.id))

        write.document_title = doc_result.action.replace("save_document:", "")
        write.results = results
        write.actions = [r.action for r in results]

        try:
            after_entity = self.connector.get_entity(incident.entity.urn)
            write.after = {
                "tags": list(after_entity.tags),
                "owners": list(after_entity.owners),
                "description": after_entity.description,
            }
        except Exception as exc:  # noqa: BLE001
            write.after = {}
            verification = WriteBackAction(
                action="verify_write_back",
                status="error",
                detail=f"Could not verify provider state: {exc}",
            )
            write.results.append(verification)
            write.actions.append(verification.action)

        return write

    def _document(self, incident: Incident, blast: BlastRadius, plan: RemediationPlan) -> str:
        lines = [
            f"# Remedi Approved Remediation — {incident.id}",
            "",
            f"**Title:** {incident.title}",
            f"**Severity:** {incident.severity.value}",
            f"**Entity:** `{incident.entity.urn}`",
            f"**Codegen:** {plan.codegen_mode}",
            "",
            "## Summary",
            plan.summary,
            "",
            "## Root cause",
            plan.root_cause,
            "",
            "## Blast radius",
        ]
        for d in blast.downstream:
            lines.append(f"- Hop {d.hop}: `{d.entity.name}` ({d.entity.type}) — {d.impact_reason}")
        lines += ["", "## Steps", *[f"{i}. {s}" for i, s in enumerate(plan.steps, 1)]]
        lines += ["", "## Artifacts"]
        for a in plan.artifacts:
            ground = f" _(grounded: {', '.join(a.grounded_in)})_" if a.grounded_in else ""
            lines.append(f"- `{a.path}` ({a.kind}){ground}")
        lines += ["", "## Owners notified", *[f"- {o}" for o in blast.owner_notify]]
        return "\n".join(lines) + "\n"
