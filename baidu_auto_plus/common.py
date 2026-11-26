from pathlib import Path
import logging
import time
from datetime import datetime

ROOT = Path(__file__).resolve().parent

# Toast/error copy Baidu's uploader shows when it rejects a file. A rejected
# upload disappears after ~2s, so it must be polled for, not read afterwards.
UPLOAD_ERROR_HINTS = [
    # 只留强错误词：表单静态说明里也有「大小不能超过2MB」这类文案，
    # 弱词会把帮助文本误判成上传失败。
    "上传失败", "上传出错", "处理失败", "无法提交", "格式不支持", "不支持的格式",
    "请重新上传", "网络异常", "图片太大", "尺寸不符", "失败，请",
    "导入失败", "解析失败", "文件损坏", "psd解析", "PSD解析",
]

THUMBNAIL_JS = """
() => {
  // 只统计表单里的槽位缩略图；图片库/上传/预览等弹层里的 img 一律排除，
  // 否则弹层网格的十几张缩略图会造成「上传已确认」假阳性。
  const inOverlay = e => !!e.closest(
    '[role="dialog"], veui-x-overlay, [class*="veui-overlay"], '
    + '[class*="cc-creative-sdk-veui-dialog"], [class*="cc-creative-overlay"]'
  );
  const usable = (src) => /^(https?:|blob:|data:image)/.test(src || '')
    && !/logo|icon|avatar|sprite/i.test(src || '');
  const srcs = [];
  for (const img of document.querySelectorAll('img')) {
    if (inOverlay(img)) continue;
    const src = img.currentSrc || img.src;
    if (usable(src) && !srcs.includes(src)) srcs.push(src);
  }
  return srcs;
}
"""

TOAST_SELECTORS = [
    # 只认真正的瞬态提示容器；tip/message 类选择器会命中表单静态帮助文案。
    '[class*="toast"]', '[class*="Toast"]', '[role="alert"]',
    '[class*="notice"]', '.cc-creative-sdk-veui-alert-box-error',
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


def resolve_path(value, base):
    """Accept an absolute path, or a path relative to `base`."""
    path = Path(value)
    return path if path.is_absolute() else Path(base) / path

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
    # The recording clicks 本地上传 before the file chooser appears; the raw
    # input[type=file] may not be actionable until then.
    try:
        local = page.get_by_role("button", name="本地上传", exact=True)
        if local.count() and local.first.is_visible():
            # 本地上传 opens the OS-native file picker; intercept it instead of
            # leaving a modal chooser stranded on the user's desktop.
            with page.expect_file_chooser(timeout=timeout) as fc_info:
                local.first.click(timeout=timeout)
            fc_info.value.set_files(image_path)
            return
    except Exception:
        pass
    file_inputs = page.locator('input[type="file"]')
    if file_inputs.count() == 0:
        raise RuntimeError("页面未找到 input[type=file]，请检查上传按钮是否打开上传窗口")
    file_inputs.first.set_input_files(image_path, timeout=timeout)


def image_signature(page):
    """Sorted tuple of uploaded-image srcs. Changes on BOTH add and replace,
    unlike a plain count which stays flat when an existing image is replaced."""
    try:
        return tuple(sorted(page.evaluate(THUMBNAIL_JS)))
    except Exception:
        return ()


def count_thumbnails(page):
    return len(image_signature(page))


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


def wait_upload_confirmed(page, before=None, before_sig=None, timeout=30000, logger=None):
    """Block until the uploaded image is provably present, or raise.

    Choosing a file and clicking confirm is NOT proof of success: the platform
    can reject the file (format/size/quota) with a toast that vanishes in ~2s,
    leaving the form imageless. Callers that skip this check only discover the
    problem at submit time, or silently publish a product with no picture.

    Pass before_sig (preferred) to detect both adds and replaces; pass before
    (a count) only for flows that strictly append new images.
    """
    if before_sig is None and before is None:
        before_sig = image_signature(page)
    deadline = time.monotonic() + timeout / 1000.0
    while time.monotonic() < deadline:
        hint = find_upload_error(page)
        if hint:
            raise RuntimeError(f"上传被平台拒绝：{hint}")
        if before_sig is not None:
            now_sig = image_signature(page)
            if now_sig != before_sig:
                if logger:
                    added = [s for s in now_sig if s not in before_sig]
                    removed = [s for s in before_sig if s not in now_sig]
                    logger.info("上传已确认：新增 %s 张，替换 %s 张", len(added), len(removed))
                return now_sig
        else:
            now = count_thumbnails(page)
            if now > before:
                if logger:
                    logger.info("上传已确认：缩略图 %s -> %s", before, now)
                return now
        page.wait_for_timeout(400)
    if before_sig is not None:
        raise RuntimeError(
            f"等待上传结果超时（{timeout}ms）：图片 src 集合未变化，上传可能未生效或被静默拒绝"
        )
    raise RuntimeError(f"等待上传结果超时（{timeout}ms）：缩略图仍为 {count_thumbnails(page)}，未超过 {before}")
