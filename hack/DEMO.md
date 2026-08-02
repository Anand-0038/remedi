# Demo Script

1. Install dev extras (first run only):
   ```bash
   uv sync --extra dev
   ```
2. Verify local path:
   ```bash
   make verify-local
   ```
3. Start service:
   ```bash
   make serve
   ```
4. Open `http://localhost:8790`.
5. In the UI, inspect queue, open an incident, and run:
   - **Propose grounded fix**
   - **Apply sealed plan**
   - **Run judge proof**
6. Capture evidence:
   ```bash
   uv run --with playwright python scripts/browser_smoke.py
   ```
   (outputs screenshots under `/tmp/remedi-browser-proof`)
