from pathlib import Path
import json
import re
import time
import logging
from common import (
    count_thumbnails,
    image_signature,
    resolve_path,
    safe_close_known_popups,
    upload_local_file,
    wait_upload_confirmed,
)
from state_manager import load_state, set_task
from agent_handler import AgentExceptionHandler

ROOT = Path(__file__).resolve().parents[1]

def load_products(config):
    folder = ROOT / config["product_dir"]
    tasks = []
    if not folder.exists():
        raise FileNotFoundError(f"产品目录不存在：{folder}")
    for product_folder in sorted(folder.iterdir()):
        if not product_folder.is_dir():
            continue
        json_file = product_folder / "product.json"
        if not json_file.exists():
            logging.warning("跳过缺少 product.json 的目录：%s", product_folder)
            continue
        data = json.loads(json_file.read_text(encoding="utf-8"))
        data["_folder"] = product_folder
        # Skip placeholder folders whose images were never filled in, so a
        # template directory cannot abort the whole batch.
        missing = [
            key for key, rel in (data.get("images") or {}).items()
            if rel and not resolve_path(rel, product_folder).exists()
        ]
        if missing:
            logging.warning(
                "跳过 %s：图片文件不存在 %s（请放置真实图片或运行 tools/build_data.py）",
                product_folder.name, missing,
            )
            continue
        tasks.append(data)
    return tasks

def _click_category_steps(page, product, timeout, logger):
    """Recorded category clicks are index-based and fragile.

    新版表单的行业字段默认已预选「其他」，录制里的三级点击找不到目标；
    找不到就警告跳过（真缺分类时提交审核会报平台错误，不会静默通过）。
    """
    for step in product.get("category_clicks", []):
        text = step["text"]
        index = int(step.get("index", 0))
        matches = page.get_by_text(text, exact=True)
        if matches.count() <= index:
            logger.warning(
                "分类选项找不到：%s nth(%s)，跳过该步（使用页面默认分类）", text, index
            )
            continue
        matches.nth(index).click(timeout=timeout)
        page.wait_for_timeout(600)

def _fill_tags(page, tags, timeout):
    """Fill the tag widget.

    The widget accepts both preset suggestions (older recording clicked them)
    and free text (newer recording types into #sdk-for-product-tag-input-select
    and presses Enter). Try the preset first, fall back to typing, so either UI
    variant works.
    """
    if not tags:
        return
    wrapper = page.locator("#sdk-for-product-tag-input-select")
    input_loc = page.locator("#sdk-for-product-tag-input-select input")
    for tag in tags:
        preset = page.get_by_text(tag, exact=True)
        if preset.count():
            try:
                preset.last.click(timeout=2000)
                continue
            except Exception:
                pass
        if input_loc.count():
            input_loc.first.click(timeout=timeout)
            input_loc.first.fill(tag, timeout=timeout)
            input_loc.first.press("Enter")
        elif wrapper.count():
            # 新版标签组件外层是 div，fill 不了；点开后直接键入
            wrapper.first.click(timeout=timeout)
            page.keyboard.insert_text(tag)
            page.keyboard.press("Enter")
        else:
            raise RuntimeError(f"标签既无预设选项也无输入框，无法添加：{tag}")
        page.wait_for_timeout(200)

def _upload_detail_image(page, resolved, timeout, logger):
    """详情富文本里的拼图：新版入口是编辑器工具条「上传图片」按钮。

    点击后要么直接唤起系统 Finder，要么打开图片库网页选择器，两种都兼容。
    """
    from modules.merchant_uploader import (
        _capture_alert,
        _picker_open,
        _upload_via_picker,
    )
    before_sig = image_signature(page)
    editor = page.locator(".fr-element").first
    editor.click(timeout=timeout)
    btn = page.get_by_role("button", name="上传图片", exact=True)
    target = None
    for i in range(btn.count()):
        if btn.nth(i).is_visible():
            target = btn.nth(i)
            break
    if target is None:
        raise RuntimeError("详情编辑器里找不到「上传图片」按钮")
    try:
        with page.expect_file_chooser(timeout=5000) as fc_info:
            target.click(timeout=timeout)
        fc_info.value.set_files(str(resolved))
        logger.info("详情拼图：已拦截系统文件选择器并设置文件")
    except Exception as exc:
        if _picker_open(page):
            logger.info("详情拼图：走图片库选择器通道")
            _upload_via_picker(page, "detail", resolved, timeout, logger)
        else:
            raise RuntimeError(
                f"详情拼图上传入口点击后既无系统选择器也无图片库：{str(exc).splitlines()[0]}"
            )
    page.wait_for_timeout(6000)
    err_text = _capture_alert(page, 10)
    if err_text:
        raise RuntimeError(f"平台拒绝详情拼图：{err_text}")
    wait_upload_confirmed(page, before_sig=before_sig, timeout=max(timeout, 30000))


