from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent

def launch_browser(config):
    """Launch persistent Chromium profile; user can log in manually once."""
    pw = sync_playwright().start()
    profile = ROOT / config["profile_dir"]
    profile.mkdir(parents=True, exist_ok=True)
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=config.get("headless", False),
        viewport={"width": 1440, "height": 1000},
        accept_downloads=True
    )
    page = context.pages[0] if context.pages else context.new_page()
    return pw, context, page
