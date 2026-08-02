import json
from pathlib import Path
from urllib.error import URLError

import pytest

from remedi.agents.coder import CodeGenerationError
from remedi.agents.lineage import LineageAgent
from remedi.agents.verifier import GroundednessVerifier
from remedi.config import Settings
from remedi.connectors.datahub import FixtureConnector
from remedi.models.incident import IncidentSeverity, IncidentType
from remedi.orchestrator import RemediOrchestrator
from remedi.store import ProposalStore


FIXTURES = Path(__file__).resolve().parents[1] / "examples" / "fixtures"


def _orch(tmp_path: Path) -> RemediOrchestrator:
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    (fixtures_dir / "catalog.json").write_text(
        (FIXTURES / "catalog.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    settings = Settings(
        remedi_mode="fixture",
        artifacts_dir=tmp_path / "out",
        fixtures_dir=fixtures_dir,
    )
    store = ProposalStore(tmp_path / "proposals")
    return RemediOrchestrator(
        connector=FixtureConnector(fixtures_dir),
        settings=settings,
        store=store,
    )


def test_list_includes_ecommerce_and_assertions():
    orch = RemediOrchestrator(settings=Settings(remedi_mode="fixture"))
    incidents = orch.list_incidents()
    ids = {i.id for i in incidents}
    assert "freshness-ecommerce-orders" in ids
    by_id = {i.id: i for i in incidents}
    assert by_id["freshness-nyc-taxi"].detection_source == "assertion"


def test_propose_then_apply_uses_store(tmp_path):
    orch = _orch(tmp_path)
    proposed = orch.run("schema-orders-amount", dry_run=True)
    assert proposed.pending_write_back
    assert proposed.proposal_id
    assert proposed.groundedness["ok"] is True
    # Apply exact proposal — must not invent a new run_id path for codegen
    applied = orch.apply_proposal(run_id=proposed.run_id)
    assert applied.run_id == proposed.run_id
    assert applied.pending_write_back is False
    assert any(r.status == "ok" for r in applied.plan.write_back.results)
    assert any(
        "glossary" in r.action or "glossary" in (r.detail or "")
        for r in applied.plan.write_back.results
    ) or any("add_glossary_terms" in r.action for r in applied.plan.write_back.results)
    assert applied.success is True
    assert "sealed plan, no re-codegen" in applied.message
    try:
        orch.apply_proposal(run_id=proposed.run_id)
    except ValueError as exc:
        assert "already applied" in str(exc)
    else:
        raise AssertionError("Applied proposals must be single-use")


def test_direct_apply_is_rejected(tmp_path):
    orch = _orch(tmp_path)
    try:
        orch.run("schema-orders-amount", dry_run=False)
    except ValueError as exc:
        assert "Direct apply is disabled" in str(exc)
    else:
        raise AssertionError("Direct apply must not bypass the sealed proposal store")


def test_failed_provider_refresh_does_not_consume_proposal(tmp_path):
    orch = _orch(tmp_path)
    proposal = orch.run("schema-orders-amount", dry_run=True)
    receipt = orch.store.execution_root / f"{proposal.run_id}.json"

    def fail_refresh(_incident_id: str):
        raise ConnectionError("provider unavailable before apply")

    orch.detector.get = fail_refresh  # type: ignore[method-assign]
    with pytest.raises(ConnectionError, match="before apply"):
        orch.apply_proposal(run_id=proposal.run_id)

    assert not receipt.exists()


def test_live_style_proposal_discloses_assertion_rerun_gate(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.connector.requires_assertion_rerun = True

    proposed = orch.run("schema-orders-amount", dry_run=True)

    assert any(
        result.action.startswith("await_assertion_rerun:")
        for result in proposed.plan.write_back.results
    )
    assert not any(
        result.action.startswith("mark_resolved:") for result in proposed.plan.write_back.results
    )


def test_ungrounded_proposal_cannot_be_applied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from remedi.store import ProposalIntegrityError

    orch = _orch(tmp_path)
    original_plan = orch.coder.plan

    def ungrounded_plan(*args: object, **kwargs: object):
        plan = original_plan(*args, **kwargs)
        plan.artifacts[0].grounded_in.append("schema:invented_column")
        return plan

    monkeypatch.setattr(orch.coder, "plan", ungrounded_plan)
    proposed = orch.run("schema-orders-amount", dry_run=True)
    assert proposed.groundedness["ok"] is False

    with pytest.raises(ProposalIntegrityError, match="ungrounded"):
        orch.apply_proposal(run_id=proposed.run_id)

    assert not (tmp_path / "proposals" / "_executions" / f"{proposed.run_id}.json").exists()


def test_configured_llm_failure_never_falls_back_to_templates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    orch = _orch(tmp_path)
    orch.coder.llm_enabled = True
    orch.coder.openai_api_key = "test-key"

    def fail_request(*_args: object, **_kwargs: object) -> object:
        raise URLError("provider unavailable")

    monkeypatch.setattr("urllib.request.urlopen", fail_request)

    with pytest.raises(CodeGenerationError, match="no template fallback"):
        orch.run("freshness-nyc-taxi", dry_run=True)

    assert list((tmp_path / "out").glob("**/*")) == []


def test_notification_storage_failure_finishes_execution_as_failed(tmp_path: Path):
    orch = _orch(tmp_path)
    proposed = orch.run("schema-orders-amount", dry_run=True)
    outbox = tmp_path / "notifications" / "outbox.json"
    outbox.parent.mkdir()
    outbox.write_text('{"not": "a list"}', encoding="utf-8")

    with pytest.raises(ValueError, match="JSON array"):
        orch.apply_proposal(run_id=proposed.run_id)

    receipt = json.loads(
        (tmp_path / "proposals" / "_executions" / f"{proposed.run_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["status"] == "failed"


def test_generated_artifact_path_cannot_escape_output_root(tmp_path: Path):
    from remedi.models.incident import GeneratedArtifact

    orch = _orch(tmp_path)
    artifact = GeneratedArtifact(
        path="../../outside.sql",
        kind="sql",
        description="untrusted output path",
        content="select 1",
    )

    with pytest.raises(ValueError, match="escapes incident root"):
        orch._materialize_artifacts("safe-incident", [artifact])

    assert not (tmp_path / "outside.sql").exists()


def test_freshness_has_upstream_dagster_and_pr(tmp_path):
    orch = _orch(tmp_path)
    result = orch.run("freshness-nyc-taxi", dry_run=True)
    assert len(result.blast_radius.upstream) >= 1
    assert result.blast_radius.column_impacts
    kinds = {a.kind for a in result.plan.artifacts}
    assert "dagster" in kinds and "prefect" in kinds and "pr" in kinds
    assert result.groundedness["badge"] == "grounded"
    executable_python = [
        artifact
        for artifact in result.plan.artifacts
        if artifact.kind in {"airflow_dag", "dagster", "prefect", "ml_guard"}
    ]
    for artifact in executable_python:
        compile(artifact.content, artifact.path, "exec")
    for artifact in executable_python:
        if artifact.kind in {"dagster", "prefect"}:
            assert "subprocess.run" in artifact.content
            assert 'print("dbt ' not in artifact.content


def test_dq_quality_gate_not_freshness_copy(tmp_path):
    orch = _orch(tmp_path)
    result = orch.run("dq-healthcare-vitals", dry_run=True)
    assert result.blast_radius.ml_feature_count >= 1
    assert result.blast_radius.ml_model_count >= 1
    paths = [a.path for a in result.plan.artifacts]
    assert any("feature_quality_gate.py" in p for p in paths)
    assert not any("feature_freshness_guard.py" in p for p in paths)


def test_ecommerce_clean_names(tmp_path):
    orch = _orch(tmp_path)
    result = orch.run("freshness-ecommerce-orders", dry_run=True)
    generated = "\n".join(
        [
            *(artifact.path for artifact in result.plan.artifacts),
            *(artifact.content for artifact in result.plan.artifacts),
        ]
    )
    assert "raw_orders" in generated
    assert "nyc_taxi" not in generated
    assert "dbt source freshness --select source:ecommerce.raw_orders" in generated


def test_lineage_repair_uses_upstream_schema_without_invented_columns(tmp_path: Path):
    orch = _orch(tmp_path)

    result = orch.run("lineage-break-customer-dim", dry_run=True)

    assert result.success is True
    assert result.groundedness["badge"] == "grounded"
    assert "customer_id" not in result.groundedness["invented_blocked"]
    model = next(artifact for artifact in result.plan.artifacts if artifact.kind == "dbt_model")
    assert "retail.customers" in model.content
    assert "d.email = u.email" in model.content
    assert "source('repaired', 'upstream')" not in model.content


def test_selftest_passes():
    from remedi.selftest import run_selftest

    report = run_selftest()
    assert report["ok"] is True, report
    assert report["failed"] == 0


def test_selftest_finds_samples_from_configured_artifact_root(tmp_path):
    from remedi.selftest import run_selftest

    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    (fixtures_dir / "catalog.json").write_text(
        (FIXTURES / "catalog.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    artifacts_dir = tmp_path / "generated"
    sample_dir = artifacts_dir / "freshness-nyc-taxi"
    sample_dir.mkdir(parents=True)
    (sample_dir / "PR_DESCRIPTION.md").write_text("# Sample\n", encoding="utf-8")

    report = run_selftest(
        Settings(
            remedi_mode="fixture",
            artifacts_dir=artifacts_dir,
            fixtures_dir=fixtures_dir,
        )
    )

    committed_sample = next(
        check for check in report["checks"] if check["name"] == "committed_sample_pr"
    )
    assert committed_sample["ok"] is True
    assert committed_sample["detail"] == str(sample_dir / "PR_DESCRIPTION.md")


def test_verifier_blocks_invented_grounded_in():
    from remedi.models.incident import (
        EntityRef,
        GeneratedArtifact,
        Incident,
        IncidentSeverity,
        IncidentType,
        SchemaField,
    )

    incident = Incident(
        id="x",
        title="t",
        type=IncidentType.DATA_QUALITY,
        severity=IncidentSeverity.HIGH,
        entity=EntityRef(
            urn="u",
            name="t",
            type="dataset",
            schema_fields=[SchemaField(name="heart_rate", type="INT")],
        ),
    )
    bad = GeneratedArtifact(
        path="x.sql",
        kind="sql",
        description="bad",
        content="SELECT heart_rate FROM t",
        grounded_in=["schema:totally_fake_col"],
    )
    report = GroundednessVerifier().verify(incident, [bad])
    assert report.ok is False
    assert "totally_fake_col" in report.invented_blocked


def test_llm_grounding_claims_only_referenced_schema_fields():
    from remedi.agents.coder import _schema_grounding
    from remedi.models.incident import SchemaField

    fields = [
        SchemaField(name="order_id", type="STRING"),
        SchemaField(name="updated_at", type="TIMESTAMP"),
    ]

    assert _schema_grounding("SELECT order_id FROM orders", fields) == ["schema:order_id"]


def test_verifier_fails_closed_without_schema():
    from remedi.models.incident import EntityRef, GeneratedArtifact, Incident

    incident = Incident(
        id="no-schema",
        title="Schema unavailable",
        type=IncidentType.DATA_QUALITY,
        severity=IncidentSeverity.HIGH,
        entity=EntityRef(urn="urn:li:dataset:no-schema", name="unknown", type="dataset"),
    )
    artifact = GeneratedArtifact(
        path="fix.sql",
        kind="sql",
        description="Cannot be proven",
        content="SELECT mystery_column FROM unknown",
    )

    report = GroundednessVerifier().verify(incident, [artifact])

    assert report.ok is False
    assert "cannot be proven" in report.notes[0]


def test_lineage_adds_observed_queries_to_codegen_context():
    from remedi.models.incident import EntityRef, Incident

    class QueryConnector:
        def get_lineage_downstream(self, _urn: str, max_hops: int = 3) -> list[dict]:
            return []

        def get_lineage_upstream(self, _urn: str, max_hops: int = 3) -> list[dict]:
            return []

        def get_dataset_queries(self, _urn: str) -> list[str]:
            return ["SELECT order_id FROM orders"]

    incident = Incident(
        id="queries",
        title="Observed query context",
        type=IncidentType.DATA_QUALITY,
        severity=IncidentSeverity.HIGH,
        entity=EntityRef(urn="urn:li:dataset:orders", name="orders", type="dataset"),
    )

    LineageAgent(QueryConnector()).analyze(incident)  # type: ignore[arg-type]

    assert incident.sample_queries == ["SELECT order_id FROM orders"]
