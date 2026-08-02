from __future__ import annotations

from remedi.connectors.datahub import DataHubConnector
from remedi.models.incident import Incident


class DetectorAgent:
    """Finds unhealthy assets via DataHub quality assertions + planted incidents."""

    def __init__(self, connector: DataHubConnector) -> None:
        self.connector = connector

    def list(self) -> list[Incident]:
        # Triggers search_quality_assertions + list_incidents under the hood
        return self.connector.list_incidents()

    def get(self, incident_id: str) -> Incident:
        incident = self.connector.get_incident(incident_id)
        # Enrich with entity schema + queries (MCP-shaped get_entities / get_dataset_queries)
        entity = self.connector.get_entity(incident.entity.urn)
        incident.entity = entity
        if not incident.sample_queries:
            incident.sample_queries = self.connector.get_dataset_queries(incident.entity.urn)
        return incident

    def search_related(self, query: str) -> list:
        return self.connector.search(query)
