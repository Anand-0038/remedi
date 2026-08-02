"""DataHub connectors with an explicit trust boundary between replay and live modes."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path
from typing import Any

from remedi.config import Settings, get_settings
from remedi.connectors.audit import ToolAudit
from remedi.models.incident import (
    EntityRef,
    Incident,
    IncidentSeverity,
    IncidentType,
    SchemaField,
    WriteBackAction,
)


class DataHubConnector(ABC):
    audit: ToolAudit
    requires_assertion_rerun: bool = False

    @abstractmethod
    def list_incidents(self) -> list[Incident]:
        raise NotImplementedError

    @abstractmethod
    def get_incident(self, incident_id: str) -> Incident:
        raise NotImplementedError

    @abstractmethod
    def get_entity(self, urn: str) -> EntityRef:
        raise NotImplementedError

    @abstractmethod
    def get_lineage_downstream(self, urn: str, max_hops: int = 3) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_lineage_upstream(self, urn: str, max_hops: int = 3) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_dataset_queries(self, urn: str) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def search(self, query: str, *, entity_types: list[str] | None = None) -> list[EntityRef]:
        raise NotImplementedError

    @abstractmethod
    def list_failing_assertions(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def add_tags(self, urn: str, tags: list[str]) -> WriteBackAction:
        raise NotImplementedError

    @abstractmethod
    def update_description(self, urn: str, description: str) -> WriteBackAction:
        raise NotImplementedError

    @abstractmethod
    def add_owners(self, urn: str, owners: list[str]) -> WriteBackAction:
        raise NotImplementedError

    @abstractmethod
    def add_glossary_terms(self, urn: str, terms: list[str]) -> WriteBackAction:
        raise NotImplementedError

    @abstractmethod
    def save_document(self, title: str, body: str, related_urns: list[str]) -> WriteBackAction:
        raise NotImplementedError

    @abstractmethod
    def mark_incident_resolved(self, incident_id: str) -> WriteBackAction:
        raise NotImplementedError


class LiveIntegrationError(RuntimeError):
    """A real DataHub operation failed or is not configured.

    Live mode must never substitute fixture data or local writes for a failed
    provider operation. Callers surface this error as an unavailable dependency.
    """


class FixtureConnector(DataHubConnector):
    """Offline catalog slice — MCP-shaped tools with audit trail."""

    def __init__(self, fixtures_dir: Path, audit: ToolAudit | None = None) -> None:
        self.fixtures_dir = fixtures_dir
        self.audit = audit or ToolAudit()
        self._state_path = fixtures_dir / "catalog.state.json"
        self._catalog = self._load_catalog()
        self._write_log: list[dict[str, Any]] = []

    def _load_catalog(self) -> dict[str, Any]:
        base = self.fixtures_dir / "catalog.json"
        with base.open(encoding="utf-8") as f:
            catalog = json.load(f)
        if self._state_path.exists():
            with self._state_path.open(encoding="utf-8") as f:
                state = json.load(f)
            for urn, patch in state.get("entities", {}).items():
                if urn in catalog["entities"]:
                    catalog["entities"][urn].update(patch)
            resolved = set(state.get("resolved_incidents", []))
            for inc in catalog["incidents"]:
                if inc["id"] in resolved:
                    inc["status"] = "resolved"
        return catalog

    def _persist(self) -> None:
        entities_patch: dict[str, Any] = {}
        for urn, ent in self._catalog["entities"].items():
            entities_patch[urn] = {
                "tags": ent.get("tags", []),
                "owners": ent.get("owners", []),
                "description": ent.get("description"),
                "glossary_terms": ent.get("glossary_terms", []),
            }
        resolved = [i["id"] for i in self._catalog["incidents"] if i.get("status") == "resolved"]
        payload = {"entities": entities_patch, "resolved_incidents": resolved}
        self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        log_path = self.fixtures_dir / "writeback_log.json"
        log_path.write_text(json.dumps(self._write_log, indent=2), encoding="utf-8")

    def _entity_from_dict(self, raw: dict[str, Any]) -> EntityRef:
        fields = [
            SchemaField(
                name=f["name"],
                type=f["type"],
                description=f.get("description"),
                nullable=f.get("nullable", True),
            )
            for f in raw.get("schema_fields", [])
        ]
        return EntityRef(
            urn=raw["urn"],
            name=raw["name"],
            type=raw["type"],
            platform=raw.get("platform"),
            owners=list(raw.get("owners", [])),
            tags=list(raw.get("tags", [])),
            description=raw.get("description"),
            schema_fields=fields,
        )

    def _incident_from_raw(self, raw: dict[str, Any], *, source: str = "incident") -> Incident:
        entity = self._entity_from_dict(self._catalog["entities"][raw["entity_urn"]])
        status = (
            "resolved"
            if raw.get("status") == "resolved" or "remedi-resolved" in entity.tags
            else "open"
        )
        return Incident(
            id=raw["id"],
            title=raw["title"],
            type=IncidentType(raw["type"]),
            severity=IncidentSeverity(raw["severity"]),
            entity=entity,
            details=raw.get("details", {}),
            sample_queries=list(raw.get("sample_queries", [])),
            status=status,
            detection_source=source,
        )

    def list_failing_assertions(self) -> list[dict[str, Any]]:
        with self.audit.track("search_quality_assertions", status="FAILURE") as meta:
            assertions = [
                a for a in self._catalog.get("assertions", []) if a.get("status") == "FAILURE"
            ]
            meta["detail"] = f"{len(assertions)} failing"
            return assertions

    def list_incidents(self) -> list[Incident]:
        # Compose quality assertions → incidents (DataHub quality handoff), then planted incidents
        with self.audit.track("list_incidents") as meta:
            by_id: dict[str, Incident] = {}
            for assertion in self.list_failing_assertions():
                # map assertion → incident shape if linked
                linked = assertion.get("incident_id")
                if linked:
                    raw = next((i for i in self._catalog["incidents"] if i["id"] == linked), None)
                    if raw:
                        by_id[linked] = self._incident_from_raw(raw, source="assertion")
                else:
                    # synthesize lightweight incident from assertion
                    entity_urn = assertion["entity_urn"]
                    entity = self._entity_from_dict(self._catalog["entities"][entity_urn])
                    syn_id = assertion["id"]
                    by_id[syn_id] = Incident(
                        id=syn_id,
                        title=assertion.get("title", f"Assertion failed: {assertion['id']}"),
                        type=IncidentType(assertion.get("type", "data_quality")),
                        severity=IncidentSeverity(assertion.get("severity", "high")),
                        entity=entity,
                        details=assertion.get("details", {}),
                        sample_queries=list(assertion.get("sample_queries", [])),
                        detection_source="assertion",
                    )
            for raw in self._catalog["incidents"]:
                if raw["id"] not in by_id:
                    by_id[raw["id"]] = self._incident_from_raw(raw, source="incident")
            out = list(by_id.values())
            meta["detail"] = f"{len(out)} incidents"
            return out

    def get_incident(self, incident_id: str) -> Incident:
        with self.audit.track("get_entities", incident_id=incident_id) as meta:
            raw = next((i for i in self._catalog["incidents"] if i["id"] == incident_id), None)
            if raw:
                meta["detail"] = raw["entity_urn"]
                source = (
                    "assertion"
                    if any(
                        a.get("incident_id") == incident_id and a.get("status") == "FAILURE"
                        for a in self._catalog.get("assertions", [])
                    )
                    else "incident"
                )
                return self._incident_from_raw(raw, source=source)
            # assertion-only synthetic
            for assertion in self._catalog.get("assertions", []):
                if assertion.get("id") == incident_id and assertion.get("status") == "FAILURE":
                    entity = self._entity_from_dict(
                        self._catalog["entities"][assertion["entity_urn"]]
                    )
                    meta["detail"] = assertion["entity_urn"]
                    return Incident(
                        id=assertion["id"],
                        title=assertion.get("title", assertion["id"]),
                        type=IncidentType(assertion.get("type", "data_quality")),
                        severity=IncidentSeverity(assertion.get("severity", "high")),
                        entity=entity,
                        details=assertion.get("details", {}),
                        sample_queries=list(assertion.get("sample_queries", [])),
                        detection_source="assertion",
                    )
            meta["status"] = "error"
            raise KeyError(f"Unknown incident: {incident_id}")

    def get_entity(self, urn: str) -> EntityRef:
        with self.audit.track("get_entities", urn=urn) as meta:
            raw = self._catalog["entities"].get(urn)
            if not raw:
                meta["status"] = "error"
                raise KeyError(f"Unknown entity: {urn}")
            ent = self._entity_from_dict(raw)
            meta["detail"] = f"{len(ent.schema_fields)} fields"
            return ent

    def get_lineage_downstream(self, urn: str, max_hops: int = 3) -> list[dict[str, Any]]:
        """BFS over adjacency list — hops computed, not pre-baked."""
        with self.audit.track(
            "get_lineage", urn=urn, direction="downstream", max_hops=max_hops
        ) as meta:
            results = self._bfs_lineage(urn, direction="downstream", max_hops=max_hops)
            meta["detail"] = f"{len(results)} nodes"
            return results

    def get_lineage_upstream(self, urn: str, max_hops: int = 3) -> list[dict[str, Any]]:
        with self.audit.track(
            "get_lineage", urn=urn, direction="upstream", max_hops=max_hops
        ) as meta:
            results = self._bfs_lineage(urn, direction="upstream", max_hops=max_hops)
            meta["detail"] = f"{len(results)} nodes"
            return results

    def _bfs_lineage(self, urn: str, *, direction: str, max_hops: int) -> list[dict[str, Any]]:
        if direction == "downstream":
            adjacency: dict[str, list[dict[str, Any]]] = self._catalog.get("lineage", {})
        else:
            # invert downstream adjacency
            adjacency = {}
            for parent, edges in self._catalog.get("lineage", {}).items():
                for edge in edges:
                    adjacency.setdefault(edge["urn"], []).append(
                        {
                            "urn": parent,
                            "impact_reason": edge.get("impact_reason", "upstream producer"),
                            "columns": edge.get("columns", []),
                        }
                    )
            # explicit upstream map wins when present
            for parent, edges in self._catalog.get("lineage_upstream", {}).items():
                adjacency.setdefault(parent, []).extend(edges)

        results: list[dict[str, Any]] = []
        seen: set[str] = {urn}
        queue: deque[tuple[str, int]] = deque([(urn, 0)])
        while queue:
            current, depth = queue.popleft()
            if depth >= max_hops:
                continue
            for edge in adjacency.get(current, []):
                child_urn = edge["urn"]
                if child_urn in seen or child_urn not in self._catalog["entities"]:
                    continue
                seen.add(child_urn)
                hop = depth + 1
                child = self._entity_from_dict(self._catalog["entities"][child_urn])
                results.append(
                    {
                        "entity": child,
                        "hop": hop,
                        "impact_reason": edge.get("impact_reason", "related"),
                        "parent_urn": current,
                        "columns": list(edge.get("columns", [])),
                    }
                )
                queue.append((child_urn, hop))
        return results

    def get_dataset_queries(self, urn: str) -> list[str]:
        with self.audit.track("get_dataset_queries", urn=urn) as meta:
            qs = list(self._catalog.get("queries", {}).get(urn, []))
            meta["detail"] = f"{len(qs)} queries"
            return qs

    def search(self, query: str, *, entity_types: list[str] | None = None) -> list[EntityRef]:
        with self.audit.track("search", query=query, entity_types=entity_types or []) as meta:
            q = query.lower()
            out: list[EntityRef] = []
            for raw in self._catalog["entities"].values():
                if entity_types and raw.get("type") not in entity_types:
                    continue
                blob = f"{raw.get('name', '')} {raw.get('description', '')} {' '.join(raw.get('tags', []))}".lower()
                if q in blob or any(q in t.lower() for t in raw.get("tags", [])):
                    out.append(self._entity_from_dict(raw))
            meta["detail"] = f"{len(out)} hits"
            return out

    def add_tags(self, urn: str, tags: list[str]) -> WriteBackAction:
        with self.audit.track("add_tags", urn=urn, tags=tags) as meta:
            ent = self._catalog["entities"][urn]
            existing = set(ent.get("tags", []))
            existing.update(tags)
            ent["tags"] = sorted(existing)
            self._write_log.append({"action": "add_tags", "urn": urn, "tags": tags, "status": "ok"})
            self._persist()
            meta["detail"] = ",".join(tags)
            return WriteBackAction(
                action=f"add_tags:{','.join(tags)}", status="ok", detail="fixture catalog updated"
            )

    def update_description(self, urn: str, description: str) -> WriteBackAction:
        with self.audit.track("update_description", urn=urn) as meta:
            self._catalog["entities"][urn]["description"] = description
            self._write_log.append({"action": "update_description", "urn": urn, "status": "ok"})
            self._persist()
            meta["detail"] = f"{len(description)} chars"
            return WriteBackAction(
                action="update_description", status="ok", detail=f"{len(description)} chars"
            )

    def add_owners(self, urn: str, owners: list[str]) -> WriteBackAction:
        with self.audit.track("add_owners", urn=urn, owners=owners) as meta:
            ent = self._catalog["entities"][urn]
            existing = set(ent.get("owners", []))
            existing.update(owners)
            ent["owners"] = sorted(existing)
            self._write_log.append(
                {"action": "add_owners", "urn": urn, "owners": owners, "status": "ok"}
            )
            self._persist()
            meta["detail"] = str(len(owners))
            return WriteBackAction(
                action=f"add_owners:{len(owners)}", status="ok", detail=", ".join(owners[:5])
            )

    def add_glossary_terms(self, urn: str, terms: list[str]) -> WriteBackAction:
        with self.audit.track("add_glossary_terms", urn=urn, terms=terms) as meta:
            ent = self._catalog["entities"][urn]
            existing = set(ent.get("glossary_terms", []))
            existing.update(terms)
            ent["glossary_terms"] = sorted(existing)
            # also reflect as tags for visibility in UI before/after
            tag_set = set(ent.get("tags", []))
            tag_set.update(f"glossary:{t}" for t in terms)
            ent["tags"] = sorted(tag_set)
            self._write_log.append(
                {"action": "add_glossary_terms", "urn": urn, "terms": terms, "status": "ok"}
            )
            self._persist()
            meta["detail"] = ",".join(terms)
            return WriteBackAction(
                action=f"add_glossary_terms:{','.join(terms)}",
                status="ok",
                detail="fixture glossary linked",
            )

    def save_document(self, title: str, body: str, related_urns: list[str]) -> WriteBackAction:
        with self.audit.track("save_document", title=title, related_urns=related_urns) as meta:
            doc_id = f"doc:{title.lower().replace(' ', '-')}"
            self._write_log.append(
                {
                    "action": "save_document",
                    "id": doc_id,
                    "title": title,
                    "related_urns": related_urns,
                    "body_preview": body[:200],
                    "status": "ok",
                }
            )
            docs = self._catalog.setdefault("documents", {})
            docs[doc_id] = {"title": title, "body": body, "related_urns": related_urns}
            self._persist()
            meta["detail"] = doc_id
            return WriteBackAction(action=f"save_document:{doc_id}", status="ok", detail=title)

    def mark_incident_resolved(self, incident_id: str) -> WriteBackAction:
        with self.audit.track("mark_resolved", incident_id=incident_id) as meta:
            for inc in self._catalog["incidents"]:
                if inc["id"] == incident_id:
                    inc["status"] = "resolved"
                    self._write_log.append(
                        {"action": "mark_resolved", "incident_id": incident_id, "status": "ok"}
                    )
                    self._persist()
                    meta["detail"] = "resolved"
                    return WriteBackAction(action=f"mark_resolved:{incident_id}", status="ok")
            meta["status"] = "error"
            return WriteBackAction(
                action=f"mark_resolved:{incident_id}", status="error", detail="not found"
            )

    @property
    def write_log(self) -> list[dict[str, Any]]:
        return list(self._write_log)


class LiveConnector(DataHubConnector):
    requires_assertion_rerun = True

    _FAILING_ASSERTIONS_QUERY = """
    query RemediFailingAssertions($start: Int!, $count: Int!) {
      searchAcrossEntities(
        input: {
          query: "*"
          types: [ASSERTION]
          start: $start
          count: $count
          searchFlags: { skipHighlighting: true }
        }
      ) {
        start
        count
        total
        searchResults {
          entity {
            ... on Assertion {
              urn
              info {
                type
                description
                externalUrl
                datasetAssertion { datasetUrn }
                freshnessAssertion { entityUrn }
                volumeAssertion { entityUrn }
                sqlAssertion { entityUrn }
                fieldAssertion { entityUrn }
                schemaAssertion { entityUrn }
                customAssertion {
                  entityUrn
                  logic
                }
              }
              runEvents(limit: 1) {
                runEvents {
                  status
                  result { type severity }
                }
              }
            }
          }
        }
      }
    }
    """

    """Strict live DataHub SDK reads and Agent Context mutations.

    The replay connector is intentionally not constructed here. A failed live
    operation is an error, never permission to return fixture data or record a
    local write as if DataHub accepted it.
    """

    def __init__(self, settings: Settings, audit: ToolAudit | None = None) -> None:
        self.settings = settings
        self.audit = audit or ToolAudit()
        self._client = None
        self._incidents: dict[str, Incident] = {}
        self._init_client()

    def _init_client(self) -> None:
        try:
            from datahub.sdk.main_client import DataHubClient

            self._client = DataHubClient(
                server=self.settings.datahub_gms_url,
                token=self.settings.datahub_token or None,
            )
            self._client.test_connection()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Live mode requires a reachable DataHub GMS and the live dependencies. "
                "Install with `uv sync --extra live` and verify DATAHUB_GMS_URL / "
                "DATAHUB_TOKEN. "
                f"Underlying error: {exc}"
            ) from exc

    def list_incidents(self) -> list[Incident]:
        incidents: list[Incident] = []
        for assertion in self.list_failing_assertions():
            entity_urn = str(assertion["entity_urn"])
            entity = self.get_entity(entity_urn)
            assertion_type = str(assertion.get("type") or "CUSTOM").upper()
            incident_type = {
                "FRESHNESS": IncidentType.FRESHNESS,
                "DATA_SCHEMA": IncidentType.SCHEMA_DRIFT,
            }.get(assertion_type, IncidentType.DATA_QUALITY)
            assertion_urn = str(assertion["urn"])
            incident_id = f"assertion-{hashlib.sha256(assertion_urn.encode()).hexdigest()[:16]}"
            description = str(assertion.get("description") or "").strip()
            incident = Incident(
                id=incident_id,
                title=description or f"Failing {assertion_type.lower()} assertion on {entity.name}",
                type=incident_type,
                severity=IncidentSeverity.HIGH,
                entity=entity,
                details={
                    "assertion_urn": assertion_urn,
                    "assertion": assertion,
                },
                detection_source="assertion",
            )
            incidents.append(incident)
        self._incidents = {incident.id: incident for incident in incidents}
        return incidents

    def list_failing_assertions(self) -> list[dict[str, Any]]:
        assert self._client is not None
        with self.audit.track(
            "search_quality_assertions",
            status="FAILING",
            mode="live",
        ) as meta:
            try:
                from datahub_agent_context.mcp_tools.base import execute_graphql

                assertions: list[dict[str, Any]] = []
                start = 0
                page_size = 100
                while True:
                    response = execute_graphql(
                        self._client._graph,
                        query=self._FAILING_ASSERTIONS_QUERY,
                        variables={"start": start, "count": page_size},
                        operation_name="RemediFailingAssertions",
                    )
                    page = response.get("searchAcrossEntities") or {}
                    results = page.get("searchResults") or []
                    for result in results:
                        assertion = result.get("entity") or {}
                        run_events = (assertion.get("runEvents") or {}).get("runEvents") or []
                        latest_result = (run_events[0].get("result") or {}) if run_events else {}
                        if latest_result.get("type") != "FAILURE":
                            continue
                        info = assertion.get("info") or {}
                        entity_urn = self._assertion_entity_urn(info)
                        assertion_urn = assertion.get("urn")
                        if entity_urn and assertion_urn:
                            assertions.append(
                                {
                                    "urn": assertion_urn,
                                    "type": info.get("type"),
                                    "description": info.get("description"),
                                    "externalUrl": info.get("externalUrl"),
                                    "definition": self._assertion_definition(info),
                                    "entity_urn": entity_urn,
                                }
                            )
                    start += len(results)
                    total = int(page.get("total") or 0)
                    if not results or start >= total:
                        break
                meta["detail"] = f"live Agent Context · {len(assertions)} failing assertions"
                return assertions
            except Exception as exc:  # noqa: BLE001
                meta["status"] = "error"
                meta["detail"] = str(exc)
                raise LiveIntegrationError(
                    f"DataHub failing-assertion discovery failed: {exc}"
                ) from exc

    @staticmethod
    def _assertion_definition(info: dict[str, Any]) -> dict[str, Any]:
        for key in (
            "datasetAssertion",
            "freshnessAssertion",
            "volumeAssertion",
            "sqlAssertion",
            "fieldAssertion",
            "schemaAssertion",
            "customAssertion",
        ):
            definition = info.get(key)
            if definition:
                return definition
        return {}

    @classmethod
    def _assertion_entity_urn(cls, info: dict[str, Any]) -> str | None:
        definition = cls._assertion_definition(info)
        entity_urn = definition.get("entityUrn") or definition.get("datasetUrn")
        return str(entity_urn) if entity_urn else None

    def get_incident(self, incident_id: str) -> Incident:
        if incident_id not in self._incidents:
            self.list_incidents()
        try:
            return self._incidents[incident_id].model_copy(deep=True)
        except KeyError as exc:
            raise KeyError(f"Unknown live assertion incident: {incident_id}") from exc

    @staticmethod
    def _string_values(values: Any, *attrs: str) -> list[str]:
        out: list[str] = []
        for value in values or []:
            current = value
            for attr in attrs:
                candidate = getattr(current, attr, None)
                if candidate is not None:
                    current = candidate
                    break
            text = str(current)
            if text and text not in out:
                out.append(text)
        return out

    def get_entity(self, urn: str) -> EntityRef:
        assert self._client is not None
        with self.audit.track("get_entities", urn=urn, mode="live") as meta:
            try:
                entity = self._client.entities.get(urn)
                name = (
                    getattr(entity, "display_name", None)
                    or getattr(entity, "name", None)
                    or getattr(getattr(entity, "urn", None), "name", None)
                    or urn.split(",")[-1].rstrip(")")
                )
                entity_type = getattr(entity, "entity_type", entity.__class__.__name__)
                platform = getattr(entity, "platform", None) or getattr(
                    getattr(entity, "urn", None),
                    "platform",
                    None,
                )
                description = getattr(entity, "description", None)
                owners = self._string_values(getattr(entity, "owners", []), "owner", "urn")
                tags = self._string_values(getattr(entity, "tags", []), "tag", "urn")
                raw_fields = (
                    getattr(entity, "schema_fields", None) or getattr(entity, "schema", None) or []
                )
                fields = [
                    SchemaField(
                        name=str(
                            getattr(
                                field,
                                "field_path",
                                getattr(field, "name", ""),
                            )
                        ),
                        type=str(
                            getattr(
                                field,
                                "native_type",
                                getattr(
                                    field,
                                    "native_data_type",
                                    getattr(field, "type", "unknown"),
                                ),
                            )
                        ),
                        description=getattr(field, "description", None),
                        nullable=bool(getattr(field, "nullable", True)),
                    )
                    for field in raw_fields
                    if getattr(field, "field_path", getattr(field, "name", None))
                ]
                live = EntityRef(
                    urn=urn,
                    name=str(name),
                    type=str(entity_type),
                    platform=str(platform) if platform else None,
                    owners=owners,
                    tags=tags,
                    description=str(description) if description else None,
                    schema_fields=fields,
                )
                meta["detail"] = f"live SDK · {len(fields)} fields"
                return live
            except Exception as exc:  # noqa: BLE001
                meta["status"] = "error"
                meta["detail"] = str(exc)
                raise LiveIntegrationError(f"DataHub entity read failed for {urn}: {exc}") from exc

    def get_lineage_downstream(self, urn: str, max_hops: int = 3) -> list[dict[str, Any]]:
        return self._get_live_lineage(urn, direction="downstream", max_hops=max_hops)

    def get_lineage_upstream(self, urn: str, max_hops: int = 3) -> list[dict[str, Any]]:
        return self._get_live_lineage(urn, direction="upstream", max_hops=max_hops)

    def _get_live_lineage(
        self,
        urn: str,
        *,
        direction: str,
        max_hops: int,
    ) -> list[dict[str, Any]]:
        assert self._client is not None
        with self.audit.track(
            "get_lineage",
            urn=urn,
            direction=direction,
            max_hops=max_hops,
            mode="live",
        ) as meta:
            try:
                related = self._client.lineage.get_lineage(
                    source_urn=urn,
                    direction=direction,
                    max_hops=max_hops,
                    count=500,
                )
                results: list[dict[str, Any]] = []
                for item in related:
                    item_urn = str(item.urn)
                    try:
                        entity = self.get_entity(item_urn)
                    except Exception:  # noqa: BLE001
                        entity = EntityRef(
                            urn=item_urn,
                            name=str(item.name or item_urn),
                            type=str(item.type),
                            platform=str(item.platform) if item.platform else None,
                            description=item.description,
                        )
                    columns = sorted(
                        {str(path.column_name) for path in (item.paths or []) if path.column_name}
                    )
                    results.append(
                        {
                            "entity": entity,
                            "hop": int(item.hops),
                            "impact_reason": f"live DataHub {direction} lineage",
                            "parent_urn": urn,
                            "columns": columns,
                        }
                    )
                meta["detail"] = f"live SDK · {len(results)} nodes"
                return results
            except Exception as exc:  # noqa: BLE001
                meta["status"] = "error"
                meta["detail"] = f"live lineage failed: {exc}"
                raise LiveIntegrationError(
                    f"DataHub {direction} lineage read failed for {urn}: {exc}"
                ) from exc

    def get_dataset_queries(self, urn: str) -> list[str]:
        assert self._client is not None
        with self.audit.track("get_dataset_queries", urn=urn, mode="live") as meta:
            try:
                from datahub_agent_context.context import DataHubContext
                from datahub_agent_context.mcp_tools.queries import (
                    get_dataset_queries,
                )

                with DataHubContext(self._client):
                    response = get_dataset_queries(urn=urn, count=10)
                queries = [
                    str(query["properties"]["statement"]["value"])
                    for query in response.get("queries", [])
                    if query.get("properties", {}).get("statement", {}).get("value")
                ]
                meta["detail"] = f"live Agent Context · {len(queries)} queries"
                return queries
            except Exception as exc:  # noqa: BLE001
                meta["status"] = "error"
                meta["detail"] = str(exc)
                raise LiveIntegrationError(
                    f"DataHub query-history read failed for {urn}: {exc}"
                ) from exc

    def search(self, query: str, *, entity_types: list[str] | None = None) -> list[EntityRef]:
        assert self._client is not None
        with self.audit.track(
            "search",
            query=query,
            entity_types=entity_types or [],
            mode="live",
        ) as meta:
            try:
                search_filter = None
                if entity_types:
                    from datahub.sdk.search_filters import FilterDsl

                    search_filter = FilterDsl.entity_type(entity_types)
                urns = list(
                    self._client.search.get_urns(
                        query=query or "*",
                        filter=search_filter,
                        skip_cache=True,
                    )
                )[:100]
                entities = [self.get_entity(str(found)) for found in urns]
                meta["detail"] = f"live SDK · {len(entities)} hits"
                return entities
            except Exception as exc:  # noqa: BLE001
                meta["status"] = "error"
                meta["detail"] = f"live search failed: {exc}"
                raise LiveIntegrationError(f"DataHub search failed for {query!r}: {exc}") from exc

    def add_tags(self, urn: str, tags: list[str]) -> WriteBackAction:
        try:
            from datahub.sdk import Tag
            from datahub_agent_context.context import DataHubContext
            from datahub_agent_context.mcp_tools.tags import add_tags as mcp_add_tags

            assert self._client is not None
            tag_urns: list[str] = []
            for tag in tags:
                tag_entity = Tag(
                    name=tag,
                    display_name=tag,
                    description="Created by Remedi remediation workflow",
                )
                tag_urns.append(str(tag_entity.urn))
                graph = getattr(self._client, "_graph", None)
                if graph is None or not graph.exists(tag_urns[-1]):
                    self._client.entities.upsert(tag_entity)
            with DataHubContext(self._client):
                mcp_add_tags(tag_urns=tag_urns, entity_urns=[urn])
            self.audit.record(
                "add_tags", args={"urn": urn, "tags": tags}, status="ok", detail="live MCP"
            )
            return WriteBackAction(
                action=f"add_tags:{','.join(tags)}", status="ok", detail="live MCP"
            )
        except Exception as exc:  # noqa: BLE001
            self.audit.record(
                "add_tags",
                args={"urn": urn, "tags": tags},
                status="error",
                detail=str(exc),
            )
            raise LiveIntegrationError(f"DataHub add_tags failed for {urn}: {exc}") from exc

    def update_description(self, urn: str, description: str) -> WriteBackAction:
        try:
            from datahub_agent_context.context import DataHubContext
            from datahub_agent_context.mcp_tools.descriptions import update_description as mcp_upd

            assert self._client is not None
            with DataHubContext(self._client):
                mcp_upd(
                    entity_urn=urn,
                    operation="replace",
                    description=description,
                )
            self.audit.record(
                "update_description", args={"urn": urn}, status="ok", detail="live MCP"
            )
            return WriteBackAction(action="update_description", status="ok", detail="live MCP")
        except Exception as exc:  # noqa: BLE001
            self.audit.record(
                "update_description", args={"urn": urn}, status="error", detail=str(exc)
            )
            raise LiveIntegrationError(
                f"DataHub update_description failed for {urn}: {exc}"
            ) from exc

    def add_owners(self, urn: str, owners: list[str]) -> WriteBackAction:
        try:
            from datahub_agent_context.context import DataHubContext
            from datahub_agent_context.mcp_tools.owners import (
                OwnershipType,
                add_owners as mcp_owners,
            )

            assert self._client is not None
            with DataHubContext(self._client):
                mcp_owners(
                    owner_urns=owners,
                    entity_urns=[urn],
                    ownership_type=OwnershipType.TECHNICAL_OWNER,
                )
            self.audit.record("add_owners", args={"urn": urn}, status="ok", detail="live MCP")
            return WriteBackAction(
                action=f"add_owners:{len(owners)}", status="ok", detail="live MCP"
            )
        except Exception as exc:  # noqa: BLE001
            self.audit.record("add_owners", args={"urn": urn}, status="error", detail=str(exc))
            raise LiveIntegrationError(f"DataHub add_owners failed for {urn}: {exc}") from exc

    def add_glossary_terms(self, urn: str, terms: list[str]) -> WriteBackAction:
        try:
            from datahub.sdk import GlossaryTerm
            from datahub_agent_context.context import DataHubContext
            from datahub_agent_context.mcp_tools.terms import (
                add_glossary_terms as mcp_terms,
            )

            assert self._client is not None
            term_urns: list[str] = []
            for term in terms:
                term_entity = GlossaryTerm(
                    id=term,
                    display_name=term,
                    definition="Remedi remediation workflow classification",
                )
                term_urns.append(str(term_entity.urn))
                graph = getattr(self._client, "_graph", None)
                if graph is None or not graph.exists(term_urns[-1]):
                    self._client.entities.upsert(term_entity)
            with DataHubContext(self._client):
                mcp_terms(term_urns=term_urns, entity_urns=[urn])
            self.audit.record(
                "add_glossary_terms",
                args={"urn": urn, "terms": terms},
                status="ok",
                detail="live MCP",
            )
            return WriteBackAction(
                action=f"add_glossary_terms:{','.join(terms)}",
                status="ok",
                detail="live MCP",
            )
        except Exception as exc:  # noqa: BLE001
            self.audit.record(
                "add_glossary_terms",
                args={"urn": urn, "terms": terms},
                status="error",
                detail=str(exc),
            )
            raise LiveIntegrationError(
                f"DataHub add_glossary_terms failed for {urn}: {exc}"
            ) from exc

    def save_document(self, title: str, body: str, related_urns: list[str]) -> WriteBackAction:
        try:
            from datahub_agent_context.context import DataHubContext
            from datahub_agent_context.mcp_tools.save_document import (
                save_document as mcp_doc,
            )

            assert self._client is not None
            with DataHubContext(self._client):
                result = mcp_doc(
                    document_type="Summary",
                    title=title,
                    content=body,
                    topics=["remediation", "data-quality"],
                    related_assets=related_urns,
                )
            if not result.get("success") or not result.get("urn"):
                raise RuntimeError(result.get("message") or "document write returned no URN")
            document_urn = str(result["urn"])
            self.audit.record(
                "save_document", args={"title": title}, status="ok", detail="live MCP"
            )
            return WriteBackAction(
                action=f"save_document:{document_urn}",
                status="ok",
                detail="live MCP",
            )
        except Exception as exc:  # noqa: BLE001
            self.audit.record(
                "save_document", args={"title": title}, status="error", detail=str(exc)
            )
            raise LiveIntegrationError(f"DataHub save_document failed: {exc}") from exc

    def mark_incident_resolved(self, incident_id: str) -> WriteBackAction:
        return WriteBackAction(
            action=f"await_assertion_rerun:{incident_id}",
            status="ok",
            detail=(
                "Approved remediation evidence was written to DataHub; the failing assertion "
                "remains active until DataHub records a real passing run."
            ),
        )


def build_connector(
    settings: Settings | None = None, audit: ToolAudit | None = None
) -> DataHubConnector:
    settings = settings or get_settings()
    audit = audit or ToolAudit()
    if settings.remedi_mode == "live":
        return LiveConnector(settings, audit=audit)
    return FixtureConnector(settings.fixtures_dir, audit=audit)
