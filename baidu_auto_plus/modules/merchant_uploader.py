"""商户信息（商家管理 -> 商户信息 -> 编辑）自动填写与图片上传。

Selectors are transcribed from the 2026-11 Codegen recording. Several of them are
SVG-path deep and WILL break when Baidu ships a new UI, so every one of them is
overridable from config.json["merchant_selectors"] / ["merchant_slots"] instead
of editing this file.

Deliberately not automated:
  * 商家名称 / 地区变更 -- the recording changed 保定市 to 沧州市, which looks
    like a manual test rather than a requirement. Both run only when explicitly
    configured, so the script never rewrites business identity data on a guess.
"""

from pathlib import Path
import json
import re
import time

from common import (
    image_signature,
    resolve_path,
    safe_close_known_popups,
    wait_upload_confirmed,
)
from state_manager import load_state, set_task
from agent_handler import AgentExceptionHandler

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

DEFAULT_SELECTORS = {
    "menu_merchant": "商家管理",
    "menu_merchant_info": "商户信息",
    "edit_button": "编辑",
    "intro_placeholder": "请输入商家简介，1-1000个字符",
    "name_placeholder": "请输入商家名称，最少4个字符，最多60个字符",
    "address_placeholder": "请输入联系地址",
    "phone_placeholder": "请输入联系电话",
    "contact_placeholder": "如：马先生，最多10个字符",
    "next_button": "下一步",
    "submit_button": "提交审核",
    "local_upload_button": "本地上传",
}

# Image slots in form order. 每个槽用行容器圈定范围（标签+上传区在同一行内），
# 避免全局 .first / following 轴命中相邻槽；cap 用于校验打开的选择器是否本槽。
DEFAULT_SLOTS = {
    "logo": {
        "row_css": "div.sdk-img-picker-rows-item-main",
        "row_label": "自定义图",
        "cap": 1,
        "note": "商家logo/自定义图（2:1~5:1、<=2M）",
    },
    "gallery": {
        "row_css": "div.sdk-img-picker-rows-item-main",
        "row_label": "1:1图",
        "cap": 1,
        "note": "1:1图（156-256px、<=120K）",
    },
    "main": {
        "row_css": ".inner-picture",
        "row_label": "",
        "cap": 10,
        "note": "商家图片（最多10张、>=790x444、<=2M）",
    },
}

SUCCESS_TEXTS = ["提交成功", "审核中", "已提交审核", "审核提交成功", "保存成功"]


def _resolve_image(value):
    """Accept an absolute path, or a path relative to the project data dir."""
    return resolve_path(value, DATA_DIR)


def _js_click(element, logger, what):
    """SVG 元素没有 HTMLElement.click()，必须用 dispatchEvent 派发点击。"""
    element.evaluate(
        "el => el.dispatchEvent(new MouseEvent('click', "
        "{bubbles: true, cancelable: true, view: window}))"
    )
    logger.info("已 JS 点击 %s", what)


def _picker_open(page):
    """图片库网页选择器是否开着：只有它里面有「本地上传」按钮。"""
    btn = page.get_by_role("button", name="本地上传", exact=True)
    try:
        return bool(btn.count()) and btn.first.is_visible()
    except Exception:
        return False


def _wait_picker(page, timeout):
    deadline = time.monotonic() + timeout / 1000.0
    while time.monotonic() < deadline:
        if _picker_open(page):
            return True
        page.wait_for_timeout(300)
    return False


def _dismiss_stray_overlay(page):
    """点错图标（如预览）会盖一层没有「本地上传」的遮罩，Esc 退掉再试下一个入口。"""
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)


def _picker_capacity(page):
    """图片库左栏「已选图片(x/y)」的 y，用来确认开的是不是本槽的选择器。"""
    dlg = page.get_by_role("dialog").filter(has_text="图片库")
    try:
        txt = dlg.get_by_text("已选图片", exact=False).first.inner_text(timeout=2000)
    except Exception:
        return None
    m = re.search(r"\((\d+)\s*/\s*(\d+)\)", txt)
    return int(m.group(2)) if m else None


