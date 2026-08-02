#!/usr/bin/env python3
"""Seed one disclosed failing assertion into a local DataHub OSS instance."""

from __future__ import annotations

import argparse
import os
from urllib.parse import urlparse

import httpx

ASSERTION_URN = "urn:li:assertion:remedi-showcase-orders-freshness"
DATASET_URN = (
    "urn:li:dataset:(urn:li:dataPlatform:dbt,b2fd91.ORDER_ENTRY_DB.analytics.order_details,PROD)"
)

UPSERT_ASSERTION = """
mutation SeedRemediDemo($urn: String, $input: UpsertCustomAssertionInput!) {
  upsertCustomAssertion(urn: $urn, input: $input) {
    urn
    info {
      type
      description
      customAssertion { entityUrn logic }
    }
  }
}
"""

REPORT_FAILURE = """
mutation ReportRemediDemo($urn: String!, $result: AssertionResultInput!) {
  reportAssertionResult(urn: $urn, result: $result)
}
"""

VERIFY_ASSERTION = """
query VerifyRemediDemo($urn: String!) {
  assertion(urn: $urn) {
    urn
    info { description customAssertion { entityUrn } }
    runEvents(limit: 1) {
      runEvents { result { type severity } }
    }
  }
}
"""


def graphql(client: httpx.Client, query: str, variables: dict[str, object]) -> dict:
    response = client.post("/api/graphql", json={"query": query, "variables": variables})
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        messages = "; ".join(str(error.get("message", error)) for error in payload["errors"])
        raise RuntimeError(f"DataHub GraphQL rejected the request: {messages}")
    return payload.get("data") or {}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed a disclosed synthetic failure into a local DataHub showcase graph."
    )
    parser.add_argument(
        "--gms-url",
        default=os.getenv("DATAHUB_GMS_URL", "http://localhost:8080"),
        help="DataHub GMS base URL (default: DATAHUB_GMS_URL or http://localhost:8080)",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow mutation of a non-local DataHub host. Use only on an intentional demo tenant.",
    )
    args = parser.parse_args()

    host = (urlparse(args.gms_url).hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1"} and not args.allow_remote:
        raise SystemExit(
            "Refusing to seed a non-local DataHub deployment. Pass --allow-remote only for an "
            "intentional demo tenant."
        )

    headers = {"Content-Type": "application/json"}
    token = os.getenv("DATAHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with httpx.Client(base_url=args.gms_url.rstrip("/"), headers=headers, timeout=20) as client:
        entity = graphql(
            client,
            "query Target($urn: String!) { dataset(urn: $urn) { urn } }",
            {"urn": DATASET_URN},
        ).get("dataset")
        if not entity:
            raise SystemExit(
                "The showcase target dataset is missing. Load `showcase-ecommerce` first."
            )

        graphql(
            client,
            UPSERT_ASSERTION,
            {
                "urn": ASSERTION_URN,
                "input": {
                    "entityUrn": DATASET_URN,
                    "type": "Remedi demo freshness monitor",
                    "description": (
                        "DEMO INCIDENT — Order Details freshness guard failed in the local "
                        "DataHub showcase graph. Seeded by Remedi for live integration "
                        "verification; not a production outage."
                    ),
                    "platform": {"name": "Remedi demo monitor"},
                    "externalUrl": "http://localhost:8790",
                    "logic": (
                        "DEMO CHECK: updated_at must be within the expected daily refresh "
                        "window. This assertion is synthetic test evidence in DataHub OSS."
                    ),
                },
            },
        )
        graphql(
            client,
            REPORT_FAILURE,
            {
                "urn": ASSERTION_URN,
                "result": {
                    "type": "FAILURE",
                    "severity": "HIGH",
                    "properties": [
                        {"key": "evidence_scope", "value": "synthetic local integration demo"},
                        {"key": "expected_freshness", "value": "within 24 hours"},
                        {"key": "observed_freshness", "value": "outside demo threshold"},
                    ],
                },
            },
        )
        assertion = graphql(client, VERIFY_ASSERTION, {"urn": ASSERTION_URN}).get("assertion")

    latest = (((assertion or {}).get("runEvents") or {}).get("runEvents") or [{}])[0]
    result = latest.get("result") or {}
    if not assertion or result.get("type") != "FAILURE":
        raise SystemExit("DataHub did not return the expected failing assertion after seeding.")
    print(f"Seeded and verified in DataHub: {ASSERTION_URN}")
    print(f"Target dataset: {DATASET_URN}")
    print("Evidence scope: synthetic local integration demo; not a production outage")


if __name__ == "__main__":
    main()
