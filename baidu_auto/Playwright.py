
# -*- coding: utf-8 -*-
"""百度基木鱼产品批量录入自动化（Playwright）"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ==================== 路径配置 ====================

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


def load_config():
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return {**DEFAULT_CONFIG, **data}
        except Exception as exc:
            print(f"配置文件读取失败：{exc}")
    return DEFAULT_CONFIG.copy()


CONFIG = load_config()

PRODUCT_DIR = ROOT / CONFIG["product_dir"]
PROFILE_DIR = ROOT / CONFIG["profile_dir"]
STATE_FILE = ROOT / CONFIG["state_file"]
LOG_FILE = ROOT / CONFIG["log_file"]
SCREENSHOT_DIR = ROOT / CONFIG["screenshot_dir"]

for folder in (
    PROFILE_DIR,
    STATE_FILE.parent,
    LOG_FILE.parent,
    SCREENSHOT_DIR
):
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


# ==================== 页面定位器 ====================

SELECTORS = {
    "new_product_button": "新建产品",
    "manual_entry_text": "手动录入",
    "long_title_placeholder": "请输入产品长标题，4-100字符",
    "short_title_placeholder": "请输入产品短标题，4-24字符",
    "upload_local_text": "本地上传",
    "upload_confirm_text": "确定",
    "submit_button_text": "提交审核",

    # 录制得到的主图上传按钮
    "main_upload_icon":
        ".dls-icon.vi-upload-add-cross-cross-icon",

    # 录制得到的方图上传按钮
    "square_upload_selector": (
        "#sdk-content-oneToOne > .one-col-19 > "
        ".picture-container > .inner-picture > "
        ".common-image-uploader > "
        ".vi.vi-ccsdk-image-upload > "
        ".vi.vi-image-upload-anchor > "
        ".vi.vi-upload-anchor > .vi"
    ),

    # 录制得到的详情编辑区域
    "detail_editor_selector": ".fr-element",

    "file_input_selector": "input[type=file]"
}

KNOWN_POPUP_TEXTS = [
    "知道了",
    "稍后再说",
    "跳过"
]

SUCCESS_TEXTS = [
    "提交成功",
    "审核中",
    "已提交审核",
    "审核提交成功"
]


# ==================== JSON及状态处理 ====================

def save_json_atomic(path: Path, data: Any):
    """原子写入，防止任务中断导致状态文件损坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(path.suffix + ".tmp")

    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    temp_path.replace(path)


def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        logger.warning("任务状态文件异常，使用空状态")
        return {}


def screenshot(page, name: str):
    safe_name = re.sub(
        r"[^\w\-\.\u4e00-\u9fff]",
        "_",
        name
    )

    path = SCREENSHOT_DIR / (
        f"{safe_name}_{int(time.time())}.png"
    )

    try:
        page.screenshot(
            path=str(path),
            full_page=True
        )
        logger.info("截图保存：%s", path)
    except Exception as exc:
        logger.warning("截图失败：%s", exc)

    return path


# ==================== 页面通用操作 ====================

def wait_page_ready(page):
    try:
        page.wait_for_load_state(
            "domcontentloaded",
            timeout=CONFIG["wait_timeout"]
        )
    except PlaywrightTimeoutError:
        pass


def wait_visible(locator, timeout=None):
    locator.wait_for(
        state="visible",
        timeout=timeout or CONFIG["wait_timeout"]
    )
    return locator


def click_text(page, text, exact=True, timeout=None):
    locator = page.get_by_text(
        text,
        exact=exact
    )

    wait_visible(locator, timeout)

    locator.first.click(
        timeout=timeout or CONFIG["wait_timeout"]
    )


def click_button(page, text, timeout=None):
    locator = page.get_by_role(
        "button",
        name=text,
        exact=True
    )

    wait_visible(locator, timeout)

    locator.first.click(
        timeout=timeout or CONFIG["wait_timeout"]
    )


def safe_dismiss_known_popup(page):
    """只关闭已经明确识别的提示弹窗。"""

    for text in KNOWN_POPUP_TEXTS:
        try:
            locator = page.get_by_text(
                text,
                exact=True
            )

            for i in range(min(locator.count(), 3)):
                item = locator.nth(i)

                try:
                    if item.is_visible():
                        item.click(timeout=1500)
                        logger.info(
                            "关闭已知弹窗：%s",
                            text
                        )
                        page.wait_for_timeout(300)
                        break
                except Exception:
                    continue

        except Exception:
            continue


