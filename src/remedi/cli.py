from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from remedi.config import get_settings
from remedi.orchestrator import RemediOrchestrator

app = typer.Typer(help="Remedi — DataHub-powered on-call remediation agent", no_args_is_help=True)
console = Console()


@app.command("incidents")
def list_incidents() -> None:
    """List detectable incidents from DataHub quality assertions + fixtures."""
    orch = RemediOrchestrator()
    rows = orch.list_incidents()
    table = Table(title="Remedi incidents")
    table.add_column("ID")
    table.add_column("Sev")
    table.add_column("Type")
    table.add_column("Via")
    table.add_column("Status")
    table.add_column("Title")
    for i in rows:
        table.add_row(
            i.id,
            i.severity.value,
            i.type.value,
            i.detection_source,
            i.status,
            i.title,
        )
    console.print(table)


@app.command("triage")
def triage_incidents() -> None:
    """Rank incidents by severity and DataHub graph blast radius."""
    report = RemediOrchestrator().triage()
    table = Table(title="Remedi context-risk queue")
    table.add_column("Priority")
    table.add_column("Risk", justify="right")
    table.add_column("Incident")
    table.add_column("Impact", justify="right")
    table.add_column("Recommended action")
    for item in report.items:
        table.add_row(
            item.priority,
            str(item.risk_score),
            item.incident.id,
            str(item.blast_radius.total_impacted),
            item.recommended_action,
        )
    console.print(table)
    console.print(
        f"[dim]DataHub context calls: {len(report.tools_used)} · "
        f"recommended first: {report.top_incident_id or 'none'}[/]"
    )


@app.command("run")
def run_remediation(
    incident: str = typer.Option(..., "--incident", "-i", help="Incident id"),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Propose only (default) or propose, seal, and apply the exact stored plan",
    ),
    json_out: Optional[Path] = typer.Option(None, "--json", help="Write result JSON"),
) -> None:
    """Propose remediation, optionally applying the exact sealed proposal."""
    orch = RemediOrchestrator()
    proposal = orch.run(incident, dry_run=True)
    result = proposal if dry_run else orch.apply_proposal(run_id=proposal.run_id)
    console.print(
        Panel.fit(
            f"[bold green]{result.message}[/]\n"
            f"mode={result.mode} run_id={result.run_id}\n"
            f"approval={result.proposal_integrity} digest={result.proposal_digest or 'n/a'}\n"
            f"blast_radius={result.blast_radius.total_impacted} "
            f"artifacts={len(result.plan.artifacts)} tools={len(result.tools_used)}\n"
            f"pending_write_back={result.pending_write_back}\n"
            f"write_back={result.plan.write_back.actions}",
            title="Remedi",
        )
    )
    if result.tools_used:
        console.print("[dim]DataHub tools:[/]")
        for t in result.tools_used:
            console.print(f"  · {t.tool} [{t.status}] {t.detail}")
    for artifact in result.plan.artifacts:
        console.print(f"  • {artifact.path}")
    if json_out:
        json_out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"Wrote {json_out}")


@app.command("serve")
def serve(
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """Start the Remedi demo API + UI."""
    settings = get_settings()
    uvicorn.run(
        "remedi.api.app:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=False,
    )


@app.command("selftest")
def selftest(
    json_out: Optional[Path] = typer.Option(None, "--json", help="Write report JSON"),
) -> None:
    """Run judge-facing end-to-end checks and print a report."""
    from remedi.selftest import run_selftest

    report = run_selftest()
    if report["ok"]:
        console.print(
            Panel.fit(
                f"[bold green]SELFTEST PASS[/] {report['passed']}/{report['passed'] + report['failed']} checks "
                f"in {report['elapsed_ms']}ms\nreport={report.get('report_path')}",
                title="Remedi",
            )
        )
    else:
        failed = [c for c in report["checks"] if not c["ok"]]
        console.print(
            Panel.fit(
                f"[bold red]SELFTEST FAIL[/] {report['failed']} failed\n"
                + "\n".join(f"• {c['name']}: {c['detail']}" for c in failed),
                title="Remedi",
            )
        )
    if json_out:
        json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        console.print(f"Wrote {json_out}")
    if not report["ok"]:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