def _close_picker(page):
    dlg = page.get_by_role("dialog").filter(has_text="图片库")
    cancel = dlg.get_by_role("button", name="取消", exact=True)
    try:
        if cancel.count() and cancel.first.is_visible():
            cancel.first.click(timeout=3000)
            page.wait_for_timeout(500)
    except Exception:
        pass


def _slot_row(page, slot):
    row = page.locator(slot.get("row_css", "div.sdk-img-picker-rows-item-main"))
    label = slot.get("row_label")
    if label:
        row = row.filter(has=page.get_by_text(label, exact=True))
    return row.first


def _picker_matches(page, want_cap, timeout):
    """等图片库选择器打开，并校验「已选图片(x/y)」的 y 是否本槽容量。"""
    if not _wait_picker(page, timeout):
        return False
    cap = _picker_capacity(page)
    if want_cap is None or cap == want_cap:
        return True
    _close_picker(page)
    return False


def _open_picker_for_slot(page, slot_key, slot, timeout, logger):
    """打开本槽的「图片库」网页选择器（只用具实测安全的入口）。

    空槽/可追加槽点行内加号上传位；已有图点悬浮工具条第 2 个图标（实测=替换，
    第 3 个图标疑似删除、第 1 个疑似预览，一律不碰）。
    """
    row = _slot_row(page, slot)
    if not row.count():
        raise RuntimeError(
            f"{slot_key}：找不到槽位行容器（row_css={slot.get('row_css')}，"
            f"label={slot.get('row_label')}）；请在 config.json 的 merchant_slots 更新"
        )
    want_cap = slot.get("cap")

    add = row.locator("div.vi-upload-add-cross")
    if add.count() and add.first.is_visible():
        add.first.scroll_into_view_if_needed(timeout=timeout)
        add.first.click(timeout=timeout)
        if _picker_matches(page, want_cap, 4000):
            logger.info("%s：加号上传位已打开本槽图片库选择器", slot_key)
            return
        _close_picker(page)
        _dismiss_stray_overlay(page)

    ops = row.locator(".vi-image-upload-anchor-operation-container")
    if ops.count():
        span = ops.first.locator("span:nth-child(2)")
        if span.count():
            try:
                row.locator(".vi-image-upload-anchor").first.hover(timeout=3000)
                span.first.click(timeout=3000)
            except Exception as exc:
                logger.info("%s：工具条替换图标点击失败：%s", slot_key, str(exc).splitlines()[0])
            if _picker_matches(page, want_cap, 3000):
                logger.info("%s：工具条替换图标打开本槽图片库选择器", slot_key)
                return
            _close_picker(page)
            _dismiss_stray_overlay(page)
    raise RuntimeError(
        f"{slot_key}：加号上传位和工具条替换图标都试过了，仍打不开本槽图片库选择器；"
        f"请在 config.json 的 merchant_slots.{slot_key} 更新 row_css/row_label"
    )