def _upload_product_image(page, label, rel, folder, timeout, logger):
    """按「横版图4:3 / 横版图1:1」标签定位加号上传位；录制选择器作首选。"""
    if not rel:
        logger.info("未配置 %s 图片，跳过", label)
        return
    recorded = {
        "横版图4:3": ".dls-icon.vi-upload-add-cross-cross-icon",
        "横版图1:1": (
            "#sdk-content-oneToOne > .one-col-19 > .picture-container > "
            ".inner-picture > .common-image-uploader > .vi.vi-ccsdk-image-upload > "
            ".vi.vi-image-upload-anchor > .vi.vi-upload-anchor > .vi"
        ),
    }[label]
    opener = page.locator(recorded)
    if not opener.count() or not opener.first.is_visible():
        opener = page.locator(
            "xpath=//div[contains(text(),'%s')]/following::div"
            "[contains(@class,'vi-upload-add-cross')][1]" % label
        )
    opener.first.scroll_into_view_if_needed(timeout=timeout)
    opener.first.click(timeout=timeout)
    _upload_confirm(page, resolve_path(rel, folder), timeout, logger)


def _upload_confirm(page, image_path, timeout, logger=None):
    """产品图上传：图片库弹窗 -> 本地上传(拦截 Finder) -> 上传图片确定 ->
    等 10s 平台加载 -> 图片库确定。两层确定之间不能连点，否则「处理失败」。"""
    before_sig = image_signature(page)
    upload_local_file(page, image_path, timeout)
    page.wait_for_timeout(6000)
    confirm = page.get_by_role("button", name="确定", exact=True)
    if confirm.count() >= 2 and confirm.nth(1).is_visible():
        confirm.nth(1).click(timeout=timeout)
    page.wait_for_timeout(10000)
    confirm = page.get_by_role("button", name="确定", exact=True)
    if confirm.count() and confirm.first.is_visible():
        confirm.first.click(timeout=timeout)
    # 平台校验失败的红框约 16-18s 后才弹，轮询 25s（成功时图片库关闭即提前退出）
    from modules.merchant_uploader import _capture_alert, _close_picker
    err_text = _capture_alert(page, 25)
    if err_text:
        try:
            page.get_by_role("button", name="知道了", exact=True).first.click(timeout=2000)
        except Exception:
            pass
        _close_picker(page)
        raise RuntimeError(f"平台拒绝本次上传：{err_text}")
    # Clicking 确定 is not proof the image landed; the platform can reject the
    # file with a toast that vanishes in ~2s and leave the form imageless.
    wait_upload_confirmed(page, before_sig=before_sig, timeout=max(timeout, 30000))

def _reset_product_form(page, timeout, logger):
    """失败/开局时若产品表单还开着，点取消收回列表，保证批量能继续。

    取消会弹「确认关闭：保存并关闭/直接关闭/取消」，这里选直接关闭（不保存草稿）。
    """
    def _close_confirm_dialog():
        dlg = page.get_by_role("dialog").filter(has_text="确认关闭")
        if dlg.count() and dlg.first.is_visible():
            dlg.get_by_role("button", name="直接关闭", exact=True).first.click(timeout=3000)
            page.wait_for_timeout(800)
            return True
        return False

    _close_confirm_dialog()
    if page.get_by_role("button", name="新建产品", exact=True).count():
        return
    cancel = page.get_by_role("button", name="取消", exact=True)
    if cancel.count() and cancel.first.is_visible():
        cancel.first.click(timeout=3000)
        page.wait_for_timeout(800)
        if _close_confirm_dialog():
            logger.info("已取消未提交的产品表单，回到列表")


