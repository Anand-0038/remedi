from __future__ import annotations

import hmac
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from remedi import __version__
from remedi.agents.coder import CodeGenerationError
from remedi.config import get_settings
from remedi.connectors.datahub import LiveIntegrationError
from remedi.orchestrator import RemediOrchestrator
from remedi.selftest import run_selftest
from remedi.store import ProposalAlreadyAppliedError, ProposalIntegrityError


def _resolve_web_dir() -> Path:
    candidates = (
        Path.cwd() / "web",
        Path(__file__).resolve().parents[3] / "web",
    )
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return candidates[0]


WEB_DIR = _resolve_web_dir()


def _safe_artifact_path(root: Path, *parts: str) -> Path:
    base = root.resolve()
    target = base.joinpath(*parts).resolve()
    if not target.is_relative_to(base):
        raise HTTPException(status_code=404, detail="Artifact not found")
    return target


class RunRequest(BaseModel):
    incident_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    dry_run: bool = True


class ApplyRequest(BaseModel):
    incident_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    )
    run_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    )

    @model_validator(mode="after")
    def require_one_proposal_selector(self) -> "ApplyRequest":
        if (self.incident_id is None) == (self.run_id is None):
            raise ValueError("Provide exactly one of run_id or incident_id")
        return self


def create_app() -> FastAPI:
    settings = get_settings()
    api_auth_required = settings.remedi_mode == "live" or bool(settings.remedi_ops_webhook_url)
    if api_auth_required and not settings.remedi_api_key:
        raise RuntimeError(
            "Live mode and external action delivery require REMEDI_API_KEY to protect "
            "catalog reads, mutations, and notifications."
        )
    api = FastAPI(
        title="Remedi",
        description="DataHub-powered on-call remediation agent",
        version=__version__,
    )
    orch = RemediOrchestrator(settings=settings)

    @api.middleware("http")
    async def secure_api(request: Request, call_next):
        if (
            api_auth_required
            and request.url.path.startswith("/api/")
            and request.url.path != "/api/health"
        ):
            authorization = request.headers.get("authorization", "")
            scheme, separator, credentials = authorization.partition(" ")
            bearer = credentials.strip() if separator and scheme.lower() == "bearer" else ""
            provided = request.headers.get("x-api-key", "") or bearer
            if not provided or not hmac.compare_digest(provided, settings.remedi_api_key):
                response = JSONResponse(
                    status_code=401,
                    content={
                        "detail": "A valid Remedi API key is required for this configuration.",
                        "code": "api_auth_required",
                    },
                )
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    @api.exception_handler(LiveIntegrationError)
    def live_integration_error(
        _request: Request,
        exc: LiveIntegrationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": str(exc),
                "code": "live_datahub_unavailable",
                "mode": settings.remedi_mode,
            },
        )

    @api.exception_handler(CodeGenerationError)
    def code_generation_error(
        _request: Request,
        exc: CodeGenerationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={
                "detail": str(exc),
                "code": "llm_codegen_failed",
                "mode": settings.remedi_mode,
            },
        )

    @api.get("/api/health")
    def health() -> dict:
        fixture_ok = Path(settings.fixtures_dir).joinpath("catalog.json").is_file()
        live = settings.remedi_mode == "live"
        fixture_ready = not live and fixture_ok
        # LiveConnector validates GMS connectivity during application startup.
        live_ready = live
        return {
            "ok": fixture_ready or live_ready,
            "mode": settings.remedi_mode,
            "product": "remedi",
            "llm": bool(settings.openai_api_key),
            "version": __version__,
            "gates": {
                "fixture_demo_ok": fixture_ready,
                "live_gms_connected": live_ready,
                "live_fail_closed": True,
                "live_auth_required": live,
                "api_auth_required": api_auth_required,
                "external_action_configured": bool(settings.remedi_ops_webhook_url),
                "live_write_verification": "per_apply_receipt" if live else "not_applicable",
                "live_reads": [
                    "failing_assertions",
                    "entities",
                    "lineage",
                    "query_history",
                    "search",
                ],
                "live_writes": [
                    "tags",
                    "descriptions",
                    "owners",
                    "glossary_terms",
                    "documents",
                ],
                "live_blockers": [],
                "selftest_path": "/api/selftest",
            },
        }

    @api.get("/api/incidents")
    def incidents() -> list[dict]:
        return [i.model_dump(mode="json") for i in orch.list_incidents()]

    @api.get("/api/triage")
    def triage() -> dict:
        return orch.triage().model_dump(mode="json")

    @api.post("/api/run")
    def run(req: RunRequest) -> dict:
        if not req.dry_run:
            raise HTTPException(
                status_code=400,
                detail="Direct apply is disabled. Propose first, then POST /api/apply with run_id.",
            )
        try:
            result = orch.run(req.incident_id, dry_run=True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @api.post("/api/apply")
    def apply(req: ApplyRequest) -> dict:
        try:
            result = orch.apply_proposal(run_id=req.run_id, incident_id=req.incident_id)
        except ProposalAlreadyAppliedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProposalIntegrityError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @api.get("/api/proposals/{run_id}")
    def get_proposal(run_id: str) -> dict:
        try:
            return orch.store.load(run_id).model_dump(mode="json")
        except ProposalIntegrityError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.get("/api/selftest")
    def selftest() -> dict:
        return run_selftest(settings)

    @api.get("/api/report/{incident_id}")
    def incident_report(incident_id: str) -> dict:
        try:
            latest = orch.store.latest_for(incident_id)
        except ProposalIntegrityError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        sample_root = _safe_artifact_path(Path(settings.artifacts_dir), incident_id)
        files: list[str] = []
        if sample_root.exists():
            files = sorted(
                str(p.relative_to(sample_root).as_posix())
                for p in sample_root.rglob("*")
                if p.is_file()
            )
        return {
            "incident_id": incident_id,
            "proposal": latest.model_dump(mode="json") if latest else None,
            "generated_files": files,
        }

    @api.get("/api/artifacts/{incident_id}")
    def list_artifacts(incident_id: str) -> dict:
        root = _safe_artifact_path(Path(settings.artifacts_dir), incident_id)
        if not root.exists():
            return {"incident_id": incident_id, "files": []}
        files = [str(p.relative_to(root).as_posix()) for p in root.rglob("*") if p.is_file()]
        return {"incident_id": incident_id, "files": sorted(files)}

    @api.get("/api/artifacts/{incident_id}/{file_path:path}")
    def get_artifact(incident_id: str, file_path: str) -> PlainTextResponse:
        root = _safe_artifact_path(Path(settings.artifacts_dir), incident_id)
        target = _safe_artifact_path(root, file_path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found")
        return PlainTextResponse(target.read_text(encoding="utf-8"))

    @api.get("/api/entity/{urn:path}")
    def get_entity(urn: str) -> dict:
        try:
            return orch.connector.get_entity(urn).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    if WEB_DIR.exists():
        api.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")

        @api.get("/")
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    return api
