"""H5 落地页建站：逐个导入 PSD 模块。

Mechanism confirmed by the 2026-11 Codegen recording -- H5 建站 is neither
drag-and-drop nor plain image upload. It is repeated PSD module import:

    我的站点 -> 新建站点 -> 新建空白站点 -> 关闭（引导层）
    对每个 PSD 重复：
        模块导入psd内容 -> 选择PSD文件 -> input[name="file"] <- .psd -> 等解析成功
    收尾（全部模块导入完之后才点一次顶栏「提交」）：
        保存 -> 提交 -> 发布站点对话框填站点名称 -> 确定
        （确定会触发原生 beforeunload，需要 dismiss）

用户确认：单个 PSD 解析成功后模块即落到画布，中途不能点「提交」；
中途点顶栏「提交」会弹「发布站点」对话框，取消后已解析的模块会丢失。

The module list and its order come from data/h5_site.json, which
tools/build_data.py generates from output/04_plan.json (region=落地页).

Note the file input is `input[name="file"]`, NOT `input[type="file"]` -- the PSD
picker is a different widget from the image uploader, so common.upload_local_file
is deliberately not reused here.

The recording also contained layer-reordering steps (tab「图层」+ 上移, and
「在下方插入空白模块」). Their required counts depend on the PSD combination, so
they are opt-in per module via data/h5_site.json -> modules[].layer_ops:
    {"insert_below": true, "move_up": 1}
"""

from pathlib import Path
import json
import time

from common import find_upload_error, safe_close_known_popups
from state_manager import load_state, set_task
from agent_handler import AgentExceptionHandler

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_FILE = ROOT / "data" / "h5_site.json"

DEFAULT_SELECTORS = {
    "menu_my_sites": "我的站点",
    "new_site": "新建站点",
    "new_blank_site": "新建空白站点",
    "import_psd": "模块导入psd内容",
    "choose_psd": "选择PSD文件",
    "file_input": 'input[name="file"]',
    "module_submit": "提交",
    "save": "保存",
    "result_card_close": ".card-header > .el-icon-close",
    "guide_close": "关闭",
    "dialog_close": ".one-button.one-dialog-close",
    "confirm": "确定",
    # The recording shows both a full-width and a half-width comma variant.
    "site_name_placeholders": [
        "请输入站点名称，最多30个字符",
        "请输入站点名称,最多30个字符",
    ],
    "layer_tab": "图层",
    "move_up": [
        ".dls-icon.m-captain-icon.m-captain-icon-move-up",
        ".layer-operate-move-up > .dls-icon",
    ],
    "insert_below_img": "在下方插入空白模块",
    # 现版编辑器加号是 operation 层 div（录制时代的 img[alt] 已不存在）
    "insert_plus": "div.operation.add-module-after",
}

SUCCESS_TEXTS = ["提交成功", "保存成功", "创建成功", "审核中", "发布成功"]


def load_manifest(logger):
    if not MANIFEST_FILE.exists():
        raise RuntimeError(
            f"缺少 H5 站点清单：{MANIFEST_FILE}；请先运行 tools/build_data.py"
        )
    data = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    modules = data.get("modules", [])
    missing = [m["module_id"] for m in modules if not Path(m["psd"]).exists()]
    if missing:
        raise RuntimeError(f"以下模块的 PSD 文件不存在：{missing}；请重跑 pipeline 或 build_data.py")
    if not modules:
        raise RuntimeError("h5_site.json 的 modules 为空，没有可导入的 PSD")
    logger.info("H5 站点「%s」待导入 %s 个模块", data.get("site_name"), len(modules))
    return data


def _click_button(page, name, timeout, logger, step=""):
    loc = page.get_by_role("button", name=name, exact=True)
    if not loc.count():
        raise RuntimeError(f"{step}找不到按钮「{name}」；如页面改版请更新 config.json 的 h5_selectors")
    loc.first.click(timeout=timeout)
    page.wait_for_timeout(800)
    logger.info("%s已点击「%s」", step, name)


def _clear_overlays(page, sel, timeout, logger):
    """Clear guide layers left over from 新建站点 / PSD 导入."""
    safe_close_known_popups(page, logger)
    for key in ("dialog_close", "guide_close"):
        selector = sel.get(key)
        if not selector:
            continue
        try:
            loc = (page.locator(selector) if selector.startswith(".")
                   else page.get_by_role("button", name=selector, exact=True))
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=2000)
                page.wait_for_timeout(500)
                logger.info("关闭遮挡层（%s）", key)
        except Exception as exc:
            logger.info("遮挡层 %s 未出现或不可点：%s", key, exc)


