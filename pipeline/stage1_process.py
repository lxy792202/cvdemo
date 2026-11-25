"""Stage 1：全部图片逐张分析并调用图片处理 Skill。"""

from datetime import datetime
from pathlib import Path

from pipeline import common


def _final_output_path(rel):
    rel_path = Path(rel)
    filename = f"{common.safe_stem(rel_path)}__{common.image_id_for(rel)}_final.png"
    return common.PROCESSED_DIR / rel_path.parent / filename


def run(force=False, limit=None):
    from test_image_analysis import process_image, analyze_image

    print("=" * 60)
    print("Stage 1：图片逐张处理")
    print("=" * 60)

    images = common.collect_input_images(
        common.INPUT_DIR,
        common.INPUT_DIR
    )

    if limit:
        images = images[:limit]

    previous = common.load_manifest("processed")
    previous_map = {
        item["src_rel"]: item
        for item in previous.get("images", [])
    }

    current_hash = common.input_hash(img["rel"] for img in images)

    results = []

    def build_manifest():
        return {
            "schema_version": 1,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "input_dir": common.rel_path(common.INPUT_DIR),
            "input_hash": current_hash,
            "total": len(results),
            "success": sum(1 for r in results if r.get("success")),
            "images": results,
        }

    for index, item in enumerate(images, start=1):

        rel = item["rel"]
        src_path = item["path"]
        image_id = common.image_id_for(rel)

        source_type, category, is_banner = (
            common.derive_source_type_category(
                Path(rel).parent.parts,
                src_path.name
            )
        )

        final_path = _final_output_path(rel)
        work_dir = common.WORK_ROOT / image_id

        content_hash = common.file_sha1(src_path)

        print(
            f"\n[{index}/{len(images)}] {rel}\n"
            f"  分类：{source_type} / {category}"
            f"{'（banner）' if is_banner else ''}"
        )

        record = {
            "image_id": image_id,
            "src_rel": rel,
            "out_rel": common.rel_path(final_path),
            "source_type": source_type,
            "category": category,
            "is_banner": is_banner,
            "content_hash": content_hash,
        }

        old = previous_map.get(rel)

        if (
            not force
            and old
            and old.get("success")
            and old.get("content_hash") == content_hash
            and final_path.exists()
        ):
            print("  已处理过，跳过")
            record.update(old)
            results.append(record)
            continue

        try:
            width, height = common.get_image_size(src_path)

            result = process_image(
                src_path,
                final_path.parent,
                work_dir,
                final_filename=final_path.name
            )

            record.update({
                "success": True,
                "width": width,
                "height": height,
                "skills": result.get("executed_skills", []),
                "error": None,
            })

            print(f"  完成：{record['out_rel']}")

        except Exception as exc:

            print(f"  失败：{src_path.name} -> {exc!r}")

            record.update({
                "success": False,
                "skills": [],
                "error": repr(exc),
            })

        results.append(record)
        common.save_manifest("processed", build_manifest())

    manifest = build_manifest()
    common.save_manifest("processed", manifest)

    print(
        f"\nStage 1 完成：{manifest['success']}/{manifest['total']} 张成功"
    )
    print(f"清单：{common.MANIFEST_DIR / 'processed.json'}")

    return manifest


def load_processed(only_success=True):
    manifest = common.load_manifest("processed")
    images = manifest.get("images", [])
    if only_success:
        images = [img for img in images if img.get("success")]
    return manifest, images
