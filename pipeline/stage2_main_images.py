"""Stage 2：每类产品选出最优主图，输出 4:3 与 1:1 两版。"""

import math
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from pipeline import common
from pipeline.stage1_process import load_processed


COLLAGE_SIZE = (2000, 1500)  # 4:3
COLLAGE_MARGIN = 40
COLLAGE_GAP = 18


def _collage_boxes(width, height, count):
    """返回 count 张图片的美观排版区域（4:3 画布）。"""

    x1, y1 = COLLAGE_MARGIN, COLLAGE_MARGIN
    x2, y2 = width - COLLAGE_MARGIN, height - COLLAGE_MARGIN
    area_w, area_h = x2 - x1, y2 - y1
    gap = COLLAGE_GAP

    if count <= 0:
        return []

    if count == 1:
        return [(x1, y1, x2, y2)]

    if count == 2:
        mid = x1 + area_w // 2
        return [
            (x1, y1, mid - gap // 2, y2),
            (mid + gap // 2, y1, x2, y2),
        ]

    if count == 3:
        left_w = int(area_w * 0.62)
        right_h = (area_h - gap) // 2
        return [
            (x1, y1, x1 + left_w, y2),
            (x1 + left_w + gap, y1, x2, y1 + right_h),
            (x1 + left_w + gap, y1 + right_h + gap, x2, y2),
        ]

    if count == 4:
        cell_w = (area_w - gap) // 2
        cell_h = (area_h - gap) // 2
        return [
            (x1, y1, x1 + cell_w, y1 + cell_h),
            (x1 + cell_w + gap, y1, x2, y1 + cell_h),
            (x1, y1 + cell_h + gap, x1 + cell_w, y2),
            (x1 + cell_w + gap, y1 + cell_h + gap, x2, y2),
        ]

    # >=5：顶部主图 + 下方网格
    hero_h = int(area_h * 0.46)
    boxes = [(x1, y1, x2, y1 + hero_h)]

    rest = count - 1
    columns = 2 if rest <= 4 else 3
    rows = math.ceil(rest / columns)

    grid_top = y1 + hero_h + gap
    grid_height = y2 - grid_top
    cell_h = (grid_height - gap * (rows - 1)) // rows

    for row in range(rows):
        row_count = min(columns, rest - row * columns)
        cell_w = (area_w - gap * (row_count - 1)) // row_count
        row_width = cell_w * row_count + gap * (row_count - 1)
        start_x = x1 + (area_w - row_width) // 2
        cell_y = grid_top + row * (cell_h + gap)

        for col in range(row_count):
            cell_x = start_x + col * (cell_w + gap)
            boxes.append(
                (cell_x, cell_y, cell_x + cell_w, cell_y + cell_h)
            )

    return boxes


def _paste_rounded(canvas, image_path, box, radius=14):
    x1, y1, x2, y2 = (int(v) for v in box)
    width, height = x2 - x1, y2 - y1

    if width <= 0 or height <= 0:
        return

    image = common.fit_cover(image_path, (width, height))
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width, height), radius=radius, fill=255
    )
    canvas.paste(image, (x1, y1), mask)


def build_collage(image_paths, out_path):
    """把剩余产品图按美观排版拼成 4:3 图集，并压到 2MB 以内。"""

    width, height = COLLAGE_SIZE
    canvas = Image.new("RGB", (width, height), "#FAF7F2")
    draw = ImageDraw.Draw(canvas)

    draw.rectangle((0, 0, width, 8), fill="#C50303")
    draw.line((0, 9, width, 9), fill="#C9A96A", width=1)

    boxes = _collage_boxes(width, height, len(image_paths))

    for path, box in zip(image_paths, boxes):
        _paste_rounded(canvas, path, box)

    common.save_image_within_limit(canvas, out_path)
    return out_path


def _ensure_collage(category, items, selected_name, output_dir,
                    rebuild=False):
    collage_path = output_dir / "product_collage.jpg"

    remaining = [
        common.PROJECT_ROOT / item["out_rel"]
        for item in items
        if Path(item["out_rel"]).name != selected_name
    ]

    if not remaining:
        return None

    if rebuild or not collage_path.exists():
        print(f"  生成产品图集：{len(remaining)} 张 -> {collage_path.name}")
        build_collage(remaining, collage_path)

    return {
        "collage_rel": common.rel_path(collage_path),
        "collage_count": len(remaining),
        "collage_size_mb": round(common.file_size_mb(collage_path), 2),
    }


def _migrate_png_outputs(output_dir, category):
    """把旧的 >2MB PNG 主图转成 JPEG，并把 PNG 原件移入 _png_source 留档。"""

    archive_dir = (
        common.MAIN_IMAGE_DIR / "_png_source" / common.safe_name(category)
    )

    for stem in ("main_image", "main_image_1_1"):
        png = output_dir / f"{stem}.png"
        jpg = output_dir / f"{stem}.jpg"

        if not png.exists():
            continue

        if not jpg.exists():
            common.save_image_within_limit(png, jpg)
            print(f"  已压缩：{category}/{stem}.png -> jpg")

        archive_dir.mkdir(parents=True, exist_ok=True)
        png.replace(archive_dir / png.name)


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

        _migrate_png_outputs(output_dir, category)

        four_three = output_dir / "main_image.jpg"
        square = output_dir / "main_image_1_1.jpg"
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
                "final_size": list(common.get_image_size(four_three)),
                "four_three_size_mb": round(
                    common.file_size_mb(four_three), 2
                ),
                "square_size_mb": round(
                    common.file_size_mb(square), 2
                ),
                "cached": True,
            })
            print(
                f"  已有结果，跳过（4:3 "
                f"{record['four_three_size_mb']}MB / 1:1 "
                f"{record['square_size_mb']}MB）"
            )

            try:
                collage = _ensure_collage(
                    category, items,
                    record.get("selected_image"),
                    output_dir, rebuild=force or stale,
                )
                if collage:
                    record.update(collage)
                    print(
                        f"  产品图集：{collage['collage_count']} 张 / "
                        f"{collage['collage_size_mb']}MB"
                    )
            except Exception as exc:
                print(f"  产品图集生成失败：{exc!r}")

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
                "final_size": list(common.get_image_size(four_three)),
                "four_three_size_mb": round(
                    common.file_size_mb(four_three), 2
                ),
                "square_size_mb": round(
                    common.file_size_mb(square), 2
                ),
                "cached": False,
            })

            try:
                collage = _ensure_collage(
                    category, items,
                    record.get("selected_image"),
                    output_dir, rebuild=True,
                )
                if collage:
                    record.update(collage)
                    print(
                        f"  产品图集：{collage['collage_count']} 张 / "
                        f"{collage['collage_size_mb']}MB"
                    )
            except Exception as exc:
                print(f"  产品图集生成失败：{exc!r}")

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