def _upload_via_picker(page, slot_key, resolved, timeout, logger):
    """图片库选择器 -> 本地上传（macOS Finder，被 expect_file_chooser 拦截）
    -> 顶层「上传图片」确认弹窗 -> 图片库「确定」。"""
    btn = _first_visible(page.get_by_role("button", name="本地上传", exact=True))
    if btn is None:
        raise RuntimeError(f"{slot_key}：图片库选择器里没有可见的「本地上传」按钮")
    with page.expect_file_chooser(timeout=timeout) as fc_info:
        btn.click(timeout=timeout)
    fc_info.value.set_files(str(resolved))
    logger.info("%s：已拦截系统文件选择器并设置 %s", slot_key, resolved.name)
    # 平台服务端要先上传+转码；人工操作每步间隔数秒所以不会踩，
    # 自动化连点两层确定会赶上「1张图片处理失败」。这里等处理完成。
    page.wait_for_timeout(6000)

    # 顶层弹窗锚点用「图片格式：」：图片库弹窗文案含「本地上传+图片工具」，
    # 会误包含「上传图片」子串，不能用它做 filter。
    upload_dlg = page.get_by_role("dialog").filter(has_text="图片格式：")
    confirm = upload_dlg.get_by_role("button", name="确定", exact=True).first
    try:
        confirm.click(timeout=10000)
    except Exception as exc:
        logger.info("%s：直接点「上传图片」确定失败（%s），改先选中图片瓦片", slot_key, exc)
        tile = upload_dlg.locator(
            ".cc-creative-image-fill-image-item, .cc-creative-image-dimension-item"
        ).first
        tile.click(timeout=5000)
        confirm.click(timeout=10000)
    logger.info("%s：已确认「上传图片」弹窗", slot_key)
    # 两层确定之间必须等平台加载/转码完刚上传的图，人工操作天然有这段间隔
    page.wait_for_timeout(10000)

    picker_dlg = page.get_by_role("dialog").filter(has_text="已选图片")
    expected = ""
    try:
        style = picker_dlg.locator(".cc-picker-fill-boxes .cc-image").first.get_attribute(
            "style", timeout=3000
        ) or ""
        murl = re.search(r'url\(["\']?(.*?)["\']?\)', style)
        if murl:
            expected = murl.group(1)
    except Exception as exc:
        logger.info("%s：读取已选瓦片 src 失败：%s", slot_key, str(exc).splitlines()[0])
    pconfirm = picker_dlg.get_by_role("button", name="确定", exact=True)
    if pconfirm.count() and pconfirm.first.is_visible():
        pconfirm.first.click(timeout=timeout)
        logger.info("%s：已确认图片库选择器", slot_key)

    # 平台校验失败会弹红色 alert（约 2s 自动消失）且图片库不关闭；
    # 轮询抓原文写日志，收拾现场后干净失败，避免假阳性往下走。
    alert = page.locator(".cc-creative-sdk-veui-alert-box-error")
    deadline = time.monotonic() + 25
    err_text = ""
    while time.monotonic() < deadline:
        try:
            if alert.count() and alert.first.is_visible():
                err_text = (alert.first.inner_text(timeout=1000) or "").strip()
                if err_text:
                    break
        except Exception:
            pass
        if not _picker_open(page):
            break
        page.wait_for_timeout(300)
    if err_text:
        try:
            alert.first.get_by_role("button", name="确定", exact=True).first.click(timeout=2000)
        except Exception:
            page.keyboard.press("Escape")
        _close_picker(page)
        raise RuntimeError(f"{slot_key}：平台拒绝本次上传：{err_text}")
    if _picker_open(page):
        _close_picker(page)
        raise RuntimeError(f"{slot_key}：点确定后图片库选择器未关闭，上传未生效")
    return expected


def _capture_alert(page, seconds):
    """抓平台红色错误弹窗原文（会自动消失），返回文本或空串。"""
    alert = page.locator(".cc-creative-sdk-veui-alert-box-error")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if alert.count() and alert.first.is_visible():
                text = (alert.first.inner_text(timeout=1000) or "").strip()
                if text:
                    return text
        except Exception:
            pass
        page.wait_for_timeout(250)
    return ""


def _upload_slot_once(page, slot_key, slot, resolved, timeout, logger):
    before_sig = image_signature(page)
    row = _slot_row(page, slot)
    if not row.count():
        raise RuntimeError(
            f"{slot_key}：找不到槽位行容器（row_css={slot.get('row_css')}，"
            f"label={slot.get('row_label')}）；请在 config.json 的 merchant_slots 更新"
        )

    done = False
    file_input = row.locator('input[type="file"]')
    if file_input.count():
        try:
            file_input.first.set_input_files(str(resolved), timeout=timeout)
            logger.info("%s：槽位隐藏 input[type=file] 直接设置文件成功", slot_key)
            done = True
        except Exception as exc:
            logger.info("%s：隐藏 input 设置失败，改走图片库：%s",
                        slot_key, str(exc).splitlines()[0])

    expected = ""
    if not done:
        _open_picker_for_slot(page, slot_key, slot, timeout, logger)
        expected = _upload_via_picker(page, slot_key, resolved, timeout, logger) or ""

    # 服务端处理图片需要时间（人工经验约 10s）；轮询红框的同时把等待覆盖掉
    err_text = _capture_alert(page, 10)
    if err_text:
        try:
            page.get_by_role("button", name="知道了", exact=True).first.click(timeout=2000)
        except Exception:
            pass
        _close_picker(page)
        raise RuntimeError(f"{slot_key}：平台拒绝本次上传：{err_text}")
    _wait_slot_image(page, row, before_sig, expected, max(timeout, 30000), logger)