# ==================== 加载产品数据 ====================

def get_product_dirs():
    if not PRODUCT_DIR.exists():
        logger.error(
            "产品目录不存在：%s",
            PRODUCT_DIR
        )
        return []

    return sorted(
        [
            path
            for path in PRODUCT_DIR.iterdir()
            if path.is_dir()
        ]
    )


def load_products():
    tasks = []

    for folder in get_product_dirs():

        json_path = folder / "product.json"

        if not json_path.exists():
            logger.warning(
                "缺少 product.json，跳过：%s",
                folder.name
            )
            continue

        try:
            data = json.loads(
                json_path.read_text(
                    encoding="utf-8"
                )
            )
        except Exception as exc:
            logger.error(
                "JSON读取失败 %s：%s",
                folder.name,
                exc
            )
            continue

        data["folder"] = folder
        data["product_name"] = data.get(
            "product_name",
            folder.name
        )

        data["long_title"] = str(
            data.get("long_title", "")
        ).strip()

        data["short_title"] = str(
            data.get("short_title", "")
        ).strip()

        data["tags"] = data.get("tags", [])
        data["images"] = data.get("images", {})

        # 必填字段检查
        missing_fields = []

        if not data["long_title"]:
            missing_fields.append("long_title")

        if not data["short_title"]:
            missing_fields.append("short_title")

        if missing_fields:
            logger.error(
                "%s 缺少字段：%s",
                folder.name,
                missing_fields
            )
            continue

        # 图片检查
        image_paths = {}
        image_errors = []

        for key in ["main", "square", "detail"]:

            image_name = data["images"].get(key)

            if not image_name:
                image_errors.append(
                    f"images.{key} 未配置"
                )
                continue

            image_path = folder / image_name

            if not image_path.exists():
                image_errors.append(
                    f"图片不存在：{image_path}"
                )
                continue

            image_paths[key] = image_path

        if image_errors:
            logger.error(
                "%s 图片检查失败：%s",
                folder.name,
                "；".join(image_errors)
            )
            continue

        data["image_paths"] = image_paths

        # 分类步骤配置
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


# ==================== 图片上传 ====================

def upload_image(page, image_path: Path, label: str):

    logger.info(
        "开始上传%s：%s",
        label,
        image_path.name
    )

    # 点击本地上传
    click_text(
        page,
        SELECTORS["upload_local_text"],
        exact=True
    )

    # 找文件输入框
    file_input = page.locator(
        SELECTORS["file_input_selector"]
    )

    # 设置文件
    file_input.last.set_input_files(
        str(image_path),
        timeout=CONFIG["wait_timeout"]
    )

    page.wait_for_timeout(700)

    # 查找可见的确定按钮
    confirm = page.get_by_role(
        "button",
        name=SELECTORS["upload_confirm_text"],
        exact=True
    )

    visible_buttons = []

    for i in range(confirm.count()):

        try:
            if confirm.nth(i).is_visible():
                visible_buttons.append(
                    confirm.nth(i)
                )
        except Exception:
            continue

    # 优先点击最后一个可见确定按钮
    if visible_buttons:

        visible_buttons[-1].click(
            timeout=CONFIG["wait_timeout"]
        )

        page.wait_for_timeout(400)

        # 如果仍有确定按钮，再尝试点击一次
        remaining = []

        for i in range(confirm.count()):
            try:
                if confirm.nth(i).is_visible():
                    remaining.append(
                        confirm.nth(i)
                    )
            except Exception:
                continue

        if remaining:
            try:
                remaining[0].click(timeout=2500)
                page.wait_for_timeout(400)
            except Exception:
                pass

    else:
        logger.warning(
            "%s 上传确认按钮未找到，请检查页面",
            label
        )

    logger.info("%s上传操作完成", label)


# ==================== 分类选择 ====================

def fill_category(page, category_clicks):

    for step, item in enumerate(
        category_clicks,
        start=1
    ):

        text = item.get("text", "其他")
        index = int(item.get("index", 0))

        locator = page.get_by_text(
            text,
            exact=True
        )

        wait_visible(locator)

        if locator.count() <= index:
            raise RuntimeError(
                f"分类第{step}步失败："
                f"‘{text}’数量不足，无法取第{index}个"
            )

        logger.info(
            "分类步骤%d：%s，index=%d",
            step,
            text,
            index
        )

        locator.nth(index).click(
            timeout=CONFIG["wait_timeout"]
        )

        page.wait_for_timeout(300)


