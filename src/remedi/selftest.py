"""Produce a judge-facing JSON report proving Remedi works end-to-end."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from remedi import __version__
from remedi.config import Settings, get_settings
from remedi.connectors.datahub import FixtureConnector
from remedi.orchestrator import RemediOrchestrator
from remedi.store import ProposalAlreadyAppliedError, ProposalIntegrityError, ProposalStore


REQUIRED_KINDS = {
    "freshness-nyc-taxi": {"dbt_model", "airflow_dag", "ml_guard", "dagster", "prefect", "pr"},
    "dq-healthcare-vitals": {"sql", "ml_guard", "pr"},
    "freshness-ecommerce-orders": {"dbt_model", "dagster", "prefect", "pr"},
}


def run_selftest(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    started = time.time()
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    # Keep the committed fixture catalog immutable so every judge gets the same run.
    report_dir = Path(settings.artifacts_dir).parent / "selftest"
    report_dir.mkdir(parents=True, exist_ok=True)
    fixtures = Path(settings.fixtures_dir)
    sandbox = tempfile.TemporaryDirectory(prefix="remedi-selftest-")
    sandbox_root = Path(sandbox.name)
    isolated_fixtures = sandbox_root / "fixtures"
    isolated_fixtures.mkdir()
    shutil.copy2(fixtures / "catalog.json", isolated_fixtures / "catalog.json")
    store = ProposalStore(sandbox_root / "proposals")
    orch = RemediOrchestrator(
        connector=FixtureConnector(isolated_fixtures),
        settings=Settings(
            remedi_mode="fixture",
            artifacts_dir=sandbox_root / "generated",
            fixtures_dir=isolated_fixtures,
            openai_api_key="",
        ),
        store=store,
    )

    incidents = orch.list_incidents()
    check("list_incidents", len(incidents) >= 5, f"count={len(incidents)}")

    triage = orch.triage()
    scores = [item.risk_score for item in triage.items if item.incident.status == "open"]
    check("triage_ranked", scores == sorted(scores, reverse=True), str(scores))
    check(
        "triage_recommends_open",
        triage.top_incident_id is not None
        and triage.items[0].incident.id == triage.top_incident_id,
        str(triage.top_incident_id),
    )
    check("triage_context_tools", len(triage.tools_used) >= 3, str(len(triage.tools_used)))

    # Propose + apply freshness
    prop = orch.run("freshness-nyc-taxi", dry_run=True)
    check(
        "propose_grounded", prop.groundedness.get("ok") is True, prop.groundedness.get("badge", "")
    )
    check("propose_pending", prop.pending_write_back is True, prop.run_id)
    check(
        "proposal_sealed",
        prop.proposal_integrity == "sealed"
        and bool(prop.proposal_digest)
        and prop.proposal_digest.startswith("sha256:"),
        str(prop.proposal_digest),
    )
    check(
        "operational_action_planned",
        len(prop.actions_taken) == 1 and prop.actions_taken[0].status == "planned",
        str([action.status for action in prop.actions_taken]),
    )
    kinds = {a.kind for a in prop.plan.artifacts}
    missing = REQUIRED_KINDS["freshness-nyc-taxi"] - kinds
    check(
        "freshness_artifact_kinds", not missing, f"missing={sorted(missing)} kinds={sorted(kinds)}"
    )
    python_artifacts = [
        artifact
        for artifact in prop.plan.artifacts
        if artifact.kind in {"airflow_dag", "dagster", "prefect", "ml_guard"}
    ]
    try:
        for artifact in python_artifacts:
            compile(artifact.content, artifact.path, "exec")
        generated_python_valid = True
    except SyntaxError:
        generated_python_valid = False
    check(
        "generated_python_valid",
        generated_python_valid,
        str([artifact.path for artifact in python_artifacts]),
    )
    check(
        "upstream_present",
        len(prop.blast_radius.upstream) >= 1,
        str(len(prop.blast_radius.upstream)),
    )
    check("tools_logged", len(prop.tools_used) >= 3, str(len(prop.tools_used)))

    applied = orch.apply_proposal(run_id=prop.run_id)
    check("apply_same_run", applied.run_id == prop.run_id, applied.run_id)
    check("apply_no_pending", applied.pending_write_back is False, applied.message)
    check(
        "glossary_writeback",
        any("glossary" in r.action for r in applied.plan.write_back.results),
        str([r.action for r in applied.plan.write_back.results]),
    )
    check(
        "operational_action_recorded",
        len(applied.actions_taken) == 1 and applied.actions_taken[0].status == "recorded",
        str([action.status for action in applied.actions_taken]),
    )
    try:
        orch.apply_proposal(run_id=prop.run_id)
        replay_rejected = False
    except ProposalAlreadyAppliedError:
        replay_rejected = True
    check("proposal_replay_rejected", replay_rejected, prop.run_id)
    stored_path = sandbox_root / "proposals" / f"{prop.run_id}.json"
    stored = json.loads(stored_path.read_text(encoding="utf-8"))
    stored["plan"]["summary"] = "tampered after approval"
    stored_path.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    try:
        store.load(prop.run_id)
        tamper_rejected = False
    except ProposalIntegrityError:
        tamper_rejected = True
    check("proposal_tamper_rejected", tamper_rejected, prop.run_id)

    dq = orch.run("dq-healthcare-vitals", dry_run=True)
    dq_paths = " ".join(a.path for a in dq.plan.artifacts)
    check("dq_quality_gate", "feature_quality_gate" in dq_paths, dq_paths)
    check(
        "dq_ml_feature",
        dq.blast_radius.ml_feature_count >= 1,
        str(dq.blast_radius.ml_feature_count),
    )
    check("dq_not_freshness_guard", "feature_freshness_guard" not in dq_paths, "ok")

    ecom = orch.run("freshness-ecommerce-orders", dry_run=True)
    epaths = " ".join(a.path for a in ecom.plan.artifacts)
    econtent = "\n".join(a.content for a in ecom.plan.artifacts)
    check("ecommerce_names", "raw_orders" in epaths, epaths)
    check("ecommerce_no_taxi_pollution", "nyc_taxi" not in econtent, "content scan")
    check(
        "ecommerce_real_source_gate",
        "dbt source freshness --select source:ecommerce.raw_orders" in econtent,
        "dbt source freshness",
    )

    lineage = orch.run("lineage-break-customer-dim", dry_run=True)
    lineage_content = "\n".join(a.content for a in lineage.plan.artifacts)
    check(
        "lineage_context_grounded",
        lineage.groundedness.get("ok") is True,
        lineage.groundedness.get("badge", ""),
    )
    check(
        "lineage_repair_uses_real_upstream",
        "retail.customers" in lineage_content
        and "d.email = u.email" in lineage_content
        and "source('repaired', 'upstream')" not in lineage_content,
        "customer_id restored from DataHub upstream schema",
    )

    # Committed samples exist for judges who won't run code
    sample = Path(settings.artifacts_dir) / "freshness-nyc-taxi" / "PR_DESCRIPTION.md"
    check("committed_sample_pr", sample.exists(), str(sample))

    ok = all(c["ok"] for c in checks)
    report = {
        "product": "remedi",
        "version": __version__,
        "ok": ok,
        "elapsed_ms": int((time.time() - started) * 1000),
        "checks": checks,
        "passed": sum(1 for c in checks if c["ok"]),
        "failed": sum(1 for c in checks if not c["ok"]),
        "hackathon": "DataHub Agent Hackathon",
        "judge_hint": "All checks should be ok=true before submission",
    }
    out = report_dir / "report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["report_path"] = str(out)
    sandbox.cleanup()
    return report