def _upload_slot(page, slot_key, slot, image_path, timeout, logger,
                 skip_existing=False):
    """上传一个图片位；平台偶发处理失败时收拾现场重试一次。

    skip_existing：槽位里已有平台图片时跳过重复上传。平台图床对同内容
    重传会报「1张图片处理失败」，幂等重跑（--rerun-success）必须跳过。
    """
    resolved = _resolve_image(image_path)
    if not resolved.exists():
        raise RuntimeError(f"图片位 {slot_key} 的文件不存在：{resolved}")
    if skip_existing:
        row = _slot_row(page, slot)
        if row.count():
            srcs = [
                row.locator("img").nth(i).get_attribute("src") or ""
                for i in range(row.locator("img").count())
            ]
            srcs = [x for x in srcs if x.startswith("http")]
            if srcs:
                logger.info("%s：槽位已有平台图片（%s 张），幂等重跑跳过重复上传",
                            slot_key, len(srcs))
                return
    logger.info("上传商户图片位 %s：%s", slot_key, resolved.name)
    last_exc = None
    for attempt in (1, 2):
        try:
            _upload_slot_once(page, slot_key, slot, resolved, timeout, logger)
            return
        except Exception as exc:
            last_exc = exc
            logger.warning("%s：第 %s 次上传失败：%s", slot_key, attempt, str(exc).splitlines()[0])
            if attempt == 2:
                break
            safe_close_known_popups(page, logger)
            _close_picker(page)
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
    raise last_exc


def _wait_slot_image(page, row, before_sig, expected_src, timeout, logger):
    """上传生效判定：src 签名变化，或槽内图已是期望 src。

    平台图床按内容去重：重复上传同一文件会返回同一个 src，签名不变，
    所以不能只靠「变化」判定；期望 src 取自图片库左栏已选瓦片。
    """
    key = expected_src.rsplit("/", 1)[-1] if expected_src else ""
    deadline = time.monotonic() + timeout / 1000.0
    while time.monotonic() < deadline:
        if image_signature(page) != before_sig:
            logger.info("上传已确认：槽位图片 src 签名变化")
            return
        if key:
            srcs = [
                row.locator("img").nth(i).get_attribute("src") or ""
                for i in range(row.locator("img").count())
            ]
            if any(key in s for s in srcs):
                logger.info("上传已确认：槽位图片即期望 src（同内容重传 src 不变）")
                return
        page.wait_for_timeout(400)
    if not expected_src and row.locator("img").count():
        logger.info("上传已确认（兜底）：槽位已有图且平台未报错")
        return
    raise RuntimeError("等待上传结果超时：槽位图片未变成期望图片，上传可能未生效")


def _fill_optional_textbox(page, placeholder, value, field_name, timeout, logger):
    """Fill only when a value is configured; never invent business data."""
    if not value:
        logger.info("商户字段 %s 未配置，跳过（不猜测填写）", field_name)
        return False
    box = page.get_by_role("textbox", name=placeholder)
    if not box.count():
        logger.warning("商户字段 %s 的输入框找不到（placeholder=%s），跳过", field_name, placeholder)
        return False
    box.first.click(timeout=timeout)
    box.first.fill(str(value), timeout=timeout)
    logger.info("已填写商户字段 %s", field_name)
    return True


def _change_region(page, region, timeout, logger):
    """Two-step cascading picker: click the current region chip, then the city.

    录制里的 nth 只在当时那版 DOM 成立；找不到城市就 Esc 收掉下拉换下一个
    候选 nth，避免静默跳过留下旧地区。
    """
    current = region.get("current_text")
    city = region.get("city")
    if not current or not city:
        logger.info("未配置 region.current_text / region.city，跳过地区变更")
        return False
    pattern = re.compile(r"^%s$" % re.escape(current))
    chip = page.locator("div").filter(has_text=pattern)
    total = chip.count()
    if not total:
        logger.warning("地区控件找不到「%s」，跳过地区变更", current)
        return False
    prefer = int(region.get("nth", 4))
    order = [prefer] + [i for i in range(total) if i != prefer]
    for nth in order:
        try:
            chip.nth(nth).scroll_into_view_if_needed(timeout=timeout)
            chip.nth(nth).click(timeout=timeout)
        except Exception as exc:
            logger.info("地区 chip nth=%s 点击失败：%s", nth, str(exc).splitlines()[0])
            continue
        page.wait_for_timeout(600)
        target = page.get_by_text(city, exact=True)
        if target.count() and target.first.is_visible():
            target.first.click(timeout=timeout)
            logger.info("地区已切换：%s -> %s（nth=%s）", current, city, nth)
            return True
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    raise RuntimeError(f"地区下拉里找不到城市：{city}（已尝试 {len(order)} 个 chip 候选）")