# ==================== 标签选择 ====================

def fill_tags(page, tags):

    for tag in tags:

        locator = page.get_by_text(
            tag,
            exact=True
        )

        if locator.count() == 0:
            logger.warning(
                "未找到标签，跳过：%s",
                tag
            )
            continue

        locator.last.click(
            timeout=CONFIG["wait_timeout"]
        )

        page.wait_for_timeout(200)

        logger.info("已选择标签：%s", tag)


# ==================== 产品基础信息填写 ====================

def fill_product_basic_info(page, product):

    long_title = page.get_by_placeholder(
        SELECTORS["long_title_placeholder"],
        exact=True
    )

    short_title = page.get_by_placeholder(
        SELECTORS["short_title_placeholder"],
        exact=True
    )

    wait_visible(long_title)

    long_title.fill(
        product["long_title"]
    )

    short_title.fill(
        product["short_title"]
    )

    logger.info(
        "标题填写完成：%s",
        product["product_name"]
    )

    fill_tags(
        page,
        product.get("tags", [])
    )


# ==================== 创建单个产品 ====================

def create_product(page, product):

    product_name = product["product_name"]

    submitted_or_started = False

    try:

        safe_dismiss_known_popup(page)

        # 1. 新建产品
        click_button(
            page,
            SELECTORS["new_product_button"]
        )

        page.wait_for_timeout(500)

        # 2. 手动录入
        click_text(
            page,
            SELECTORS["manual_entry_text"],
            exact=True
        )

        page.wait_for_timeout(500)

        # 3. 产品分类
        fill_category(
            page,
            product.get("category_clicks", [])
        )

        # 4. 标题、标签
        fill_product_basic_info(
            page,
            product
        )

        # 5. 上传主图
        main_upload = page.locator(
            SELECTORS["main_upload_icon"]
        )

        wait_visible(main_upload)

        main_upload.first.click()

        upload_image(
            page,
            product["image_paths"]["main"],
            "主图"
        )

        # 6. 上传方图
        square_upload = page.locator(
            SELECTORS["square_upload_selector"]
        )

        wait_visible(square_upload)

        square_upload.first.click()

        upload_image(
            page,
            product["image_paths"]["square"],
            "方图"
        )

        # 7. 上传详情图
        detail_editor = page.locator(
            SELECTORS["detail_editor_selector"]
        )

        wait_visible(detail_editor)

        detail_editor.first.click()

        upload_image(
            page,
            product["image_paths"]["detail"],
            "详情图"
        )

        logger.info(
            "产品填写完成：%s",
            product_name
        )

        screenshot(
            page,
            f"{product_name}_提交前"
        )

        # 暂不提交审核
        if not CONFIG.get("submit_review", False):

            logger.info(
                "submit_review=false，"
                "本次仅完成填写和上传：%s",
                product_name
            )

            return "needs_review"

        # 8. 提交审核
        submit_btn = page.get_by_role(
            "button",
            name=SELECTORS["submit_button_text"],
            exact=True
        )

        wait_visible(submit_btn)

        # 只点击一次提交按钮
        submit_btn.first.click(
            timeout=CONFIG["wait_timeout"]
        )

        submitted_or_started = True

        page.wait_for_timeout(1000)

        # 9. 处理提交确认弹窗
        dialog = page.get_by_role("dialog")

        try:

            if (
                dialog.count() > 0
                and dialog.first.is_visible()
            ):

                confirm_button = (
                    dialog.first.get_by_role(
                        "button",
                        name=SELECTORS[
                            "submit_button_text"
                        ],
                        exact=True
                    )
                )

                if confirm_button.count() > 0:

                    if confirm_button.first.is_visible():

                        confirm_button.first.click(
                            timeout=4000
                        )

                        page.wait_for_timeout(1000)

        except Exception as exc:

            logger.info(
                "提交确认弹窗处理异常：%s",
                exc
            )

        # 10. 检查是否明确提交成功
        body_text = ""

        try:
            body_text = page.locator(
                "body"
            ).inner_text(timeout=3000)
        except Exception:
            pass

        if any(
            text in body_text
            for text in SUCCESS_TEXTS
        ):

            screenshot(
                page,
                f"{product_name}_提交成功"
            )

            logger.info(
                "检测到成功提示：%s",
                product_name
            )

            return "success"

        # 无法确定时不重复提交
        screenshot(
            page,
            f"{product_name}_提交状态待确认"
        )

        logger.warning(
            "提交动作执行，但无法确定状态：%s",
            product_name
        )

        return "needs_review"

    except Exception as exc:

        logger.exception(
            "产品创建失败：%s",
            product_name
        )

        screenshot(
            page,
            f"{product_name}_失败"
        )

        # 提交过或提交状态未知，不自动重试
        if submitted_or_started:
            return "needs_review"

        return "failed"


