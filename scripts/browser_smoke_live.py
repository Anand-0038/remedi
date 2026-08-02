"""Capture and verify the live DataHub demo without applying catalog mutations."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from playwright.sync_api import sync_playwright


BASE_URL = os.getenv("REMEDI_BASE_URL", "http://127.0.0.1:8790")
SCREENSHOT_DIR = Path(os.getenv("REMEDI_SCREENSHOT_DIR", "/tmp/remedi-live-proof"))


def main() -> None:
    api_key = os.getenv("REMEDI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Set REMEDI_API_KEY to the key used by the running live server")

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        chrome_path = (
            shutil.which("google-chrome")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
        )
        if not chrome_path:
            raise RuntimeError("Chrome or Chromium is required for the live browser smoke test")
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=chrome_path,
            args=["--no-sandbox"],
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.add_init_script(
            f"window.sessionStorage.setItem('remediApiKey', {json.dumps(api_key)});"
        )
        page = context.new_page()
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")

        assert "Live DataHub GMS · connected" in page.locator("#context-mode").inner_text()
        assert page.locator("#incident-list button").count() >= 1
        assert "DEMO INCIDENT" in page.locator("#incident-list button").first.inner_text()
        page.screenshot(path=SCREENSHOT_DIR / "live-queue.png", full_page=True)

        page.get_by_role("button", name="Propose grounded fix").click()
        page.locator("#status").get_by_text("Proposed", exact=False).wait_for(timeout=30_000)
        assert page.locator("#receipt-state").inner_text() == "Sealed · awaiting approval"
        assert int(page.locator("#metric-artifacts").inner_text()) >= 5
        assert int(page.locator("#metric-impact").inner_text()) > 0
        page.locator(".proof-overview").scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        page.screenshot(path=SCREENSHOT_DIR / "live-proposal.png")

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.add_init_script(
            f"window.sessionStorage.setItem('remediApiKey', {json.dumps(api_key)});"
        )
        mobile.goto(BASE_URL)
        mobile.wait_for_load_state("networkidle")
        assert mobile.locator("#incident-list button").count() >= 1
        mobile.screenshot(path=SCREENSHOT_DIR / "live-mobile.png", full_page=True)
        browser.close()

    if console_errors:
        raise AssertionError(f"Browser console errors: {console_errors}")
    print(f"Live browser smoke PASS · screenshots: {SCREENSHOT_DIR}")
    print("Apply was intentionally not invoked; no catalog write-back occurred.")


if __name__ == "__main__":
    main()
