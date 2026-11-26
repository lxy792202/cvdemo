from pathlib import Path
import logging
import time
from datetime import datetime

ROOT = Path(__file__).resolve().parent

# Toast/error copy Baidu's uploader shows when it rejects a file. A rejected
# upload disappears after ~2s, so it must be polled for, not read afterwards.
UPLOAD_ERROR_HINTS = [
    "上传失败", "上传出错", "格式不支持", "不支持的格式", "大小不能超过",
    "超过限制", "请重新上传", "网络异常", "图片太大", "尺寸不符", "失败，请",
]

THUMBNAIL_JS = """
() => {
  const scopes = Array.from(document.querySelectorAll(
    '[role="dialog"], .vi-dialog, .dls-dialog, [class*="dialog"], '
    + '[class*="upload"], [class*="Upload"], [class*="preview"], [class*="picture"]'
  ));
  const pool = scopes.length ? scopes : [document.body];
  const seen = new Set();
  const usable = (src) => /^(https?:|blob:|data:image)/.test(src || '')
    && !/logo|icon|avatar|sprite/i.test(src || '');
  let n = 0;
  for (const scope of pool) {
    for (const img of scope.querySelectorAll('img')) {
      if (seen.has(img)) continue;
      seen.add(img);
      const r = img.getBoundingClientRect();
      if (r.width < 8 || r.height < 8) continue;
      if (!usable(img.currentSrc || img.src || '')) continue;
      n++;
    }
  }
  // Some uploaders render the result as a CSS background instead of an <img>.
  for (const scope of pool) {
    for (const el of scope.querySelectorAll('div, span, i, a')) {
      if (seen.has(el)) continue;
      const bg = getComputedStyle(el).backgroundImage || '';
      const m = bg.match(/url\\(["']?(.*?)["']?\\)/);
      if (!m || !usable(m[1])) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 24 || r.height < 24) continue;
      seen.add(el);
      n++;
    }
  }
  return n;
}
"""

TOAST_SELECTORS = [
    '[class*="toast"]', '[class*="Toast"]', '[role="alert"]',
    '[class*="message"]', '[class*="notice"]', '[class*="tip"]',
]

def setup_logging(config):
    log_path = ROOT / config["log_file"]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()]
    )
    return logging.getLogger("baidu_auto")

def screenshot(page, config, name="failure"):
    folder = ROOT / config["screenshot_dir"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return None

def wait_visible(locator, timeout=15000):
    locator.wait_for(state="visible", timeout=timeout)
    return locator

def safe_close_known_popups(page, logger=None):
    """Close only known, explicitly listed popup buttons; don't dismiss unknown dialogs.

    Happy-path only: the labels below are purely informational, so dismissing
    them can never abort an in-progress upload. Broader dismissals (取消/关闭)
    live in recovery.py and are only used on the failure path.
    """
    for label in ["知道了", "稍后再说", "跳过"]:
        try:
            loc = page.get_by_role("button", name=label, exact=True)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=1500)
                if logger:
                    logger.info("关闭已知弹窗：%s", label)
        except Exception:
            pass
    return []

def upload_local_file(page, image_path, timeout=15000):
    image_path = str(Path(image_path).resolve())
    file_inputs = page.locator('input[type="file"]')
    if file_inputs.count() == 0:
        raise RuntimeError("页面未找到 input[type=file]，请检查上传按钮是否打开上传窗口")
    file_inputs.first.set_input_files(image_path, timeout=timeout)


def count_thumbnails(page):
    try:
        return int(page.evaluate(THUMBNAIL_JS))
    except Exception:
        return 0


def find_upload_error(page):
    for selector in TOAST_SELECTORS:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 8)):
                el = loc.nth(i)
                if not el.is_visible():
                    continue
                text = (el.inner_text(timeout=500) or "").strip()[:200]
                if any(hint in text for hint in UPLOAD_ERROR_HINTS):
                    return text
        except Exception:
            continue
    return ""


def wait_upload_confirmed(page, before=0, timeout=30000, logger=None):
    """Block until the uploaded image is provably present, or raise.

    Choosing a file and clicking confirm is NOT proof of success: the platform
    can reject the file (format/size/quota) with a toast that vanishes in ~2s,
    leaving the form imageless. Callers that skip this check only discover the
    problem at submit time, or silently publish a product with no picture.
    """
    deadline = time.monotonic() + timeout / 1000.0
    while time.monotonic() < deadline:
        hint = find_upload_error(page)
        if hint:
            raise RuntimeError(f"上传被平台拒绝：{hint}")
        now = count_thumbnails(page)
        if now > before:
            if logger:
                logger.info("上传已确认：缩略图 %s -> %s", before, now)
            return now
        page.wait_for_timeout(400)
    raise RuntimeError(f"等待上传结果超时（{timeout}ms）：缩略图仍为 {count_thumbnails(page)}，未超过 {before}")