def _has_edit(page, sel):
    try:
        return bool(page.get_by_role("button", name=sel["edit_button"], exact=True).count())
    except Exception:
        return False


def _first_visible(locator):
    """营销首页同一个文案常有多份节点（折叠菜单里藏着一份），.first 会命中隐藏的那个。"""
    for i in range(locator.count()):
        element = locator.nth(i)
        try:
            if element.is_visible():
                return element
        except Exception:
            continue
    return None


def _click_first_visible(locator, timeout, logger, what):
    element = _first_visible(locator)
    if element is None:
        raise RuntimeError(f"「{what}」在 DOM 里存在但全部不可见（可能在折叠菜单里），无法点击")
    element.scroll_into_view_if_needed(timeout=timeout)
    element.click(timeout=timeout)
    logger.info("已点击「%s」", what)
    return element


def _adopt_newest_page(context, before, logger, step):
    """基木鱼 的菜单经常开新标签；标签数增加就切到最新那个。"""
    pages = context.pages
    if len(pages) > before:
        newest = pages[-1]
        try:
            newest.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        logger.info("%s：检测到新标签，切换到 %s", step, newest.url)
        return newest
    return None


def _ensure_on_merchant_page(page, sel, timeout, logger):
    """从任意位置导航到 商户信息 页，返回真正承载该页的 page 对象。

    这样就不依赖用户事先手动点菜单 —— 上一轮失败的根因正是脚本在用户
    导航之前就跑了，操作的是还停在营销首页的标签页。
    """
    context = page.context
    if _has_edit(page, sel):
        logger.info("当前标签页已是商户信息页：%s", page.url)
        return page

    target = page
    jimuyu = target.get_by_text("基木鱼", exact=True)
    if jimuyu.count():
        before = len(context.pages)
        _click_first_visible(jimuyu, timeout, logger, "基木鱼")
        target.wait_for_timeout(2500)
        target = _adopt_newest_page(context, before, logger, "进入基木鱼") or target
        try:
            guide = target.locator(".index__step-close-14SC3")
            if guide.count() and guide.first.is_visible():
                guide.first.click(timeout=2000)
                logger.info("关闭基木鱼引导层")
        except Exception:
            pass
        safe_close_known_popups(target, logger)

    for label in (sel["menu_merchant"], sel["menu_merchant_info"]):
        loc = target.get_by_text(label, exact=True)
        element = _first_visible(loc)
        if element is None:
            logger.info("菜单「%s」在当前页不存在或不可见，跳过", label)
            continue
        before = len(context.pages)
        element.scroll_into_view_if_needed(timeout=timeout)
        element.click(timeout=timeout)
        logger.info("已点击菜单「%s」", label)
        target.wait_for_timeout(2000)
        target = _adopt_newest_page(context, before, logger, f"菜单{label}") or target
        safe_close_known_popups(target, logger)

    if not _has_edit(target, sel):
        urls = [p.url for p in context.pages]
        raise RuntimeError(
            f"导航后仍找不到「{sel['edit_button']}」按钮。当前所有标签页：{urls}；"
            f"请确认账号已开通基木鱼商户信息功能"
        )
    logger.info("已到达商户信息页：%s", target.url)
    return target


