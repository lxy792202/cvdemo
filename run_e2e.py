"""资源包 -> 图片流水线 -> 上传数据桥 -> 浏览器上传 的一键全链路编排。

用法（在 demo 目录下）：
    cv/bin/python run_e2e.py                      # 全链路；上传阶段只传 1 个产品（先跑通）
    cv/bin/python run_e2e.py --all-products       # 链路验证后：传完所有待传产品
    cv/bin/python run_e2e.py --skip-pipeline      # 复用已有 output/，只跑桥接+上传
    cv/bin/python run_e2e.py --from-stage 3       # 流水线从选图阶段开始
    cv/bin/python run_e2e.py --rerun-success      # 商户/建站即使已成功也重跑（演示）

阶段说明：
    1) run_pipeline.py 1->4   图片逐张处理 / 主图 / 选图 / 设计图+PSD（幂等，已有产物跳过）
    2) tools/build_data.py    把 output/ 适配成上传器索引（products/company/h5_site）
    3) baidu_auto_plus/main.py --task all --yes
                              一次浏览器会话：产品 -> 商户 -> H5 建站
任一步退出码非 0 即中断，不会带着坏数据继续上传。
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = ROOT / "cv" / "bin" / "python"


def run(cmd, step):
    print(f"\n{'=' * 62}\n[e2e] {step}\n[e2e] 命令：{' '.join(cmd)}\n{'=' * 62}", flush=True)
    rc = subprocess.call([str(PY), "-u", *cmd], cwd=str(ROOT))
    if rc != 0:
        raise SystemExit(f"[e2e] 「{step}」失败（exit {rc}），链路中断")
    print(f"[e2e] 「{step}」完成", flush=True)


def main():
    ap = argparse.ArgumentParser(description="资源包到平台上传的全链路编排")
    ap.add_argument("--from-stage", type=int, default=1, choices=[1, 2, 3, 4],
                    help="图片流水线起始阶段（默认 1）")
    ap.add_argument("--force-pipeline", action="store_true",
                    help="流水线忽略已有产物重跑（慢且耗 API）")
    ap.add_argument("--skip-pipeline", action="store_true",
                    help="跳过流水线，直接复用 output/ 既有产物")
    ap.add_argument("--products", type=int, default=1,
                    help="上传阶段传几个待传产品（默认 1，先跑通）")
    ap.add_argument("--all-products", action="store_true",
                    help="上传所有待传产品（覆盖 --products）")
    ap.add_argument("--rerun-success", action="store_true",
                    help="商户/建站已成功也重跑（整链路演示用）")
    args = ap.parse_args()

    if not PY.exists():
        raise SystemExit(f"缺少虚拟环境解释器：{PY}")

    if not args.skip_pipeline:
        cmd = ["run_pipeline.py", "--from", str(args.from_stage)]
        if args.force_pipeline:
            cmd.append("--force")
        run(cmd, f"图片流水线 Stage {args.from_stage}->4")
    else:
        print("[e2e] 已跳过图片流水线，复用 output/ 既有产物", flush=True)

    run(["baidu_auto_plus/tools/build_data.py"], "生成上传索引数据 build_data")

    up = ["baidu_auto_plus/main.py", "--task", "all", "--yes"]
    if args.all_products:
        up += ["--product-limit", "0"]   # 0 = 不限，传完所有待传产品
    else:
        up += ["--product-limit", str(args.products)]
    if args.rerun_success:
        up.append("--rerun-success")
    run(up, "浏览器上传：产品 -> 商户 -> H5 建站")

    print("\n[e2e] 全链路执行完毕。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
