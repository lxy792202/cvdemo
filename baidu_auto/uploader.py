# -*- coding: utf-8 -*-
"""百度基木鱼产品批量录入自动化（Playwright）

说明：
1. 第一次运行请使用 headless=False，手动登录并进入产品管理页面。
2. 本脚本根据录制时的页面定位器编写，基木鱼页面变化时可能需要调整 SELECTORS。
3. 初次测试请只放一个产品，config.json 中 submit_review=false，验证上传无误后再开启提交。
4. 提交状态不确定时标记 needs_review，不会自动重复提交，避免重复创建。
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# -------------------- 路径及配置 --------------------
ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"

DEFAULT_CONFIG = {
    "base_url": "https://www2.baidu.com/",
    "headless": False,
    "submit_review": False,
    "max_retries": 0,
    "skip_success": True,
    "wait_timeout": 15000,
    "product_dir": "products",
    "profile_dir": "browser_profile",
    "state_file": "state/tasks_state.json",
    "log_file": "logs/uploader.log",
    "screenshot_dir": "logs/screenshots",
    "manual_product_page": True
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return {**DEFAULT_CONFIG, **data}
        except Exception as exc:
            print(f"配置文件读取失败，将使用默认配置：{exc}")
    return DEFAULT_CONFIG.copy()


CONFIG = load_config()
PRODUCT_DIR = ROOT / CONFIG["product_dir"]
PROFILE_DIR = ROOT / CONFIG["profile_dir"]
STATE_FILE = ROOT / CONFIG["state_file"]
LOG_FILE = ROOT / CONFIG["log_file"]
SCREENSHOT_DIR = ROOT / CONFIG["screenshot_dir"]

for folder in (PROFILE_DIR, STATE_FILE.parent, LOG_FILE.parent, SCREENSHOT_DIR):
    folder.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("baidu_uploader")

# -------------------- 页面定位器 --------------------
# 这些定位器来源于你之前的 Playwright Codegen 录制。
# 页面发生变化时，优先调整这里，不要改任务处理主流程。
SELECTORS = {
    "new_product_button": {"role": "button", "name": "新建产品"},
    "manual_entry_text": "手动录入",
    "long_title_placeholder": "请输入产品长标题，4-100字符",
    "short_title_placeholder": "请输入产品短标题，4-24字符",
    "upload_local_text": "本地上传",
    "upload_confirm_text": "确定",
    "submit_button_text": "提交审核",
    "main_upload_icon": ".dls-icon.vi-upload-add-cross-cross-icon",
    "square_upload_selector": (
        "#sdk-content-oneToOne > .one-col-19 > .picture-container > "
        ".inner-picture > .common-image-uploader > .vi.vi-ccsdk-image-upload > "
        ".vi.vi-image-upload-anchor > .vi.vi-upload-anchor > .vi"
    ),
    "detail_editor_selector": ".fr-element",
    "file_input_selector": "input[type=file]",
}


# 只处理已知且明确的引导弹窗；不对未知弹窗进行盲点关闭。
KNOWN_POPUP_TEXTS = ["知道了", "稍后再说", "跳过"]

SUCCESS_TEXTS = ["提交成功", "审核中", "已提交审核", "审核提交成功"]


# -------------------- 通用工具 --------------------
def save_json_atomic(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("状态文件损坏或读取失败，将从空状态开始")
        return {}


def screenshot(page, name: str):
    safe_name = re.sub(r"[^\w\-\.\u4e00-\u9fff]", "_", name)
    path = SCREENSHOT_DIR / f"{safe_name}_{int(time.time())}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        logger.info("截图保存：%s", path)
    except Exception as exc:
        logger.warning("截图失败：%s", exc)
    return path


def wait_page_ready(page):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=CONFIG["wait_timeout"])
    except PlaywrightTimeoutError:
        pass


def safe_dismiss_known_popup(page):
    """关闭已知引导弹窗。未知弹窗保留，交由截图/人工处理。"""
    for text in KNOWN_POPUP_TEXTS:
        try:
            locator = page.get_by_text(text, exact=True)
            if locator.count() > 0:
                visible = False
                for i in range(min(locator.count(), 3)):
                    try:
                        if locator.nth(i).is_visible():
                            locator.nth(i).click(timeout=1500)
                            logger.info("关闭已知提示：%s", text)
                            visible = True
                            break
                    except Exception:
                        continue
                if visible:
                    page.wait_for_timeout(300)
        except Exception:
            continue


def wait_visible(locator, timeout=None):
    locator.wait_for(state="visible", timeout=timeout or CONFIG["wait_timeout"])
    return locator


def click_text(page, text: str, exact=True, timeout=None):
    loc = page.get_by_text(text, exact=exact)
    wait_visible(loc, timeout)
    loc.first.click(timeout=timeout or CONFIG["wait_timeout"])


def click_button(page, text: str, timeout=None):
    loc = page.get_by_role("button", name=text, exact=True)
    wait_visible(loc, timeout)
    loc.first.click(timeout=timeout or CONFIG["wait_timeout"])


def get_product_dirs() -> list[Path]:
    if not PRODUCT_DIR.exists():
        logger.error("产品目录不存在：%s", PRODUCT_DIR)
        return []
    return sorted([p for p in PRODUCT_DIR.iterdir() if p.is_dir()])


# -------------------- 产品数据读取校验 --------------------
def load_products() -> list[dict]:
    tasks = []
    for folder in get_product_dirs():
        json_path = folder / "product.json"
        if not json_path.exists():
            logger.warning("跳过，缺少 product.json：%s", folder.name)
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("产品 JSON 解析失败 %s：%s", json_path, exc)
            continue

        data["folder"] = folder
        data["product_name"] = data.get("product_name", folder.name)
        data["long_title"] = str(data.get("long_title", "")).strip()
        data["short_title"] = str(data.get("short_title", "")).strip()
        data["tags"] = data.get("tags", [])
        data["images"] = data.get("images", {})

        required_fields = ["long_title", "short_title"]
        missing = [field for field in required_fields if not data.get(field)]
        if missing:
            logger.error("%s 缺少必填字段：%s", folder.name, missing)
            continue

        image_paths = {}
        image_errors = []
        for key in ("main", "square", "detail"):
            image_name = data["images"].get(key)
            if not image_name:
                image_errors.append(f"images.{key} 未配置")
                continue
            image_path = folder / image_name
            if not image_path.exists():
                image_errors.append(f"图片不存在：{image_path}")
                continue
            image_paths[key] = image_path

        if image_errors:
            logger.error("产品 %s 图片校验失败：%s", folder.name, "；".join(image_errors))
            continue

        data["image_paths"] = image_paths
        data["category_clicks"] = data.get(
            "category_clicks",
            [
                {"text": "其他", "index": 0},
                {"text": "其他", "index": 2},
                {"text": "其他", "index": 3}
            ]
        )
        tasks.append(data)
    return tasks


# -------------------- 上传图片 --------------------
def upload_image(page, image_path: Path, label: str):
    """点击本地上传，选择文件，并尝试确认弹窗。

    由于上传弹窗结构可能变化，文件选择通过 input[type=file] 完成。
    确认按钮只在可见时点击；如页面结构变化会抛出异常并记录截图。
    """
    logger.info("开始上传%s：%s", label, image_path.name)
    click_text(page, SELECTORS["upload_local_text"], exact=True)

    file_input = page.locator(SELECTORS["file_input_selector"])
    file_input.last.set_input_files(str(image_path), timeout=CONFIG["wait_timeout"])
    page.wait_for_timeout(700)

    confirm = page.get_by_role("button", name=SELECTORS["upload_confirm_text"], exact=True)
    visible_confirm = []
    for i in range(confirm.count()):
        try:
            if confirm.nth(i).is_visible():
                visible_confirm.append(confirm.nth(i))
        except Exception:
            continue

    # Codegen 录制中有两个“确定”按钮，通常第二个为上传确认。
    # 若两个均可见，先点后一个；若只有一个可见，则点它。
    if visible_confirm:
        target = visible_confirm[-1]
        target.click(timeout=CONFIG["wait_timeout"])
        page.wait_for_timeout(400)
        # 上传弹窗可能还有一次确认。只有仍存在可见“确定”按钮才再次点击。
        remaining = []
        for i in range(confirm.count()):
            try:
                if confirm.nth(i).is_visible():
                    remaining.append(confirm.nth(i))
            except Exception:
                pass
        if remaining:
            try:
                remaining[0].click(timeout=2500)
                page.wait_for_timeout(400)
            except Exception:
                pass
    else:
        logger.warning("上传确认按钮未找到/不可见，请检查页面：%s", label)

    logger.info("上传流程已执行：%s", label)


# -------------------- 产品分类与标签 --------------------
def fill_category(page, category_clicks: list):
    """按 product.json 中的点击配置选择分类。

    index 是录制时采用的同名文本序号，页面变动后可能失效，建议测试时观察每一步。
    """
    for step, item in enumerate(category_clicks, start=1):
        text = item.get("text", "其他")
        index = int(item.get("index", 0))
        locator = page.get_by_text(text, exact=True)
        wait_visible(locator)
        if locator.count() <= index:
            raise RuntimeError(
                f"分类选择失败：第{step}步文本‘{text}’只有{locator.count()}个，无法取 nth({index})"
            )
        logger.info("选择分类步骤%d：text=%s index=%d", step, text, index)
        locator.nth(index).click(timeout=CONFIG["wait_timeout"])
        page.wait_for_timeout(300)


def fill_tags(page, tags: list[str]):
    for tag in tags:
        locator = page.get_by_text(tag, exact=True)
        if locator.count() == 0:
            logger.warning("标签未找到，跳过：%s", tag)
            continue
        # 标签在录制页面可能存在多个同名文本，优先点最后一个。
        locator.last.click(timeout=CONFIG["wait_timeout"])
        page.wait_for_timeout(200)
        logger.info("选择标签：%s", tag)


def fill_product_basic_info(page, product: dict):
    long_title = page.get_by_placeholder(SELECTORS["long_title_placeholder"], exact=True)
    short_title = page.get_by_placeholder(SELECTORS["short_title_placeholder"], exact=True)
    wait_visible(long_title)
    long_title.fill(product["long_title"])
    short_title.fill(product["short_title"])
    logger.info("填写产品标题：%s", product["product_name"])
    fill_tags(page, product.get("tags", []))


# -------------------- 产品创建主流程 --------------------
def create_product(page, product: dict) -> str:
    """执行单个产品创建。
    返回：success / needs_review / failed
    """
    product_name = product["product_name"]
    submitted_or_started = False

    try:
        safe_dismiss_known_popup(page)

        # 进入新建产品
        click_button(page, SELECTORS["new_product_button"]["name"])
        page.wait_for_timeout(500)
        click_text(page, SELECTORS["manual_entry_text"], exact=True)
        page.wait_for_timeout(500)

        # 分类、标题、标签
        fill_category(page, product.get("category_clicks", []))
        fill_product_basic_info(page, product)

        # 主图
        main_upload = page.locator(SELECTORS["main_upload_icon"])
        wait_visible(main_upload)
        main_upload.first.click()
        upload_image(page, product["image_paths"]["main"], "主图")

        # 方图
        square_upload = page.locator(SELECTORS["square_upload_selector"])
        wait_visible(square_upload)
        square_upload.first.click()
        upload_image(page, product["image_paths"]["square"], "方图")

        # 详情图：Codegen 录制时通过富文本编辑区域触发上传
        detail_editor = page.locator(SELECTORS["detail_editor_selector"])
        wait_visible(detail_editor)
        detail_editor.first.click()
        upload_image(page, product["image_paths"]["detail"], "详情图")

        logger.info("产品内容填写/上传完成：%s", product_name)
        screenshot(page, f"{product_name}_提交前")

        if not CONFIG.get("submit_review", False):
            logger.info("配置 submit_review=false，暂不提交审核：%s", product_name)
            return "needs_review"

        # 提交只点击一次。提交后无论确认状态如何，都不自动重复提交。
        submit_btn = page.get_by_role(
            "button", name=SELECTORS["submit_button_text"], exact=True
        )
        wait_visible(submit_btn)
        submit_btn.first.click(timeout=CONFIG["wait_timeout"])
        submitted_or_started = True
        page.wait_for_timeout(1000)

        # 如果弹出确认框，尝试在 dialog 内确认；不再对页面其他位置盲点按钮。
        dialog = page.get_by_role("dialog")
        try:
            if dialog.count() > 0 and dialog.first.is_visible():
                confirm_button = dialog.first.get_by_role(
                    "button", name=SELECTORS["submit_button_text"], exact=True
                )
                if confirm_button.count() > 0 and confirm_button.first.is_visible():
                    confirm_button.first.click(timeout=4000)
                    page.wait_for_timeout(1000)
        except Exception as exc:
            logger.info("提交确认弹窗处理未完成（继续检查状态）：%s", exc)

        # 尽量识别成功提示。未明确识别到成功，标记人工确认，避免误判。
        body_text = ""
        try:
            body_text = page.locator("body").inner_text(timeout=3000)
        except Exception:
            pass
        if any(text in body_text for text in SUCCESS_TEXTS):
            screenshot(page, f"{product_name}_提交成功")
            logger.info("检测到提交成功提示：%s", product_name)
            return "success"

        screenshot(page, f"{product_name}_提交状态待确认")
        logger.warning("提交动作已执行，但未确认成功，标记 needs_review：%s", product_name)
        return "needs_review"

    except Exception as exc:
        logger.exception("产品创建流程失败：%s", product_name)
        screenshot(page, f"{product_name}_失败")
        # 提交动作已经发生但状态未知，不能当作普通失败重试。
        return "needs_review" if submitted_or_started else "failed"


# -------------------- 批量任务调度 --------------------
def run_product_tasks(page):
    products = load_products()
    if not products:
        logger.warning("没有找到有效产品任务。检查 products/ 目录和 product.json。")
        return

    state = load_state()
    logger.info("有效产品数量：%d", len(products))

    for product in products:
        name = product["product_name"]
        task_key = str(product["folder"].relative_to(ROOT))
        old = state.get(task_key, {})
        old_status = old.get("status")

        if CONFIG.get("skip_success", True) and old_status == "success":
            logger.info("跳过已成功任务：%s", name)
            continue
        if old_status == "needs_review":
            logger.warning("跳过待人工核查任务，避免重复提交：%s；请先检查平台", name)
            continue

        retries = int(CONFIG.get("max_retries", 0))
        attempt = 0
        status = "failed"

        while attempt <= retries:
            attempt += 1
            logger.info("开始产品任务：%s（尝试 %d/%d）", name, attempt, retries + 1)
            status = create_product(page, product)

            if status != "failed":
                break

            if attempt <= retries:
                logger.warning("任务失败，将尝试重试：%s", name)
                # 失败重试前不自动点击未知页面按钮，等待人工确认页面回到可创建状态。
                try:
                    safe_dismiss_known_popup(page)
                except Exception:
                    pass
                page.wait_for_timeout(1500)

        state[task_key] = {
            "product_name": name,
            "status": status,
            "attempts": attempt,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "folder": task_key
        }
        save_json_atomic(STATE_FILE, state)
        logger.info("产品任务完成：%s -> %s", name, status)

        if status in ("failed", "needs_review"):
            logger.warning("任务需要人工处理；为避免误操作，暂停后续产品。")
            break

        # 成功后等待一下再开始下一项
        page.wait_for_timeout(1200)


# -------------------- 启动入口 --------------------
def main():
    print("=" * 60)
    print("百度基木鱼产品录入自动化")
    print("首次运行：手动登录并进入产品管理/新建产品所在页面")
    print("当前 submit_review =", CONFIG.get("submit_review", False))
    print("=" * 60)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=CONFIG.get("headless", False),
            viewport={"width": 1440, "height": 1000},
            accept_downloads=True
        )

        page = context.pages[0] if context.pages else context.new_page()
        page.goto(CONFIG.get("base_url", "https://www2.baidu.com/"), wait_until="domcontentloaded")
        wait_page_ready(page)

        print("\n请在浏览器中完成以下操作：")
        print("1. 如果未登录，手动登录百度账号")
        print("2. 进入基木鱼")
        print("3. 进入内容中心/产品管理页面，确保页面可以看到‘新建产品’")
        print("4. 确认当前页面没有未处理的弹窗")
        input("准备好后，回到终端按 Enter 开始自动化……")

        # 重新取当前活动页，兼容用户手动打开新标签页/弹窗。
        pages = context.pages
        page = pages[-1] if pages else page
        try:
            run_product_tasks(page)
        except KeyboardInterrupt:
            logger.warning("用户中断任务")
        except Exception:
            logger.exception("批量任务异常")
            screenshot(page, "批量任务异常")
        finally:
            print("任务结束。浏览器将在 5 秒后关闭；登录状态已保存在 browser_profile。")
            page.wait_for_timeout(5000)
            context.close()


if __name__ == "__main__":
    main()