def create_one_product(page, config, product, logger):
    timeout = config.get("wait_timeout", 15000)
    folder = product["_folder"]
    name = product.get("product_name", folder.name)
    submitted = False
    try:
        safe_close_known_popups(page, logger)
        _reset_product_form(page, timeout, logger)
        # 「新建产品」是下拉按钮：点击后应展开含「手动录入」的菜单；
        # 偶发第一次点击不展开（页面刚渲染完），重试最多 3 次。
        manual = page.locator("span").filter(has_text="手动录入").first
        for attempt in range(3):
            page.get_by_role("button", name="新建产品", exact=True).click(timeout=timeout)
            try:
                manual.wait_for(state="visible", timeout=5000)
                break
            except Exception:
                logger.info("第 %s 次点「新建产品」未展开菜单，重试", attempt + 1)
                page.keyboard.press("Escape")
                page.wait_for_timeout(800)
        # Recorded as a span filter; plain get_by_text misses it on some builds.
        if manual.count() and manual.first.is_visible():
            manual.first.click(timeout=timeout)
        else:
            page.get_by_text("手动录入", exact=True).click(timeout=timeout)
        # 表单异步渲染；等分类区出现再点分类，避免在列表页空点
        try:
            page.get_by_text("分类信息", exact=True).first.wait_for(
                state="visible", timeout=timeout
            )
        except Exception as exc:
            logger.warning("等待产品表单渲染超时：%s", str(exc).splitlines()[0])

        _click_category_steps(page, product, timeout, logger)

        long_title = page.get_by_role("textbox", name="请输入产品长标题，4-100字符")
        long_title.fill(product["long_title"], timeout=timeout)
        short_title = page.get_by_role("textbox", name="请输入产品短标题，4-24字符")
        short_title.fill(product["short_title"], timeout=timeout)
        _fill_tags(page, product.get("tags", []), timeout)

        images = product.get("images", {})
        _upload_product_image(page, "横版图4:3", images.get("main"), folder, timeout, logger)
        _upload_product_image(page, "横版图1:1", images.get("square"), folder, timeout, logger)

        # Details area selector was recorded as .fr-element; use only if configured.
        if images.get("detail"):
            _upload_detail_image(page, resolve_path(images["detail"], folder), timeout, logger)

        # 详情要求「至少 1 张图片或 100 字符」；描述写入富文本编辑器兜底+充实内容
        if product.get("description"):
            editor = page.locator(".fr-element").first
            editor.click(timeout=timeout)
            page.keyboard.insert_text(product["description"])
            logger.info("已写入产品描述到详情编辑器")

        if config.get("submit_review", True):
            submit = page.get_by_role("button", name="提交审核", exact=True)
            submit.click(timeout=timeout)
            # The first click can be swallowed by an interstitial that the
            # recording dismisses with 关闭 before clicking 提交审核 again.
            # Nothing has been submitted yet at this point, so this is safe.
            page.wait_for_timeout(1000)
            close_btn = page.get_by_role("button", name="关闭", exact=True)
            if close_btn.count() and close_btn.first.is_visible():
                logger.info("提交前出现遮挡弹层，点击关闭后重试提交")
                close_btn.first.click(timeout=timeout)
                page.wait_for_timeout(500)
                submit.click(timeout=timeout)
            submitted = True
            # Handle a confirmation dialog if one appears, but do not blindly click twice.
            dialog_submit = page.get_by_role("dialog").get_by_role("button", name="提交审核", exact=True)
            if dialog_submit.count() and dialog_submit.first.is_visible():
                dialog_submit.first.click(timeout=timeout)
            # 成功 toast 可能在遮挡层/二次确认之后才出现，轮询 12s 再判未知。
            success_texts = ["提交成功", "审核中", "已提交审核", "审核提交成功"]
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                body = page.locator("body").inner_text(timeout=3000)
                if any(t in body for t in success_texts):
                    return {"status": "success", "detail": "页面出现提交成功/审核状态提示"}
                page.wait_for_timeout(1000)
            return {"status": "needs_review", "detail": "已点击提交，页面未检测到明确成功提示，请人工确认"}
        return {"status": "success", "detail": "已填写并上传，配置为不提交审核"}
    except Exception as e:
        _reset_product_form(page, timeout, logger)
        handler = AgentExceptionHandler(page, config, logger)
        return handler.handle(name, e, submit_started=submitted)

