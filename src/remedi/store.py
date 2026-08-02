"""Persisted remediation proposals — Apply writes the approved plan, not a re-codegen."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re

from remedi.models.incident import RemediationResult


class ProposalIntegrityError(ValueError):
    """Raised when a sealed proposal no longer matches its approval digest."""


class ProposalAlreadyAppliedError(ValueError):
    """Raised when an applied proposal is submitted for execution again."""


class ProposalStore:
    _SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.execution_root = self.root / "_executions"
        self.execution_root.mkdir(exist_ok=True)

    @classmethod
    def _validate_key(cls, value: str, label: str) -> str:
        if not cls._SAFE_KEY.fullmatch(value):
            raise KeyError(f"Invalid {label}: {value!r}")
        return value

    def _path(self, run_id: str) -> Path:
        return self.root / f"{self._validate_key(run_id, 'run_id')}.json"

    @staticmethod
    def _digest(result: RemediationResult) -> str:
        payload = result.model_dump(mode="json", exclude={"proposal_digest"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"

    def save(self, result: RemediationResult) -> Path:
        result.proposal_integrity = "sealed"
        result.proposal_digest = self._digest(result)
        path = self._path(result.run_id)
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        # latest pointer per incident for convenience
        incident_id = self._validate_key(result.incident.id, "incident_id")
        latest = self.root / f"latest-{incident_id}.json"
        latest.write_text(json.dumps({"run_id": result.run_id}), encoding="utf-8")
        return path

    def load(self, run_id: str) -> RemediationResult:
        path = self._path(run_id)
        if not path.exists():
            raise KeyError(f"Unknown proposal run_id: {run_id}")
        result = RemediationResult.model_validate_json(path.read_text(encoding="utf-8"))
        if result.proposal_integrity == "sealed":
            if not result.proposal_digest:
                raise ProposalIntegrityError(f"Proposal {run_id} is sealed but has no digest")
            actual = self._digest(result)
            if not hmac.compare_digest(result.proposal_digest, actual):
                raise ProposalIntegrityError(
                    f"Proposal {run_id} changed after approval; propose a new fix before applying"
                )
        return result

    def latest_for(self, incident_id: str) -> RemediationResult | None:
        pointer = self.root / f"latest-{self._validate_key(incident_id, 'incident_id')}.json"
        if not pointer.exists():
            return None
        run_id = json.loads(pointer.read_text(encoding="utf-8"))["run_id"]
        return self.load(run_id)

    def claim_execution(self, result: RemediationResult) -> Path:
        run_id = self._validate_key(result.run_id, "run_id")
        receipt = self.execution_root / f"{run_id}.json"
        payload = {
            "run_id": run_id,
            "incident_id": result.incident.id,
            "proposal_digest": result.proposal_digest,
            "status": "applying",
        }
        try:
            with receipt.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except FileExistsError as exc:
            raise ProposalAlreadyAppliedError(
                f"Proposal {run_id} was already applied; propose a new fix before acting again"
            ) from exc
        return receipt

    def finish_execution(
        self,
        result: RemediationResult,
        *,
        status: str,
    ) -> Path:
        run_id = self._validate_key(result.run_id, "run_id")
        receipt = self.execution_root / f"{run_id}.json"
        receipt.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "incident_id": result.incident.id,
                    "proposal_digest": result.proposal_digest,
                    "status": status,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return receipt

    def list_ids(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.json") if not p.name.startswith("latest-"))