def _apply_layer_ops(page, sel, ops, timeout, logger, module_id):
    if not ops:
        return
    if ops.get("insert_below"):
        img = page.get_by_role("img", description=sel["insert_below_img"], exact=True)
        if img.count():
            img.first.click(timeout=timeout)
            page.wait_for_timeout(600)
            logger.info("%s：已在下方插入空白模块", module_id)
    moves = int(ops.get("move_up", 0))
    if moves:
        tab = page.get_by_role("tab", name=sel["layer_tab"])
        if not tab.count():
            logger.warning("%s：找不到「%s」标签页，跳过图层上移", module_id, sel["layer_tab"])
            return
        tab.first.click(timeout=timeout)
        page.wait_for_timeout(600)
        for i in range(moves):
            clicked = False
            for selector in sel["move_up"]:
                loc = page.locator(selector)
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=timeout)
                    clicked = True
                    break
            if not clicked:
                logger.warning("%s：第 %s 次图层上移找不到按钮", module_id, i + 1)
                break
            page.wait_for_timeout(400)
        logger.info("%s：图层上移 %s 次", module_id, moves)


def _dismiss_publish_dialog(page, logger, step=""):
    """误点顶栏「提交」会弹「发布站点」对话框；批量导入期间必须取消掉。"""
    dlg = page.locator(".el-dialog__wrapper").filter(has_text="发布站点")
    try:
        if dlg.count() and dlg.first.is_visible():
            dlg.first.get_by_role("button", name="取消", exact=True).first.click(timeout=3000)
            page.wait_for_timeout(800)
            logger.info("%s检测到误开的「发布站点」对话框，已取消", step)
            return True
    except Exception as exc:
        logger.info("%s关闭发布站点对话框失败：%s", step, str(exc).splitlines()[0])
    return False


PARSE_STATE_JS = ("() => ({imp: document.body.innerText.includes('导入中'),"
                  " ok: document.body.innerText.includes('解析成功')})")


def _wait_psd_parsed(page, module_id, seconds):
    """等本轮 PSD 解析结束。

    成功信号（用户确认 + 实测）：
    - 「psd文件解析成功」toast；或
    - 「psd 文件导入中/取消导入」出现后又连续 3s 消失（文案会闪烁，
      短暂消失不算结束；解析成功 toast 也不是每次都弹，不能只认它）。
    解析结束后面板收起，才允许导入下一个模块。
    """
    deadline = time.monotonic() + seconds

    def state():
        try:
            return page.evaluate(PARSE_STATE_JS)
        except Exception:
            return {"imp": False, "ok": False}

    seen_importing = False
    quiet = 0.0
    while time.monotonic() < deadline:
        st = state()
        if st.get("ok"):
            page.wait_for_timeout(800)
            return
        if st.get("imp"):
            seen_importing = True
            quiet = 0.0
        else:
            quiet += 0.5
            if seen_importing and quiet >= 3.0:
                page.wait_for_timeout(800)
                return
        page.wait_for_timeout(500)
    if not seen_importing:
        raise RuntimeError(f"{module_id}：设置 PSD 后 {seconds}s 内未出现解析中提示，导入可能未启动")
    raise RuntimeError(f"{module_id}：PSD 解析超过 {seconds}s 仍未结束")


CLOSE_QUALITY_JS = """() => {
  const nodes = [...document.querySelectorAll("div")].filter(
    el => el.innerText && el.innerText.trim().startsWith("修改以下问题后提交"));
  if (!nodes.length) return false;
  let panel = nodes.sort((a, b) => a.innerText.length - b.innerText.length)[0];
  for (let k = 0; k < 4 && panel; k++) {
    const closer = [...panel.querySelectorAll("[class*='close'], i, svg")].pop();
    if (closer) { closer.click(); return true; }
    panel = panel.parentElement;
  }
  return false;
}"""


def _close_quality_panel(page, logger):
    """顶栏「提交」被质检面板拦住时（修改以下问题后提交(n)），关掉面板再试。"""
    try:
        if page.evaluate(CLOSE_QUALITY_JS):
            page.wait_for_timeout(600)
            logger.info("已关闭「修改以下问题后提交」质检面板")
            return True
    except Exception as exc:
        logger.info("关闭质检面板失败：%s", str(exc).splitlines()[0])
    return False


