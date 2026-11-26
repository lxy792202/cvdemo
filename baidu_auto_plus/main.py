import argparse
from browser import launch_browser
from common import setup_logging
from modules.product_uploader import run_product_tasks
from modules.merchant_uploader import run_merchant_task
from modules.pc_site_creator import run_pc_site_task
from modules.h5_site_creator import run_h5_site_task

ROOT_TASKS = ["product", "merchant", "pc", "h5"]

def main():
    parser = argparse.ArgumentParser(description="百度营销平台模块化自动化 MVP")
    parser.add_argument("--task", choices=ROOT_TASKS + ["all"], default="product")
    args = parser.parse_args()

    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    logger = setup_logging(config)

    pw, context, page = launch_browser(config)
    try:
        page.goto(config["base_url"], wait_until="domcontentloaded")
        print("\\n浏览器已启动。请手动登录并进入对应业务页面。")
        print("产品任务：请进入基木鱼 -> 内容中心 -> 产品管理，确保页面可看到“新建产品”。")
        input("准备好后回到终端按 Enter 继续...")
        selected = ROOT_TASKS if args.task == "all" else [args.task]
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
        print("\\n执行结果：", outputs)
        input("任务结束。检查页面后按 Enter 关闭浏览器...")
    finally:
        context.close()
        pw.stop()

if __name__ == "__main__":
    main()
