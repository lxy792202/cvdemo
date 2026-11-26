import argparse
from browser import launch_browser, pick_target_page
from common import setup_logging
from modules.product_uploader import run_product_tasks
from modules.merchant_uploader import run_merchant_task
from modules.pc_site_creator import run_pc_site_task
from modules.h5_site_creator import run_h5_site_task

ROOT_TASKS = ["product", "merchant", "pc", "h5"]
# 合并跑（--task all）的顺序：用户指定 产品 -> 商户 -> 建站；PC 建站搁置不在内。
ALL_ORDER = ["product", "merchant", "h5"]

TASK_HINTS = {
    "product": "基木鱼 -> 内容中心 -> 产品管理（页面应显示「新建产品」）",
    "merchant": "基木鱼 -> 商家管理 -> 商户信息（页面应显示「编辑」按钮）",
    "pc": "基木鱼 -> 我的站点（PC 建站已搁置，当前不会执行任何操作）",
    "h5": "基木鱼 -> 我的站点（自动新建空白站点并逐个导入 PSD）",
}

def _ensure_platform_home(page, logger):
    """www2 营销首页现在会重定向到 tuiguang oneWeb 门户，菜单全不在那里。

    从任意标签页地址里解析 userid，直接归位到基木鱼平台首页；
    三个任务模块的菜单导航都以平台首页为起点才稳定。
    """
    import re as _re
    if "wutong.baidu.com/platform" in page.url:
        return page
    uid = None
    for _ in range(3):
        for tab in page.context.pages:
            m = _re.search(r"(?:userid|ucUserId)=(\d+)", tab.url) or \
                _re.search(r"/user/(\d+)/", tab.url)
            if m:
                uid = m.group(1)
                break
        if uid:
            break
        page.wait_for_timeout(2000)   # 等 www2 -> oneWeb 的重定向落地
    if not uid:
        logger.warning("解析不到 userid，保持当前起点：%s", page.url)
        return page
    try:
        page.goto(f"https://wutong.baidu.com/platform/user/{uid}/home?ucUserId={uid}",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        logger.info("起点已归位到基木鱼平台首页：%s", page.url)
    except Exception as exc:
        logger.warning("归位平台首页失败（忽略）：%s", str(exc).splitlines()[0])
    return page


def _collect_failed(outputs):
    """编排用：failed/needs_review 都视为未成功，让上层中断链路。"""
    bad = []
    for task, res in outputs.items():
        if isinstance(res, list):
            bad += [f"{task}:{name}" for name, st in res
                    if st in ("failed", "needs_review")]
        elif isinstance(res, dict):
            if res.get("status") in ("failed", "needs_review"):
                bad.append(task)
    return bad


def main():
    parser = argparse.ArgumentParser(description="百度营销平台模块化自动化 MVP")
    parser.add_argument("--task", choices=ROOT_TASKS + ["all"], default="product")
    parser.add_argument("--rerun-success", action="store_true",
                        help="忽略 skip_success，已成功的商户/建站也重跑（用于整链路演示）")
    parser.add_argument("--product-limit", type=int, default=None,
                        help="覆盖 config.json 的 product_limit（0=不限）")
    parser.add_argument("--yes", action="store_true",
                        help="无人值守：不等 Enter 提示（供 run_e2e.py 编排调用）")
    args = parser.parse_args()

    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    if args.rerun_success:
        config["skip_success"] = False
    if args.product_limit is not None:
        config["product_limit"] = args.product_limit or None
    logger = setup_logging(config)

    pw, context, page = launch_browser(config)
    try:
        page.goto(config["base_url"], wait_until="domcontentloaded")
        selected = ALL_ORDER if args.task == "all" else [args.task]
        print("\n浏览器已启动。请手动登录并进入对应业务页面。")
        for task in selected:
            print(f"  {task} 任务：请进入 {TASK_HINTS[task]}")
        if not args.yes:
            input("准备好后回到终端按 Enter 继续...")
        # 用户此时可能已经在 基木鱼 的新标签页里，重新挑选操作目标。
        page = pick_target_page(context, logger)
        page = _ensure_platform_home(page, logger)
        outputs = {}
        for task in selected:
            logger.info("执行任务模块：%s", task)
            if task == "product":
                outputs[task] = run_product_tasks(page, config, logger)
            elif task == "merchant":
                outputs[task] = run_merchant_task(page, config, logger)
            elif task == "pc":
                outputs[task] = run_pc_site_task(page, config, logger)
            elif task == "h5":
                outputs[task] = run_h5_site_task(page, config, logger)
        print("\n执行结果：", outputs)
        failed = _collect_failed(outputs)
        if not args.yes:
            input("任务结束。检查页面后按 Enter 关闭浏览器...")
        if failed:
            raise SystemExit(f"以下任务未成功：{failed}")
    finally:
        context.close()
        pw.stop()

if __name__ == "__main__":
    main()
