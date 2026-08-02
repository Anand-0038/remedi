# Judge Walkthrough

## 3–5 minute scoring path

1. Open `README.md` and confirm challenge fit and architecture summary.
2. Follow `hack/architecture.md` to verify the `Detect → Trace → Propose → Seal → Apply` control flow.
3. Run:
   ```bash
   make verify-local
   ```
   This runs pytest + fixture selftest and confirms:
   - no fallback to fixture/replay on live failures,
   - proposal sealing + exact-run apply,
   - no direct apply path, and
   - integrity checks for proposal replay.
4. Launch UI:
   ```bash
   make serve
   ```
   and open `http://localhost:8790`.
5. In the UI: select an incident, click **Propose grounded fix**, confirm blast-radius graph renders,
   then confirm **Apply sealed plan** becomes active.
6. Open `/api/selftest` and confirm all checks pass in fixture mode.
7. Confirm README-declared constraints:
   - fixture mode uses labeled deterministic data only,
   - live mode requires DataHub + API key,
   - proposal digest is required for apply.

## Evidence checklist

- [ ] `git log` shows clean, reproducible source tree in this repo root.
- [ ] Proposal generation is proposal-first (`/api/run`) and immutable-sealed (`/api/apply` with run_id).
- [ ] `datahub` writes are never executed without fixture/liveness boundary checks.
- [ ] Generated PR-like outputs are reviewable and tied to incident evidence.
- [ ] Judge can repeat the flow in ≤5 minutes with public commands.
