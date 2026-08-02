from pathlib import Path
import subprocess


SAMPLES = Path(__file__).resolve().parents[1] / "examples" / "generated"
INCIDENTS = {
    "freshness-nyc-taxi",
    "dq-healthcare-vitals",
    "schema-orders-amount",
    "lineage-break-customer-dim",
    "freshness-ecommerce-orders",
}


def test_committed_sample_packages_are_grounded_and_complete():
    assert {path.name for path in SAMPLES.iterdir() if path.is_dir()} == INCIDENTS

    for incident_id in INCIDENTS:
        root = SAMPLES / incident_id
        pr = (root / "PR_DESCRIPTION.md").read_text(encoding="utf-8")
        assert "**badge:** `grounded`" in pr
        assert "**invented blocked:** none" in pr
        assert (root / "README.md").is_file()
        assert (root / "patches" / "remedi.patch").is_file()


def test_committed_python_artifacts_compile_and_execute_real_commands():
    paths = sorted(SAMPLES.glob("**/*.py"))
    assert paths

    for path in paths:
        content = path.read_text(encoding="utf-8")
        compile(content, str(path), "exec")
        if "/dagster/" in path.as_posix() or "/prefect/" in path.as_posix():
            assert "subprocess.run" in content
            assert 'print("dbt ' not in content


def test_committed_samples_have_no_known_cross_scenario_placeholders():
    ecommerce = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (SAMPLES / "freshness-ecommerce-orders").rglob("*")
        if path.is_file()
    )
    lineage = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (SAMPLES / "lineage-break-customer-dim").rglob("*")
        if path.is_file()
    )

    assert "nyc_taxi" not in ecommerce
    assert "external_dag_id" not in ecommerce
    assert "source('repaired', 'upstream')" not in lineage
    assert "retail.customers" in lineage
    assert "d.email = u.email" in lineage


def test_committed_patch_bundles_are_accepted_by_git(tmp_path: Path):
    for incident_id in sorted(INCIDENTS):
        checkout = tmp_path / incident_id
        checkout.mkdir()
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=checkout,
            check=True,
        )
        subprocess.run(
            [
                "git",
                "apply",
                "--check",
                str(SAMPLES / incident_id / "patches" / "remedi.patch"),
            ],
            cwd=checkout,
            check=True,
        )