def _click_insert_below_last(page, sel, timeout, logger, module_id):
    """点上一个模块底边右侧的「+」（add-module-after）锁定插入位置。

    用户确认：不点加号直接导入，PSD 会覆盖当前模块而不是追加到末尾。
    画布懒渲染：先滚到底，让最后一个内容模块进入 DOM；加号只在模块
    选中/悬停时出现，不出现就用原生鼠标点模块中心选中它。
    """
    canvas = page.locator(".canvas-module_rendering-layer")
    try:
        box = canvas.first.bounding_box(timeout=5000)
        if box:
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            for _ in range(6):
                page.mouse.wheel(0, 1500)
                page.wait_for_timeout(250)
    except Exception as exc:
        logger.info("%s 画布滚动失败（忽略）：%s", module_id, str(exc).splitlines()[0])
    plus = page.locator(sel["insert_plus"])
    wraps = page.locator(".canvas-module_module-wrapper_common")
    for attempt in range(3):
        try:
            if plus.count() and plus.last.is_visible():
                plus.last.click(timeout=5000)
                page.wait_for_timeout(600)
                logger.info("%s 已点上一模块下方的「+」定位插入点", module_id)
                return True
        except Exception:
            pass
        # 加号没出现：原生鼠标点最后一个内容模块中心使其选中
        n = wraps.count()
        target = wraps.nth(n - 2) if n >= 2 else (wraps.first if n else None)
        try:
            if target is not None:
                b = target.bounding_box(timeout=3000)
                if b:
                    page.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
                    page.wait_for_timeout(600)
        except Exception:
            pass
        page.wait_for_timeout(500)
    logger.warning("%s 未找到加号（%s），直接按当前位置导入",
                   module_id, sel["insert_plus"])
    return False


def _import_one_psd(page, sel, module, timeout, logger, insert_before=False):
    module_id = module["module_id"]
    psd = Path(module["psd"])
    logger.info("导入 PSD 模块 %s（%s）：%s", module_id, module.get("name"), psd.name)

    _clear_overlays(page, sel, timeout, logger)
    if insert_before:
        _click_insert_below_last(page, sel, timeout, logger, module_id)
    _click_button(page, sel["import_psd"], timeout, logger, step=f"{module_id} ")
    file_input = page.locator(sel["file_input"])
    for _ in range(10):
        if file_input.count():
            break
        page.wait_for_timeout(1000)   # 面板可能还在收起动画/上一轮解析收尾
    if file_input.count():
        # 直接设文件：点「选择PSD文件」按钮会唤起 macOS 原生 Finder 并残留在桌面
        file_input.first.set_input_files(str(psd), timeout=timeout)
        logger.info("%s 已直接设置 PSD 文件（未点选择按钮，避免唤起 Finder）", module_id)
    else:
        with page.expect_file_chooser(timeout=timeout) as fc_info:
            _click_button(page, sel["choose_psd"], timeout, logger, step=f"{module_id} ")
        fc_info.value.set_files(str(psd))
        logger.info("%s 已拦截系统文件选择器并设置 PSD", module_id)

    # PSD 解析是异步的：等「解析成功」提示出现，模块才算落到画布。
    _wait_psd_parsed(page, module_id, 120)
    error = find_upload_error(page)
    if error:
        raise RuntimeError(f"{module_id}：PSD 导入被拒绝：{error}")

    _apply_layer_ops(page, sel, module.get("layer_ops"), timeout, logger, module_id)
    # 用户确认：单个模块解析完不点「提交」，全部导入完才在收尾点一次顶栏提交。

    # The recording closes an element-ui result card after each import.
    card_close = page.locator(sel["result_card_close"])
    if card_close.count() and card_close.first.is_visible():
        try:
            card_close.first.click(timeout=2000)
            page.wait_for_timeout(500)
        except Exception:
            pass
    _clear_overlays(page, sel, timeout, logger)
    logger.info("%s：导入完成", module_id)


def _set_site_name(page, sel, site_name, timeout, logger):
    for placeholder in sel["site_name_placeholders"]:
        box = page.get_by_role("textbox", name=placeholder)
        if box.count() and box.first.is_visible():
            box.first.click(timeout=timeout)
            box.first.fill(site_name[:30], timeout=timeout)
            logger.info("站点名称已填写：%s", site_name[:30])
            return True
    logger.warning("未找到站点名称输入框，尝试过的 placeholder：%s", sel["site_name_placeholders"])
    return False