def run_merchant_task(page, config, logger):
    data_file = DATA_DIR / "company.json"
    if not data_file.exists():
        logger.warning("缺少商户资料文件：%s", data_file)
        return {"status": "not_configured", "detail": "先准备 data/company.json"}
    data = json.loads(data_file.read_text(encoding="utf-8"))

    sel = {**DEFAULT_SELECTORS, **config.get("merchant_selectors", {})}
    slots = {**DEFAULT_SLOTS, **config.get("merchant_slots", {})}
    timeout = config.get("wait_timeout", 15000)
    company = data.get("company_name", "(未填写名称)")
    submitted = False

    state = load_state(config)
    key = f"merchant::{company}"
    previous = state.get(key, {})
    if config.get("skip_success", True) and previous.get("status") == "success":
        logger.info("跳过已成功的商户任务：%s", company)
        return {"status": "skipped", "detail": "此前已成功"}

    try:
        safe_close_known_popups(page, logger)
        if config.get("merchant_navigate_menu", True):
            page = _ensure_on_merchant_page(page, sel, timeout, logger)
        elif not _has_edit(page, sel):
            raise RuntimeError(
                f"找不到「{sel['edit_button']}」按钮；请手动进入 商家管理 -> 商户信息，"
                f"或把 config.json 的 merchant_navigate_menu 设为 true 让脚本自动导航"
            )

        edit = page.get_by_role("button", name=sel["edit_button"], exact=True)
        # 编辑按钮在首屏以下；点击会自动滚动，这里显式滚到可见更稳。
        edit.first.scroll_into_view_if_needed(timeout=timeout)
        edit.first.click(timeout=timeout)
        page.wait_for_timeout(1500)
        safe_close_known_popups(page, logger)

        if config.get("merchant_set_name", False):
            _fill_optional_textbox(
                page, sel["name_placeholder"], data.get("company_name"),
                "商家名称", timeout, logger,
            )

        images = data.get("images", {})
        if isinstance(images, list):
            raise RuntimeError(
                "data/company.json 的 images 需要是对象（按图片位命名），"
                '例如 {"logo": "images/logo.png", "gallery": "...", "main": "..."}'
            )
        for slot_key, slot in slots.items():
            value = images.get(slot_key)
            if not value:
                logger.info("未配置图片位 %s（%s），跳过", slot_key, slot.get("note", ""))
                continue
            _upload_slot(page, slot_key, slot, value, timeout, logger,
                         skip_existing=config.get("merchant_skip_existing_images", True))
            safe_close_known_popups(page, logger)

        _fill_optional_textbox(
            page, sel["intro_placeholder"], data.get("intro"),
            "商家简介", timeout, logger,
        )

        # The recording shows a 知道了 popup plus a stray 确定 right after the
        # last image slot; clear them before touching the region picker.
        safe_close_known_popups(page, logger)
        confirm = page.get_by_role("button", name="确定", exact=True)
        if confirm.count() and confirm.first.is_visible():
            confirm.first.click(timeout=timeout)
            page.wait_for_timeout(500)

        if data.get("region"):
            _change_region(page, data["region"], timeout, logger)

        _fill_optional_textbox(
            page, sel["address_placeholder"], data.get("address"),
            "联系地址", timeout, logger,
        )
        _fill_optional_textbox(
            page, sel["phone_placeholder"], data.get("phone"),
            "联系电话", timeout, logger,
        )
        _fill_optional_textbox(
            page, sel["contact_placeholder"], data.get("contact"),
            "联系人", timeout, logger,
        )

        page.get_by_role("button", name=sel["next_button"], exact=True).first.click(timeout=timeout)
        page.wait_for_timeout(1500)
        safe_close_known_popups(page, logger)

        if not config.get("submit_review", True):
            result = {"status": "success", "detail": "已填写并上传，配置为不提交审核"}
        else:
            page.get_by_role("button", name=sel["submit_button"], exact=True).first.click(timeout=timeout)
            submitted = True
            page.wait_for_timeout(2000)
            body = page.locator("body").inner_text(timeout=3000)
            if any(text in body for text in SUCCESS_TEXTS):
                result = {"status": "success", "detail": "页面出现提交成功/审核状态提示"}
            else:
                result = {
                    "status": "needs_review",
                    "detail": "已点击提交审核，但未检测到明确成功提示，请人工确认",
                }
        set_task(config, state, key, result["status"], result.get("detail", ""))
        logger.info("商户任务结束：%s -> %s", company, result["status"])
        return result
    except Exception as exc:
        handler = AgentExceptionHandler(page, config, logger)
        result = handler.handle(f"merchant::{company}", exc, submit_started=submitted)
        set_task(config, state, key, result["status"], result.get("detail", result.get("error", "")))
        return result
