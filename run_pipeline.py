"""营销设计图全链路编排。

用法（在 demo 目录下）：
    cv/bin/python run_pipeline.py                 # 全链路 1 -> 4
    cv/bin/python run_pipeline.py --from 3        # 从选图开始
    cv/bin/python run_pipeline.py --from 4 --dry-run
    cv/bin/python run_pipeline.py --force --only logo

阶段：
    1 图片逐张处理（水印/优化/换背景）
    2 每类产品最优主图（4:3 + 1:1）
    3 按 design.json 选图
    4 生成营销设计图
"""

import argparse
import sys

from pipeline import common


def stage1(force=False, limit=None):
    from pipeline import stage1_process
    return stage1_process.run(force=force, limit=limit)


def stage2(force=False):
    from pipeline import stage2_main_images
    return stage2_main_images.run(force=force)


def stage3(force=False):
    from marketing_demo import select_materials
    return select_materials.main(force=force)


def stage4(force=False, dry_run=False, only=None, notes=""):
    from pipeline import stage4_design
    return stage4_design.run(
        dry_run=dry_run, force=force, only=only, design_notes=notes
    )


def _parse_args():
    parser = argparse.ArgumentParser(description="营销设计图全链路")
    parser.add_argument("--from", dest="start", type=int, default=1,
                        choices=[1, 2, 3, 4], help="起始阶段")
    parser.add_argument("--to", dest="end", type=int, default=4,
                        choices=[1, 2, 3, 4], help="结束阶段")
    parser.add_argument("--force", action="store_true",
                        help="忽略已有产物重新执行")
    parser.add_argument("--dry-run", action="store_true",
                        help="Stage 4 只生成计划，不调用生成接口")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stage 1 只处理前 N 张图片")
    parser.add_argument("--only", nargs="*", default=None,
                        help="Stage 4 只处理指定模块（module_id 或名称）")
    parser.add_argument("--notes", default="",
                        help="补充设计要求（可选）")
    return parser.parse_args()


def main():
    args = _parse_args()

    if args.start > args.end:
        raise SystemExit("--from 不能大于 --to")

    if not common.API_KEY:
        raise SystemExit("config.py 中没有配置 API_KEY")

    print("=" * 60)
    print(
        f"营销设计图流水线：Stage {args.start} -> {args.end}"
        f"{'（dry-run）' if args.dry_run else ''}"
    )
    print("=" * 60)

    if args.start <= 1 <= args.end:
        stage1(force=args.force, limit=args.limit)

    if args.start <= 2 <= args.end:
        stage2(force=args.force)

    if args.start <= 3 <= args.end:
        stage3(force=args.force)

    if args.start <= 4 <= args.end:
        stage4(
            force=args.force,
            dry_run=args.dry_run,
            only=args.only,
            notes=args.notes,
        )

    print("\n全部阶段执行结束。")
    print(f"图片处理：{common.PROCESSED_DIR}")
    print(f"主图目录：{common.MAIN_IMAGE_DIR}")
    print(f"选图目录：{common.SELECTED_MATERIALS_DIR}")
    print(f"设计图目录：{common.DESIGN_DIR}")


if __name__ == "__main__":
    sys.exit(main())