def run_h5_site_task(page, config, logger):
    sel = {**DEFAULT_SELECTORS, **config.get("h5_selectors", {})}
    timeout = config.get("wait_timeout", 15000)
    submitted = False

    try:
        manifest = load_manifest(logger)
    except RuntimeError as exc:
        logger.warning("%s", exc)
        return {"status": "not_configured", "detail": str(exc)}

    site_name = manifest.get("site_name") or "落地页"
    state = load_state(config)
    key = f"h5::{site_name}"
    previous = state.get(key, {})
    if config.get("skip_success", True) and previous.get("status") == "success":
        logger.info("跳过已成功的 H5 站点：%s", site_name)
        return {"status": "skipped", "detail": "此前已成功"}

    imported = []
    try:
        safe_close_known_popups(page, logger)
        if config.get("h5_navigate_menu", True):
            loc = page.get_by_text("基木鱼", exact=True)
            jimuyu = None
            for i in range(loc.count()):
                try:
                    if loc.nth(i).is_visible():
                        jimuyu = loc.nth(i)
                        break
                except Exception:
                    continue
            if jimuyu is not None:
                before = len(page.context.pages)
                jimuyu.click(timeout=timeout)
                page.wait_for_timeout(2500)
                if len(page.context.pages) > before:
                    page = page.context.pages[-1]
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                _clear_overlays(page, sel, timeout, logger)
                logger.info("已进入基木鱼首页：%s", page.url)
            menu = page.get_by_text(sel["menu_my_sites"], exact=True)
            if menu.count() and menu.first.is_visible():
                menu.first.click(timeout=timeout)
                page.wait_for_timeout(1500)
                logger.info("已进入「我的站点」")

        _click_button(page, sel["new_site"], timeout, logger)
        # 注意：此处不能清遮罩——弹出来的正是「选建站方式」对话框，
        # 清遮罩会把它连 X 一起关掉；只在新建空白站点后清引导层。
        _click_button(page, sel["new_blank_site"], timeout, logger)
        _clear_overlays(page, sel, timeout, logger)
        page.wait_for_timeout(2000)          # 编辑器初始化
        _clear_overlays(page, sel, timeout, logger)  # 新手引导层在初始化后才弹

        for i, module in enumerate(manifest["modules"]):
            _import_one_psd(page, sel, module, timeout, logger, insert_before=i > 0)
            imported.append(module["module_id"])

        logger.info("全部 %s 个 PSD 模块导入完成：%s", len(imported), imported)

        if not config.get("submit_review", True):
            _set_site_name(page, sel, site_name, timeout, logger)
            result = {"status": "success",
                      "detail": f"已导入 {len(imported)} 个模块，配置为不提交"}
        else:
            # 收尾：先保存草稿，再顶栏「提交」->「发布站点」对话框填名称 -> 确定。
            # 质检面板（修改以下问题后提交）可能拦住提交，关掉重试。
            try:
                _click_button(page, sel["save"], timeout, logger, step="收尾")
            except Exception as exc:
                logger.info("收尾保存失败（忽略）：%s", str(exc).splitlines()[0])
            top_submit = page.get_by_role("button", name="提交", exact=True).first
            dlg = page.locator(".el-dialog__wrapper").filter(has_text="发布站点")
            for attempt in range(1, 4):
                top_submit.click(timeout=timeout)
                page.wait_for_timeout(1500)
                if dlg.count() and dlg.first.is_visible():
                    break
                _close_quality_panel(page, logger)
                page.wait_for_timeout(800)
            if dlg.count() and dlg.first.is_visible():
                box = dlg.first.get_by_role("textbox").first
                box.click(timeout=timeout)
                box.fill(site_name[:30], timeout=timeout)
                logger.info("发布站点对话框已填站点名称：%s", site_name[:30])
                # 命名确认会触发原生 beforeunload，录制里用 dismiss 处理
                page.once("dialog", lambda dialog: dialog.dismiss())
                dlg.first.get_by_role("button", name="确定", exact=True).first.click(
                    timeout=timeout
                )
                submitted = True
                page.wait_for_timeout(2500)
            else:
                raise RuntimeError("顶栏提交 3 次后仍未弹出「发布站点」对话框")
            body = page.locator("body").inner_text(timeout=3000)
            if any(text in body for text in SUCCESS_TEXTS):
                result = {"status": "success",
                          "detail": f"已导入 {len(imported)} 个模块并出现成功提示"}
            else:
                result = {"status": "needs_review",
                          "detail": f"已导入 {len(imported)} 个模块并点击确定，"
                                    f"但未检测到明确成功提示，请人工确认"}
        set_task(config, state, key, result["status"], result.get("detail", ""))
        logger.info("H5 任务结束：%s -> %s", site_name, result["status"])
        return result
    except Exception as exc:
        handler = AgentExceptionHandler(page, config, logger)
        detail = f"已导入 {imported} 后失败" if imported else ""
        result = handler.handle(f"h5::{site_name}{(' ' + detail) if detail else ''}",
                                exc, submit_started=submitted)
        result["imported"] = imported
        set_task(config, state, key, result["status"],
                 result.get("detail", result.get("error", "")))
        return result
