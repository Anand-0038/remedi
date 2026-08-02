"""Offline browser regression test for a running Remedi server."""

from pathlib import Path
import shutil

from playwright.sync_api import sync_playwright


BASE_URL = "http://127.0.0.1:8790"
SCREENSHOT_DIR = Path("/tmp/remedi-browser-proof")


def wait_for_status(page, phrase: str) -> None:
    page.locator("#status").get_by_text(phrase, exact=False).wait_for(timeout=20_000)


def main() -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []

    with sync_playwright() as playwright:
        chrome_path = (
            shutil.which("google-chrome")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
        )
        if not chrome_path:
            raise RuntimeError("Chrome or Chromium is required for the optional browser smoke test")
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=chrome_path,
            args=["--no-sandbox"],
        )
        desktop = browser.new_page(viewport={"width": 1440, "height": 1000})
        desktop.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        desktop.goto(BASE_URL)
        desktop.wait_for_load_state("networkidle")

        assert "Fix the incident" in desktop.locator("h1").inner_text()
        assert desktop.locator("#incident-list button").count() >= 5
        assert "Offline verification" in desktop.locator("#context-mode").inner_text()
        health = desktop.request.get(f"{BASE_URL}/api/health").json()
        assert desktop.locator("#product-version").inner_text() == f"Remedi v{health['version']}"
        desktop.screenshot(path=SCREENSHOT_DIR / "01-queue.png", full_page=True)

        # Keep verification deterministic even when earlier local Applies changed
        # context-risk ordering in the ignored fixture state.
        incident = desktop.locator('[data-incident-id="freshness-nyc-taxi"]')
        assert incident.count() == 1
        incident.click()
        desktop.get_by_role("button", name="Propose grounded fix").click()
        wait_for_status(desktop, "Proposed")
        assert desktop.locator("#receipt-state").inner_text() == "Sealed · awaiting approval"
        assert desktop.locator("#receipt-digest").inner_text().startswith("sha256:")
        assert int(desktop.locator("#metric-artifacts").inner_text()) >= 5
        desktop.screenshot(path=SCREENSHOT_DIR / "02-proposal.png", full_page=True)

        desktop.locator("#artifacts button").first.click()
        desktop.locator("#artifact-preview").wait_for(state="visible")
        assert len(desktop.locator("#artifact-preview").inner_text()) > 80

        desktop.on("dialog", lambda dialog: dialog.accept())
        desktop.get_by_role("button", name="Apply sealed plan").click()
        wait_for_status(desktop, "Applied proposal")
        assert desktop.locator("#receipt-state").inner_text() == "Applied · integrity verified"
        desktop.screenshot(path=SCREENSHOT_DIR / "03-applied.png", full_page=True)

        desktop.get_by_role("button", name="Run offline verification").click()
        wait_for_status(desktop, "Offline verification passed")
        assert "pass" in desktop.locator("#meta").inner_text().lower()

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.goto(BASE_URL)
        mobile.wait_for_load_state("networkidle")
        assert mobile.locator("#incident-list button").count() >= 5
        mobile.screenshot(path=SCREENSHOT_DIR / "04-mobile.png", full_page=True)
        browser.close()

    if console_errors:
        raise AssertionError(f"Browser console errors: {console_errors}")
    print(f"Browser smoke PASS · screenshots: {SCREENSHOT_DIR}")


if __name__ == "__main__":
    main()
