# Remedi Architecture

```mermaid
flowchart LR
  A[DataHub / fixtures] -->|quality assertions + metadata| B[Connector]
  B --> C[Incident Adapter]
  C --> D[Lineage Tracer]
  D --> E[Codegen Agents]
  E --> F[Proposed Bundle + PR_DESCRIPTION]
  F --> G[Proposal Store]
  G --> H[Seal + Digest]
  H --> I[Reviewer / Operator Approval]
  I -->|/api/apply run_id| J[Writer]
  J --> K[(Catalog write-back / artifacts)]
  K --> L[Receipt + Audit trail]
  J --> M[Selftest & Smoke]
  M --> N[Judge / CI evidence]
```

## Data flow details

- **Input split by mode:** fixture catalog (`examples/fixtures`) for judge reproducibility; live mode via DataHub + MCP/SDK.
- **Core control plane:** the orchestrator enforces propose-first behavior and writes only with sealed plans.
- **Safety boundary:** no local/fixture fallback in live paths and no blind re-generation on apply.
- **Output:** review-ready artifacts (dbt / SQL / Airflow / Dagster / Prefect), proposal receipts, and catalog write-back actions.
