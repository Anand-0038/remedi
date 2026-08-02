import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from remedi.config import Settings
from remedi.connectors.audit import ToolAudit
from remedi.connectors.datahub import LiveConnector, LiveIntegrationError
from remedi.models.incident import IncidentSeverity, IncidentType


def _live_connector(client: object) -> LiveConnector:
    connector = object.__new__(LiveConnector)
    connector.settings = Settings(remedi_mode="live", datahub_token="test-token")
    connector.audit = ToolAudit()
    connector._client = client
    return connector


def test_live_mode_isolates_runtime_artifacts_from_committed_samples():
    assert Settings(remedi_mode="live").artifacts_dir.as_posix() == ".remedi/generated"
    assert Settings(remedi_mode="fixture").artifacts_dir.as_posix() == ".remedi/generated"
    assert (
        Settings(remedi_mode="live", artifacts_dir="custom-output").artifacts_dir.as_posix()
        == "custom-output"
    )


def test_live_entity_failure_never_returns_fixture_data():
    class BrokenEntities:
        def get(self, _urn: str) -> object:
            raise ConnectionError("GMS unavailable")

    connector = _live_connector(SimpleNamespace(entities=BrokenEntities()))

    with pytest.raises(LiveIntegrationError, match="entity read failed"):
        connector.get_entity("urn:li:dataset:test")

    assert not hasattr(connector, "_fixture")
    assert connector.audit.calls[-1].status == "error"


def test_live_incident_adapter_returns_empty_when_no_assertions_fail(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "datahub_agent_context.mcp_tools.base.execute_graphql",
        lambda *_args, **_kwargs: {
            "searchAcrossEntities": {
                "start": 0,
                "count": 0,
                "total": 0,
                "searchResults": [],
            }
        },
    )
    connector = _live_connector(SimpleNamespace(_graph=object()))

    assert connector.list_incidents() == []


def test_custom_assertion_preserves_freshness_semantics():
    assertion = {
        "type": "CUSTOM",
        "description": "Order freshness guard failed",
        "definition": {"logic": "updated_at must stay within the watermark"},
    }

    assert LiveConnector._incident_type(assertion) is IncidentType.FRESHNESS


def test_live_assertion_preserves_reported_severity():
    assert LiveConnector._incident_severity({"severity": "MEDIUM"}) is IncidentSeverity.MEDIUM
    assert LiveConnector._incident_severity({"severity": "unexpected"}) is IncidentSeverity.HIGH


