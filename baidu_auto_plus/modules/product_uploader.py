from pathlib import Path
import json
import logging
from common import safe_close_known_popups, upload_local_file
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
        tasks.append(data)
    return tasks

def _click_category_steps(page, product, timeout):
    """
    Recorded category clicks are index-based and fragile.
    Keep the indexes configurable in product.json; verify each click against the live page.
    """
    for step in product.get("category_clicks", []):
        text = step["text"]
        index = int(step.get("index", 0))
        matches = page.get_by_text(text, exact=True)
        if matches.count() <= index:
            raise RuntimeError(f"分类选项找不到：{text} nth({index})，请更新 category_clicks")
        matches.nth(index).click(timeout=timeout)

def _fill_tags(page, tags, timeout):
    for tag in tags:
        loc = page.get_by_text(tag, exact=True)
        if not loc.count():
            raise RuntimeError(f"标签选项未找到：{tag}")
        loc.last.click(timeout=timeout)

def _upload_confirm(page, image_path, timeout):
    upload_local_file(page, image_path, timeout)
    # These confirmation buttons came from the recorded flow; verify their presence.
    confirm = page.get_by_role("button", name="确定", exact=True)
    if confirm.count():
        if confirm.count() >= 2 and confirm.nth(1).is_visible():
            confirm.nth(1).click(timeout=timeout)
        if confirm.count() and confirm.first.is_visible():
            confirm.first.click(timeout=timeout)

def create_one_product(page, config, product, logger):
    timeout = config.get("wait_timeout", 15000)
    folder = product["_folder"]
    name = product.get("product_name", folder.name)
    submitted = False
    try:
        safe_close_known_popups(page, logger)
        page.get_by_role("button", name="新建产品", exact=True).click(timeout=timeout)
        page.get_by_text("手动录入", exact=True).click(timeout=timeout)

        _click_category_steps(page, product, timeout)

        long_title = page.get_by_role("textbox", name="请输入产品长标题，4-100字符")
        long_title.fill(product["long_title"], timeout=timeout)
        short_title = page.get_by_role("textbox", name="请输入产品短标题，4-24字符")
        short_title.fill(product["short_title"], timeout=timeout)
        _fill_tags(page, product.get("tags", []), timeout)

        images = product.get("images", {})
        # Main image upload button: selector from the user's recorded Codegen flow.
        main_button = page.locator(".dls-icon.vi-upload-add-cross-cross-icon").first
        main_button.click(timeout=timeout)
        _upload_confirm(page, folder / images["main"], timeout)

        # Secondary/square upload selector from recorded flow; may change if UI changes.
        square_selector = (
            "#sdk-content-oneToOne > .one-col-19 > .picture-container > "
            ".inner-picture > .common-image-uploader > .vi.vi-ccsdk-image-upload > "
            ".vi.vi-image-upload-anchor > .vi.vi-upload-anchor > .vi"
        )
        page.locator(square_selector).click(timeout=timeout)
        _upload_confirm(page, folder / images["square"], timeout)

        # Details area selector was recorded as .fr-element; use only if configured.
        if images.get("detail"):
            detail_editor = page.locator(".fr-element").first
            detail_editor.click(timeout=timeout)
            _upload_confirm(page, folder / images["detail"], timeout)

        # Description is intentionally not guessed; configure/record its editor locator first.
        if product.get("description"):
            logger.info("配置了 description，但当前录制未提供可靠编辑器定位，暂不自动填入。")

        if config.get("submit_review", True):
            submit = page.get_by_role("button", name="提交审核", exact=True)
            submit.click(timeout=timeout)
            submitted = True
            # Handle a confirmation dialog if one appears, but do not blindly click twice.
            dialog_submit = page.get_by_role("dialog").get_by_role("button", name="提交审核", exact=True)
            if dialog_submit.count() and dialog_submit.first.is_visible():
                dialog_submit.first.click(timeout=timeout)
            page.wait_for_timeout(1500)
            # If a success/status toast is visible, mark success; otherwise ambiguous => review.
            success_texts = ["提交成功", "审核中", "已提交审核", "审核提交成功"]
            body = page.locator("body").inner_text(timeout=3000)
            if any(t in body for t in success_texts):
                return {"status": "success", "detail": "页面出现提交成功/审核状态提示"}
            return {"status": "needs_review", "detail": "已点击提交，页面未检测到明确成功提示，请人工确认"}
        return {"status": "success", "detail": "已填写并上传，配置为不提交审核"}
    except Exception as e:
        handler = AgentExceptionHandler(page, config, logger)
        return handler.handle(name, e, submit_started=submitted)

def run_product_tasks(page, config, logger):
    state = load_state(config)
    products = load_products(config)
    results = []
    for product in products:
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
