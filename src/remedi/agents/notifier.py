from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx

from remedi.models.incident import BlastRadius, Incident, OperationalAction, RemediationPlan


class NotificationAgent:
    """Dispatch an approved on-call handoff or record it in the local outbox."""

    def __init__(
        self,
        *,
        outbox_path: Path,
        webhook_url: str = "",
        webhook_kind: Literal["generic", "slack"] = "generic",
    ) -> None:
        self.outbox_path = outbox_path
        self.webhook_url = webhook_url
        self.webhook_kind = webhook_kind

    def dispatch(
        self,
        incident: Incident,
        blast: BlastRadius,
        plan: RemediationPlan,
        *,
        dry_run: bool,
    ) -> list[OperationalAction]:
        payload = self._payload(incident, blast, plan)
        destination = self.webhook_kind if self.webhook_url else "local-outbox"
        summary = (
            f"{incident.severity.value.upper()} {incident.id}: {incident.title} · "
            f"{blast.total_impacted} downstream · {len(blast.owner_notify)} owners"
        )
        if dry_run:
            return [
                OperationalAction(
                    kind="notify_oncall",
                    status="planned",
                    destination=destination,
                    summary=summary,
                    detail="Approval required; no external action taken during Propose",
                    payload=payload,
                )
            ]

        if not self.webhook_url:
            self._record(payload)
            return [
                OperationalAction(
                    kind="notify_oncall",
                    status="recorded",
                    destination=destination,
                    summary=summary,
                    detail=str(self.outbox_path),
                    payload=payload,
                )
            ]

        try:
            body = (
                self._slack_payload(summary, payload) if self.webhook_kind == "slack" else payload
            )
            response = httpx.post(self.webhook_url, json=body, timeout=8.0)
            response.raise_for_status()
            return [
                OperationalAction(
                    kind="notify_oncall",
                    status="sent",
                    destination=self.webhook_kind,
                    summary=summary,
                    detail=f"HTTP {response.status_code}",
                    payload=payload,
                )
            ]
        except Exception as exc:  # noqa: BLE001
            self._record({**payload, "delivery_error": str(exc)})
            return [
                OperationalAction(
                    kind="notify_oncall",
                    status="error",
                    destination=self.webhook_kind,
                    summary=summary,
                    detail=f"Webhook failed; preserved in outbox: {exc}",
                    payload=payload,
                )
            ]

    def _payload(
        self,
        incident: Incident,
        blast: BlastRadius,
        plan: RemediationPlan,
    ) -> dict:
        return {
            "event": "remedi.remediation_approved",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "incident": {
                "id": incident.id,
                "title": incident.title,
                "severity": incident.severity.value,
                "type": incident.type.value,
                "entity_urn": incident.entity.urn,
            },
            "impact": {
                "downstream": blast.total_impacted,
                "dashboards": blast.dashboard_count,
                "ml_models": blast.ml_model_count,
                "ml_features": blast.ml_feature_count,
                "owners": blast.owner_notify,
            },
            "remediation": {
                "summary": plan.summary,
                "artifacts": [artifact.path for artifact in plan.artifacts],
                "grounded": all(artifact.grounded is not False for artifact in plan.artifacts),
            },
        }

    def _record(self, payload: dict) -> None:
        self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
        entries: list[dict] = []
        if self.outbox_path.exists():
            parsed = json.loads(self.outbox_path.read_text(encoding="utf-8"))
            if not isinstance(parsed, list):
                raise ValueError(
                    f"Notification outbox must contain a JSON array: {self.outbox_path}"
                )
            entries = parsed
        entries.append(payload)
        self.outbox_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")

    @staticmethod
    def _slack_payload(summary: str, payload: dict) -> dict:
        impact = payload["impact"]
        owners = ", ".join(impact["owners"]) or "unowned"
        return {
            "text": summary,
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Remedi approved*\n{summary}"},
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                f"Dashboards: {impact['dashboards']} · "
                                f"ML models: {impact['ml_models']} · Owners: {owners}"
                            ),
                        }
                    ],
                },
            ],
        }
