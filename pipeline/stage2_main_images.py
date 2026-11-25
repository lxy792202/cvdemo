"""Stage 2：每类产品选出最优主图，输出 4:3 与 1:1 两版。"""

from datetime import datetime
from pathlib import Path

from pipeline import common
from pipeline.stage1_process import load_processed


def _category_images(processed_images):
    groups = {}
    for img in processed_images:
        if img.get("source_type") != "产品":
            continue
        if img.get("is_banner"):
            continue
        if img.get("category") in (None, "", "未分类"):
            continue
        groups.setdefault(img["category"], []).append(img)

    for items in groups.values():
        items.sort(key=lambda x: x["out_rel"])

    return groups


def run(force=False, only=None):
    from skills.main_image_selection_and_outpainting import (
        main_image_selection_and_outpainting as main_skill
    )

    print("=" * 60)
    print("Stage 2：每类产品最优主图（4:3 + 1:1）")
    print("=" * 60)

    processed_manifest, images = load_processed()

    if not images:
        raise RuntimeError("Stage 1 没有可用图片，请先运行 Stage 1")

    groups = _category_images(images)

    current_input_hash = processed_manifest.get("input_hash")
    previous_manifest = common.load_manifest("main")
    stale = previous_manifest.get("input_hash") != current_input_hash

    if stale and previous_manifest:
        print("检测到图片处理结果已变化，重新执行主图优选。")

    categories = sorted(groups.keys())
    if only:
        categories = [c for c in categories if c in only]

    if not categories:
        print("没有可处理的产品类别")
        return {"categories": []}

    print(f"产品类别：{', '.join(categories)}")

    results = []

    for index, category in enumerate(categories, start=1):

        items = groups[category]
        output_dir = common.MAIN_IMAGE_DIR / common.safe_name(category)

        print(
            f"\n[{index}/{len(categories)}] {category}"
            f"（{len(items)} 张候选）"
        )

        four_three = output_dir / "main_image.png"
        square = output_dir / "main_image_1_1.png"
        result_json = output_dir / "selection_result.json"

        record = {
            "category": category,
            "image_count": len(items),
            "source_images": [img["out_rel"] for img in items],
            "four_three_rel": common.rel_path(four_three),
            "square_rel": common.rel_path(square),
        }

        if (
            not force
            and not stale
            and four_three.exists()
            and square.exists()
            and result_json.exists()
        ):
            old = common.load_json(result_json)
            record.update({
                "success": True,
                "selected_image": old.get("selected_image"),
                "operation": old.get("operation", "skip"),
                "final_size": old.get("final_size"),
                "cached": True,
            })
            print("  已有结果，跳过")
            results.append(record)
            continue

        try:
            image_paths = [
                common.PROJECT_ROOT / img["out_rel"]
                for img in items
            ]

            output = main_skill.run(
                output_dir=str(output_dir),
                image_paths=image_paths
            )

            record.update({
                "success": True,
                "selected_image": Path(output["selected_image"]).name,
                "operation": output.get("operation"),
                "final_size": common.get_image_size(
                    common.abs_path(record["four_three_rel"])
                ),
                "cached": False,
            })

        except Exception as exc:

            print(f"  失败：{category} -> {exc!r}")
            record.update({
                "success": False,
                "error": repr(exc),
            })

        results.append(record)

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_hash": processed_manifest.get("input_hash"),
        "categories": results,
    }

    common.save_manifest("main", manifest)

    success = sum(1 for r in results if r.get("success"))
    print(f"\nStage 2 完成：{success}/{len(results)} 个类别成功")

    return manifest


def load_main_images():
    manifest = common.load_manifest("main")
    return {
        item["category"]: item
        for item in manifest.get("categories", [])
        if item.get("success")
    }
