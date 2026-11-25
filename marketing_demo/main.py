"""Stage 4 入口：根据图片、企业文档和设计规范生成营销设计图。

前置条件：
- Stage 2 主图：output/02_main_images/
- Stage 3 选图：marketing_demo/output/selected_materials/selection_manifest.json

用法（在 demo 目录下）：
    cv/bin/python marketing_demo/main.py --dry-run
    cv/bin/python marketing_demo/main.py
    cv/bin/python marketing_demo/main.py --only logo product_main_假山
"""

import argparse
import json
import sys
from pathlib import Path

# 项目根目录 demo
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline import stage4_design  # noqa: E402


def _parse_args():
    parser = argparse.ArgumentParser(
        description="生成营销设计图（通用模块，支持单产品/多产品/纯文字）"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="只生成计划，不调用生成接口")
    parser.add_argument("--force", action="store_true",
                        help="忽略已有产物重新生成")
    parser.add_argument("--only", nargs="*", default=None,
                        help="只处理指定 module_id 或名称")
    parser.add_argument("--notes", default="",
                        help="补充设计要求（可选，替代旧 design.txt）")
    return parser.parse_args()


def main():
    args = _parse_args()

    result = stage4_design.run(
        dry_run=args.dry_run,
        force=args.force,
        only=args.only,
        design_notes=args.notes,
    )

    if args.dry_run:
        print("\n计划预览：")
        print(json.dumps(result["plan"], ensure_ascii=False, indent=2)[:4000])
    else:
        success = sum(1 for r in result["results"] if r.get("success"))
        print(f"\n成功 {success}/{len(result['results'])} 个设计项")

    return result


if __name__ == "__main__":
    main()