def test_live_assertion_discovery_maps_assertee_without_scanning_datasets(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    def execute_graphql(*_args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "searchAcrossEntities": {
                "start": 0,
                "count": 1,
                "total": 1,
                "searchResults": [
                    {
                        "entity": {
                            "urn": "urn:li:assertion:orders-not-null",
                            "runEvents": {
                                "runEvents": [
                                    {
                                        "status": "COMPLETE",
                                        "result": {"type": "FAILURE", "severity": "HIGH"},
                                    }
                                ]
                            },
                            "info": {
                                "type": "CUSTOM",
                                "description": "Orders must have an id",
                                "customAssertion": {
                                    "entityUrn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders,PROD)"
                                },
                            },
                        }
                    }
                ],
            }
        }

    monkeypatch.setattr(
        "datahub_agent_context.mcp_tools.base.execute_graphql",
        execute_graphql,
    )
    connector = _live_connector(SimpleNamespace(_graph=object()))

    assertions = connector.list_failing_assertions()

    assert assertions[0]["urn"] == "urn:li:assertion:orders-not-null"
    assert assertions[0]["entity_urn"].endswith("snowflake,orders,PROD)")
    assert captured["operation_name"] == "RemediFailingAssertions"


def test_live_assertion_discovery_paginates_all_results(
    monkeypatch: pytest.MonkeyPatch,
):
    starts: list[int] = []

    def execute_graphql(*_args: object, **kwargs: object) -> dict[str, object]:
        start = int(kwargs["variables"]["start"])  # type: ignore[index]
        starts.append(start)
        entity = {
            "urn": f"urn:li:assertion:check-{start}",
            "runEvents": {
                "runEvents": [
                    {
                        "status": "COMPLETE",
                        "result": {"type": "FAILURE", "severity": "HIGH"},
                    }
                ]
            },
            "info": {
                "type": "CUSTOM",
                "customAssertion": {"entityUrn": f"urn:li:dataset:test-{start}"},
            },
        }
        return {
            "searchAcrossEntities": {
                "start": start,
                "count": 1,
                "total": 2,
                "searchResults": [{"entity": entity}],
            }
        }

    monkeypatch.setattr(
        "datahub_agent_context.mcp_tools.base.execute_graphql",
        execute_graphql,
    )
    connector = _live_connector(SimpleNamespace(_graph=object()))

    assertions = connector.list_failing_assertions()

    assert starts == [0, 1]
    assert [item["urn"] for item in assertions] == [
        "urn:li:assertion:check-0",
        "urn:li:assertion:check-1",
    ]


def test_live_assertion_discovery_excludes_latest_successful_run(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "datahub_agent_context.mcp_tools.base.execute_graphql",
        lambda *_args, **_kwargs: {
            "searchAcrossEntities": {
                "start": 0,
                "count": 1,
                "total": 1,
                "searchResults": [
                    {
                        "entity": {
                            "urn": "urn:li:assertion:passing",
                            "runEvents": {
                                "runEvents": [
                                    {
                                        "status": "COMPLETE",
                                        "result": {"type": "SUCCESS"},
                                    }
                                ]
                            },
                            "info": {
                                "type": "CUSTOM",
                                "customAssertion": {"entityUrn": "urn:li:dataset:passing"},
                            },
                        }
                    }
                ],
            }
        },
    )
    connector = _live_connector(SimpleNamespace(_graph=object()))

    assert connector.list_failing_assertions() == []


def test_live_write_failure_is_not_recorded_locally(monkeypatch: pytest.MonkeyPatch):
    tags_module = ModuleType("datahub_agent_context.mcp_tools.tags")

    def fail_add_tags(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("write rejected")

    setattr(tags_module, "add_tags", fail_add_tags)
    monkeypatch.setitem(sys.modules, "datahub_agent_context", ModuleType("datahub_agent_context"))
    monkeypatch.setitem(
        sys.modules,
        "datahub_agent_context.mcp_tools",
        ModuleType("datahub_agent_context.mcp_tools"),
    )
    monkeypatch.setitem(sys.modules, "datahub_agent_context.mcp_tools.tags", tags_module)
    connector = _live_connector(SimpleNamespace())

    with pytest.raises(LiveIntegrationError, match="add_tags failed"):
        connector.add_tags("urn:li:dataset:test", ["remedi-resolved"])

    assert not hasattr(connector, "_fixture")
    assert connector.audit.calls[-1].status == "error"


def test_live_tag_write_uses_agent_context_signature(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}
    tags_module = ModuleType("datahub_agent_context.mcp_tools.tags")

    def add_tags(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"success": True}

    class Entities:
        def upsert(self, entity: object) -> None:
            captured["upserted"] = entity

    setattr(tags_module, "add_tags", add_tags)
    monkeypatch.setitem(sys.modules, "datahub_agent_context.mcp_tools.tags", tags_module)
    connector = _live_connector(SimpleNamespace(entities=Entities()))

    result = connector.add_tags("urn:li:dataset:test", ["remedi-resolved"])

    assert result.status == "ok"
    assert captured["entity_urns"] == ["urn:li:dataset:test"]
    assert captured["tag_urns"] == ["urn:li:tag:remedi-resolved"]
    assert "upserted" in captured


def test_live_tag_write_rejects_false_provider_result(monkeypatch: pytest.MonkeyPatch):
    tags_module = ModuleType("datahub_agent_context.mcp_tools.tags")
    setattr(tags_module, "add_tags", lambda **_kwargs: {"success": False, "message": "denied"})
    monkeypatch.setitem(sys.modules, "datahub_agent_context.mcp_tools.tags", tags_module)
    connector = _live_connector(SimpleNamespace(entities=SimpleNamespace(upsert=lambda _tag: None)))

    with pytest.raises(LiveIntegrationError, match="denied"):
        connector.add_tags("urn:li:dataset:test", ["remedi-applied"])


def test_live_document_false_response_is_an_error(monkeypatch: pytest.MonkeyPatch):
    save_document_module = importlib.import_module("datahub_agent_context.mcp_tools.save_document")
    monkeypatch.setattr(
        save_document_module,
        "save_document",
        lambda **_kwargs: {
            "success": False,
            "urn": None,
            "message": "document rejected",
        },
    )
    connector = _live_connector(SimpleNamespace())

    with pytest.raises(LiveIntegrationError, match="document rejected"):
        connector.save_document("Resolution", "# Evidence", ["urn:li:dataset:test"])

    assert connector.audit.calls[-1].status == "error"