# ==================== 批量任务调度 ====================

def run_product_tasks(page):

    products = load_products()

    if not products:
        logger.warning(
            "没有有效产品，请检查 products 目录"
        )
        return

    state = load_state()

    logger.info(
        "本次有效产品数：%d",
        len(products)
    )

    for product in products:

        name = product["product_name"]

        task_key = str(
            product["folder"].relative_to(ROOT)
        )

        old_status = state.get(
            task_key,
            {}
        ).get("status")

        # 已成功的产品不重复创建
        if (
            CONFIG.get("skip_success", True)
            and old_status == "success"
        ):

            logger.info(
                "跳过已完成产品：%s",
                name
            )

            continue

        # 待人工确认的产品不重复创建
        if old_status == "needs_review":

            logger.warning(
                "跳过待确认产品：%s",
                name
            )

            continue

        retries = int(
            CONFIG.get("max_retries", 0)
        )

        attempt = 0

        status = "failed"

        while attempt <= retries:

            attempt += 1

            logger.info(
                "执行产品 %s，第%d/%d次",
                name,
                attempt,
                retries + 1
            )

            status = create_product(
                page,
                product
            )

            if status != "failed":
                break

            # 只对明确的提交前失败进行有限重试
            if attempt <= retries:

                logger.warning(
                    "产品执行失败，准备重试：%s",
                    name
                )

                safe_dismiss_known_popup(page)

                page.wait_for_timeout(1500)

        # 记录状态
        state[task_key] = {
            "product_name": name,
            "status": status,
            "attempts": attempt,
            "updated_at": time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "folder": task_key
        }

        save_json_atomic(
            STATE_FILE,
            state
        )

        logger.info(
            "产品任务结束：%s -> %s",
            name,
            status
        )

        # 失败或不确定时暂停整个批次
        if status in (
            "failed",
            "needs_review"
        ):

            logger.warning(
                "任务需人工检查，暂停后续产品"
            )

            break

        page.wait_for_timeout(1200)


# ==================== 主程序 ====================

def main():

    print("=" * 60)
    print("百度基木鱼产品录入自动化")
    print("=" * 60)

    print(
        "是否提交审核：",
        CONFIG.get("submit_review", False)
    )

    with sync_playwright() as p:

        # 使用持久化浏览器目录，保存登录状态
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=CONFIG.get("headless", False),
            viewport={
                "width": 1440,
                "height": 1000
            },
            accept_downloads=True
        )

        page = (
            context.pages[0]
            if context.pages
            else context.new_page()
        )

        page.goto(
            CONFIG.get(
                "base_url",
                "https://www2.baidu.com/"
            ),
            wait_until="domcontentloaded"
        )

        wait_page_ready(page)

        print("\n请在浏览器完成以下步骤：")
        print("1. 手动登录百度账号")
        print("2. 进入基木鱼")
        print("3. 进入内容中心/产品管理")
        print("4. 确保当前页面可以看到新建产品按钮")
        print("5. 关闭不需要的弹窗")
        print()

        input(
            "准备完成后，回到终端按 Enter 开始……"
        )

        # 获取当前最后一个页面
        pages = context.pages

        page = (
            pages[-1]
            if pages
            else page
        )

        try:

            run_product_tasks(page)

        except KeyboardInterrupt:

            logger.warning(
                "用户主动中断任务"
            )

        except Exception:

            logger.exception(
                "批量任务异常"
            )

            screenshot(
                page,
                "批量任务异常"
            )

        finally:

            print(
                "\n任务结束，5秒后关闭浏览器。"
            )

            page.wait_for_timeout(5000)

            context.close()


if __name__ == "__main__":
    main()
