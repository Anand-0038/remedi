from __future__ import annotations

import time
import uuid
from pathlib import Path

from remedi.agents.coder import CoderAgent
from remedi.agents.detector import DetectorAgent
from remedi.agents.lineage import LineageAgent
from remedi.agents.notifier import NotificationAgent
from remedi.agents.triage import TriageAgent
from remedi.agents.verifier import GroundednessVerifier
from remedi.agents.writer import WriterAgent
from remedi.config import Settings, get_settings
from remedi.connectors.audit import ToolAudit
from remedi.connectors.datahub import DataHubConnector, build_connector
from remedi.models.incident import GeneratedArtifact, Incident, RemediationResult, RunStep
from remedi.store import ProposalIntegrityError, ProposalStore


class RemediOrchestrator:
    """End-to-end: detect → lineage → codegen → verify → PR → propose/apply."""

    def __init__(
        self,
        connector: DataHubConnector | None = None,
        settings: Settings | None = None,
        audit: ToolAudit | None = None,
        store: ProposalStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.audit = audit or ToolAudit()
        self.connector = connector or build_connector(self.settings, audit=self.audit)
        if getattr(self.connector, "audit", None) is not None:
            self.audit = self.connector.audit
        self.detector = DetectorAgent(self.connector)
        self.lineage = LineageAgent(self.connector)
        self.triager = TriageAgent(self.detector, self.lineage)
        self.coder = CoderAgent(
            llm_enabled=bool(self.settings.openai_api_key),
            openai_api_key=self.settings.openai_api_key,
            openai_model=self.settings.openai_model,
        )
        self.writer = WriterAgent(self.connector)
        self.notifier = NotificationAgent(
            outbox_path=Path(self.settings.artifacts_dir).parent / "notifications" / "outbox.json",
            webhook_url=self.settings.remedi_ops_webhook_url,
            webhook_kind=self.settings.remedi_ops_webhook_kind,
        )
        self.verifier = GroundednessVerifier()
        self.store = store or ProposalStore(Path(self.settings.artifacts_dir).parent / "proposals")

    def list_incidents(self) -> list[Incident]:
        return self.detector.list()

    def triage(self):
        """Rank the incident queue using severity and DataHub graph impact."""
        self.audit.clear()
        report = self.triager.rank()
        report.tools_used = list(self.audit.calls)
        return report

    def run(self, incident_id: str, *, dry_run: bool = True) -> RemediationResult:
        if not dry_run:
            raise ValueError(
                "Direct apply is disabled. Propose first, then apply the sealed proposal by run_id."
            )
        self.audit.clear()
        timeline: list[RunStep] = []

        t0 = time.perf_counter()
        incident = self.detector.get(incident_id)
        timeline.append(
            RunStep(
                name="detect",
                status="done",
                detail=(
                    f"{incident.type.value} · {incident.severity.value} · "
                    f"via {incident.detection_source} · {incident.entity.name}"
                ),
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        )

        t1 = time.perf_counter()
        blast = self.lineage.analyze(incident)
        timeline.append(
            RunStep(
                name="lineage",
                status="done",
                detail=(
                    f"BFS down={blast.total_impacted} up={len(blast.upstream)} "
                    f"(ds={blast.dataset_count}, dash={blast.dashboard_count}, "
                    f"ml={blast.ml_model_count}, feats={blast.ml_feature_count}, "
                    f"cols={len(blast.column_impacts)})"
                ),
                duration_ms=int((time.perf_counter() - t1) * 1000),
            )
        )

        t2 = time.perf_counter()
        plan = self.coder.plan(incident, blast)
        timeline.append(
            RunStep(
                name="codegen",
                status="done",
                detail=f"{plan.codegen_mode} · {len(plan.artifacts)} artifacts",
                duration_ms=int((time.perf_counter() - t2) * 1000),
            )
        )

        t_v = time.perf_counter()
        grounded = self.verifier.verify(incident, plan.artifacts, blast=blast)
        for artifact in plan.artifacts:
            artifact.grounded = grounded.ok
        timeline.append(
            RunStep(
                name="verify",
                status="done" if grounded.ok else "error",
                detail=f"{grounded.as_dict()['badge']} · refs={len(grounded.referenced)}",
                duration_ms=int((time.perf_counter() - t_v) * 1000),
            )
        )

        t3 = time.perf_counter()
        pr_artifacts = self._build_pr_package(incident, blast, plan, grounded.as_dict())
        plan.artifacts.extend(pr_artifacts)
        written_paths = self._materialize_artifacts(incident_id, plan.artifacts)
        for artifact, path in zip(plan.artifacts, written_paths, strict=True):
            artifact.path = path
        timeline.append(
            RunStep(
                name="materialize",
                status="done",
                detail=f"wrote {len(written_paths)} files + PR package",
                duration_ms=int((time.perf_counter() - t3) * 1000),
            )
        )

        t_action = time.perf_counter()
        actions_taken = self.notifier.dispatch(incident, blast, plan, dry_run=dry_run)
        timeline.append(
            RunStep(
                name="operational_action",
                status="done" if all(a.status != "error" for a in actions_taken) else "error",
                detail=", ".join(
                    f"{action.kind}:{action.status}@{action.destination}"
                    for action in actions_taken
                ),
                duration_ms=int((time.perf_counter() - t_action) * 1000),
            )
        )

        t4 = time.perf_counter()
        write_back = self.writer.apply(incident, blast, plan, dry_run=dry_run)
        plan.write_back = write_back
        ok_count = sum(1 for r in write_back.results if r.status in ("ok", "dry_run"))
        timeline.append(
            RunStep(
                name="write_back",
                status="done",
                detail=f"{ok_count}/{len(write_back.results)} actions · dry_run={dry_run}",
                duration_ms=int((time.perf_counter() - t4) * 1000),
            )
        )

        try:
            incident = self.detector.get(incident_id)
        except Exception:  # noqa: BLE001
            pass

        result = RemediationResult(
            run_id=str(uuid.uuid4())[:8],
            incident=incident,
            blast_radius=blast,
            plan=plan,
            mode=self.settings.remedi_mode,
            success=grounded.ok,
            message=(
                f"{'Proposed' if dry_run else 'Applied'} {incident_id}: "
                f"{blast.total_impacted} downstream, {len(plan.artifacts)} artifacts, "
                f"tools={len(self.audit.calls)}, grounded={grounded.ok}"
            ),
            timeline=timeline,
            tools_used=self.audit.calls,
            pending_write_back=dry_run,
            groundedness=grounded.as_dict(),
            actions_taken=actions_taken,
        )
        result.proposal_id = result.run_id
        if dry_run:
            self.store.save(result)
        return result

    def apply_proposal(
        self, run_id: str | None = None, *, incident_id: str | None = None
    ) -> RemediationResult:
        """Apply an exact stored proposal — does not re-run codegen."""
        self.audit.clear()
        if run_id:
            proposal = self.store.load(run_id)
        elif incident_id:
            proposal = self.store.latest_for(incident_id)
            if proposal is None:
                raise KeyError(f"No stored proposal for incident {incident_id}")
        else:
            raise KeyError("run_id or incident_id required")
        if not proposal.groundedness.get("ok", False):
            raise ProposalIntegrityError(
                f"Proposal {proposal.run_id} is ungrounded and cannot be applied; "
                "generate a new schema-grounded proposal"
            )
        # Atomic create makes the approved run single-use even under concurrent requests.
        self.store.claim_execution(proposal)

        incident = self.detector.get(proposal.incident.id)
        # refresh entity tags for before/after
        proposal.incident = incident
        try:
            write_back = self.writer.apply(
                proposal.incident,
                proposal.blast_radius,
                proposal.plan,
                dry_run=False,
            )
            actions_taken = self.notifier.dispatch(
                proposal.incident,
                proposal.blast_radius,
                proposal.plan,
                dry_run=False,
            )
        except Exception:
            self.store.finish_execution(proposal, status="failed")
            raise
        proposal.plan.write_back = write_back
        proposal.actions_taken = actions_taken
        proposal.pending_write_back = False
        proposal.tools_used = self.audit.calls
        apply_ok = all(result.status != "error" for result in write_back.results) and all(
            action.status != "error" for action in proposal.actions_taken
        )
        proposal.success = proposal.success and apply_ok
        self.store.finish_execution(
            proposal,
            status="applied" if apply_ok else "failed",
        )
        proposal.message = (
            f"{'Applied' if apply_ok else 'Apply failed for'} proposal {proposal.run_id} "
            f"for {proposal.incident.id}: {len(write_back.results)} write-back actions "
            "(sealed plan, no re-codegen)"
        )
        proposal.timeline = list(proposal.timeline) + [
            RunStep(
                name="apply_proposal",
                status="done" if apply_ok else "error",
                detail=f"run_id={proposal.run_id} · sealed plan · no re-codegen",
            ),
            RunStep(
                name="operational_action",
                status=(
                    "done"
                    if all(action.status != "error" for action in proposal.actions_taken)
                    else "error"
                ),
                detail=", ".join(
                    f"{action.kind}:{action.status}@{action.destination}"
                    for action in proposal.actions_taken
                ),
            ),
        ]
        try:
            proposal.incident = self.detector.get(proposal.incident.id)
        except Exception:  # noqa: BLE001
            pass
        return proposal

    def _build_pr_package(self, incident, blast, plan, grounded: dict) -> list[GeneratedArtifact]:
        files = "\n".join(f"- `{a.path}` — {a.description}" for a in plan.artifacts)
        owners = "\n".join(f"- {o}" for o in blast.owner_notify) or "- (none)"
        downstream = (
            "\n".join(
                f"- hop {d.hop}: `{d.entity.name}` ({d.entity.type})" for d in blast.downstream
            )
            or "- (none)"
        )
        upstream = (
            "\n".join(f"- hop {d.hop}: `{d.entity.name}` ({d.entity.type})" for d in blast.upstream)
            or "- (none)"
        )
        cols = (
            "\n".join(
                f"- `{c['column']}` → {c['consumer']} ({c['reason']})" for c in blast.column_impacts
            )
            or "- (none)"
        )
        body = f"""## Remedi auto-remediation — `{incident.id}`

### Summary
{plan.summary}

### Root cause
{plan.root_cause}

### Groundedness
- **badge:** `{grounded.get("badge")}`
- **schema columns used:** {", ".join(grounded.get("referenced") or []) or "(n/a)"}
- **invented blocked:** {", ".join(grounded.get("invented_blocked") or []) or "none"}

### DataHub context
- **URN:** `{incident.entity.urn}`
- **Detection:** {incident.detection_source}
- **Blast radius:** {blast.total_impacted} downstream / {len(blast.upstream)} upstream
- **ML:** models={blast.ml_model_count}, features={blast.ml_feature_count}

### Downstream
{downstream}

### Upstream (root cause)
{upstream}

### Column impact
{cols}

### Owners to notify
{owners}

### Files in this PR
{files}

### Checklist for reviewers
- [ ] Schemas match DataHub (groundedness badge = grounded)
- [ ] Fail-closed tests included
- [ ] On-call notified
- [ ] Apply stored proposal after merge (`POST /api/apply` with run_id)

---
*Generated by Remedi · codegen={plan.codegen_mode}*
"""
        patch_parts: list[str] = []
        for a in plan.artifacts:
            lines = a.content.splitlines() or [""]
            patch_parts.extend(
                [
                    f"diff --git a/{a.path} b/{a.path}\n",
                    "new file mode 100644\n",
                    "--- /dev/null\n",
                    f"+++ b/{a.path}\n",
                    f"@@ -0,0 +1,{len(lines)} @@\n",
                ]
            )
            for line in lines:
                patch_parts.append(f"+{line}\n")

        return [
            GeneratedArtifact(
                path="PR_DESCRIPTION.md",
                kind="pr",
                description="Merge-ready PR description for data team review",
                content=body,
                grounded_in=[f"urn:{incident.entity.urn}", f"downstream:{blast.total_impacted}"],
                grounded=grounded.get("ok"),
            ),
            GeneratedArtifact(
                path="patches/remedi.patch",
                kind="patch",
                description="Git-apply-ready patch bundle of generated files",
                content="".join(patch_parts),
                grounded_in=["artifacts:all"],
                grounded=grounded.get("ok"),
            ),
        ]

    def _materialize_artifacts(self, incident_id: str, artifacts) -> list[str]:
        artifacts_root = Path(self.settings.artifacts_dir).resolve()
        root = (artifacts_root / incident_id).resolve()
        if not root.is_relative_to(artifacts_root):
            raise ValueError(f"Incident output path escapes artifact root: {incident_id!r}")
        # Clean prior outputs for this incident to avoid stale taxi files in ecommerce folders
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file():
                    p.unlink()
        root.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        for artifact in artifacts:
            rel = Path(artifact.path)
            if "examples/generated" in str(rel) and incident_id in rel.parts:
                idx = rel.parts.index(incident_id)
                rel = Path(*rel.parts[idx + 1 :])
            dest = (root / rel).resolve()
            if not dest.is_relative_to(root):
                raise ValueError(
                    f"Generated artifact path escapes incident root: {artifact.path!r}"
                )
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(artifact.content, encoding="utf-8")
            written.append(str(dest.as_posix()))
        index = root / "README.md"
        lines = [
            f"# Remedi generated artifacts — `{incident_id}`",
            "",
            "Merge-ready outputs + PR package from DataHub schemas, queries, and lineage.",
            "",
            "Start with `PR_DESCRIPTION.md`, then review code files.",
            "",
        ]
        for artifact in artifacts:
            ground = (
                f" — grounded in `{', '.join(artifact.grounded_in)}`"
                if artifact.grounded_in
                else ""
            )
            lines.append(f"- `{Path(artifact.path).name}` — {artifact.description}{ground}")
        index.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return written