def _ensure_on_product_page(page, config, logger):
    """从任意位置导航到 产品管理（页面应显示「新建产品」按钮）。"""
    timeout = config.get("wait_timeout", 15000)
    if page.get_by_role("button", name="新建产品", exact=True).count():
        return page
    def _vis(loc):
        for i in range(loc.count()):
            try:
                if loc.nth(i).is_visible():
                    return loc.nth(i)
            except Exception:
                continue
        return None

    context = page.context
    target = page
    target.wait_for_timeout(2000)
    jimuyu_el = _vis(target.get_by_text("基木鱼", exact=True))
    if jimuyu_el is not None:
        before = len(context.pages)
        jimuyu_el.click(timeout=timeout)
        target.wait_for_timeout(2500)
        if len(context.pages) > before:
            target = context.pages[-1]
            try:
                target.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
        safe_close_known_popups(target, logger)
    for label in ("内容中心", "产品管理"):
        el = _vis(target.get_by_text(label, exact=True))
        if el is None:
            logger.info("菜单「%s」不存在或不可见，跳过", label)
            continue
        before = len(context.pages)
        el.click(timeout=timeout)
        target.wait_for_timeout(2000)
        if len(context.pages) > before:
            target = context.pages[-1]
        if target.get_by_role("button", name="新建产品", exact=True).count():
            logger.info("已到达产品管理页：%s", target.url)
            return target
    # 录制里内容中心落地页要点第 6 个 span 才进产品列表
    try:
        target.locator("span").nth(5).click(timeout=5000)
        target.wait_for_timeout(2000)
    except Exception:
        pass
    # 兜底：菜单/span 都失效时直连产品管理 URL（userid 从标签页地址解析）
    if not target.get_by_role("button", name="新建产品", exact=True).count():
        uid = None
        for tab in context.pages:
            m = re.search(r"(?:userid|ucUserId)=(\d+)", tab.url) or \
                re.search(r"/user/(\d+)/", tab.url)
            if m:
                uid = m.group(1)
                break
        if uid:
            logger.info("菜单导航失效，直连产品管理 URL（userid=%s）", uid)
            target.goto(
                f"https://wutong.baidu.com/material/user/{uid}/product"
                f"?botMessageLength=0&isBotRestore=1&ucUserId={uid}",
                wait_until="domcontentloaded", timeout=30000,
            )
            safe_close_known_popups(target, logger)
            # SPA 渲染慢，显式等按钮出现（实测约 5s）
            try:
                target.get_by_role("button", name="新建产品", exact=True).first.wait_for(
                    state="visible", timeout=15000)
            except Exception:
                pass
    if not target.get_by_role("button", name="新建产品", exact=True).count():
        raise RuntimeError(
            f"导航后仍找不到「新建产品」按钮；当前标签页：{[p.url for p in context.pages]}"
        )
    logger.info("已到达产品管理页：%s", target.url)
    return target


def run_product_tasks(page, config, logger):
    state = load_state(config)
    if config.get("product_navigate_menu", True):
        page = _ensure_on_product_page(page, config, logger)
    products = load_products(config)
    # 先剔除已成功的，再按 product_limit 截断；否则限额会浪费在跳过项上。
    # 产品重复创建会在列表里堆重复条目，故此处不受 skip_success 影响。
    pending = [
        p for p in products
        if state.get(
            f"product::{p.get('product_name', p['_folder'].name)}", {}
        ).get("status") != "success"
    ]
    limit = config.get("product_limit")
    if limit:
        pending = pending[: int(limit)]
        logger.info("product_limit=%s，本批只跑前 %s 个待传产品", limit, len(pending))
    results = []
    for product in pending:
        name = product.get("product_name", product["_folder"].name)
        key = f"product::{name}"
        previous = state.get(key, {})
        if config.get("skip_success", True) and previous.get("status") == "success":
            logger.info("跳过已成功产品：%s", name)
            results.append((name, "skipped"))
            continue
        logger.info("开始产品任务：%s", name)
        result = create_one_product(page, config, product, logger)
        status = result.get("status", "failed")
        set_task(config, state, key, status, result.get("detail", result.get("error", "")))
        logger.info("产品任务结束：%s -> %s", name, status)
        results.append((name, status))
        # No blind retries: if submission may have happened, duplicate creation is risky.
        if status == "needs_review":
            logger.warning("状态不确定，停止批量流程，先人工确认，防止重复提交。")
            break
    return results
