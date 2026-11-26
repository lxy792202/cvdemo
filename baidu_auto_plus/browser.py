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


def pick_target_page(context, logger=None):
    """Return the tab the user actually navigated into.

    基木鱼 opens in NEW tabs (the Codegen recording is full of expect_popup), so
    context.pages[0] stays on the www2.baidu.com login dashboard while the user
    works in a later tab. Operating on pages[0] makes every locator miss.
    Prefer the newest tab; log its URL so a wrong pick is obvious.
    """
    pages = context.pages
    if not pages:
        return context.new_page()
    target = pages[-1]
    try:
        if logger:
            logger.info("操作目标标签页：%s（共 %s 个标签）", target.url, len(pages))
    except Exception:
        pass
    return target
