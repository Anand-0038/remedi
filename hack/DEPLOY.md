# Deploy Notes

Remedi is currently packaged for local and judge/demo environments.

- **Primary flow:** `make serve`
- **Port:** `8790`
- **Config (live mode):**
  - `REMEDI_MODE=live`
  - `DATAHUB_GMS_URL`, `DATAHUB_TOKEN`, `REMEDI_API_KEY`
  - `REMEDI_OPS_WEBHOOK_URL` + `REMEDI_OPS_WEBHOOK_KIND` (optional external delivery)
- **Local mode (judge):** default fixture mode; uses only labeled fixtures from `examples/fixtures`.
