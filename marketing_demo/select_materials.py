"""Stage 3：根据 design.json 从处理后的图片中智能选图。

与旧版区别：
- 输入/输出目录全部参数化，默认输入 output/01_processed；
- 设计规范读取 marketing_demo/input/design.json（不再依赖 d.json）；
- 支持“一页多产品”任务，一次为一个产品分类各选一张图（selected_ids）；
- 输出 selection_manifest.json，作为 Stage 4 的唯一选图契约。
"""

import argparse
import base64
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 项目根目录 demo
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config import TEMPERATURE  # noqa: E402
from pipeline import common  # noqa: E402


# ============================================================
# 1. 默认配置（可被命令行覆盖）
# ============================================================

DEFAULT_INPUT_DIR = common.PROCESSED_DIR
DEFAULT_DESIGN_JSON = common.DESIGN_JSON
DEFAULT_COMPANY_JSON = common.COMPANY_JSON
DEFAULT_OUTPUT_DIR = common.SELECTED_MATERIALS_DIR
DEFAULT_WORK_DIR = common.MATERIAL_TEMP_DIR

PAGE_SIZE = 20
SHEET_COLS = 4
SHEET_ROWS = 5
THUMB_WIDTH = 240
THUMB_HEIGHT = 150
AVOID_DUPLICATES = True

client = common.client


# ============================================================
# 2. 通用工具
# ============================================================

def load_json(path, default=None):
    return common.load_json(path, default={} if default is None else default)


def save_json(data, path):
    return common.save_json(data, path)


def safe_name(name):
    return common.safe_name(name)


def normalize_category(name):
    return common.normalize_category(name)


def encode_image(path):
    return common.encode_image(path)


def is_banner_image(img):
    return img["is_banner"]


def get_region_folder(region):
    return REGION_OUTPUT_DIR / region


def is_skip_design_item(item):
    """排除不需要选图的模块：服务流程、电话咨询等。"""
    item_id = str(item.get("id", "")).lower()
    item_name = str(item.get("name", "")).lower()
    text = item_id + " " + item_name

    skip_keywords = [
        "服务流程", "流程图", "service_process", "service_flow", "process",
        "联系我们", "联系咨询", "电话咨询", "电话", "咨询电话",
        "telephone", "phone", "call", "logo",
    ]

    return any(keyword.lower() in text for keyword in skip_keywords)


def is_banner_item(item):
    text = (
        str(item.get("id", "")).lower()
        + " " + str(item.get("name", "")).lower()
    )
    return any(
        keyword in text
        for keyword in ["banner", "轮播", "carousel"]
    )


def is_product_main(item):
    item_id = str(item.get("id", "")).lower()
    item_name = str(item.get("name", ""))
    return item_id == "product_main" or "产品主图" in item_name


def is_product_detail(item):
    item_id = str(item.get("id", "")).lower()
    item_name = str(item.get("name", ""))
    return (
        "product_detail" in item_id
        or "产品细节" in item_name
        or "产品详情图" in item_name
    )


def is_product_service(item):
    item_id = str(item.get("id", "")).lower()
    item_name = str(item.get("name", ""))
    return (
        item_id == "product_service"
        or "产品及服务范围" in item_name
        or "产品服务范围" in item_name
    )


# ============================================================
# 3. 扫描图片
# ============================================================

def scan_images(input_dir=None, output_dir=None, work_dir=None):

    input_dir = common.abs_path(input_dir or DEFAULT_INPUT_DIR)
    output_dir = common.abs_path(output_dir or DEFAULT_OUTPUT_DIR)
    work_dir = common.abs_path(work_dir or DEFAULT_WORK_DIR)

    print("\n========== 开始扫描图片 ==========")
    print(f"输入目录：{input_dir}")

    if not input_dir.exists():
        raise FileNotFoundError(f"找不到输入目录：{input_dir}")

    skip_dirs = {
        output_dir.resolve(),
        work_dir.resolve(),
        common.WORK_ROOT.resolve(),
        common.PROCESSED_DIR.resolve() / "_work",
    }

    images = []

    for path in sorted(input_dir.rglob("*")):

        if not path.is_file():
            continue

        if path.suffix.lower() not in common.SUPPORTED_EXTENSIONS:
            continue

        if any(parent.resolve() in skip_dirs for parent in path.parents):
            continue

        try:
            with Image.open(path) as img:
                img.verify()
        except Exception as exc:
            print(f"跳过无效图片：{path.name}（{exc}）")
            continue

        relative_path = path.relative_to(input_dir)
        folder_parts = relative_path.parent.parts

        source_type, category, is_banner = (
            common.derive_source_type_category(folder_parts, path.name)
        )

        image_id = f"IMG_{len(images) + 1:05d}"

        images.append({
            "id": image_id,
            "path": path,
            "filename": path.name,
            "relative_path": str(relative_path),
            "category": category,
            "source_type": source_type,
            "is_banner": is_banner,
            "extension": path.suffix.lower(),
        })

    stats = defaultdict(int)
    for img in images:
        stats[(img["source_type"], img["category"])] += 1

    print(f"\n图片总数：{len(images)}")
    print("\n素材分类统计：")
    for (source_type, category), count in sorted(stats.items()):
        print(f"{source_type} / {category}: {count}张")

    print(f"\nBanner图片：{sum(1 for i in images if i['is_banner'])}张")

    return images


# ============================================================
# 4. 建立图片索引
# ============================================================

def build_image_index(images, work_dir=None):

    print("\n========== 建立图片索引 ==========")

    work_dir = common.abs_path(work_dir or DEFAULT_WORK_DIR)

    index = {
        "total": len(images),
        "categories": {},
        "source_types": {},
        "images": [],
    }

    for img in images:

        index["source_types"][img["source_type"]] = (
            index["source_types"].get(img["source_type"], 0) + 1
        )
        index["categories"][img["category"]] = (
            index["categories"].get(img["category"], 0) + 1
        )

        index["images"].append({
            "id": img["id"],
            "filename": img["filename"],
            "relative_path": img["relative_path"],
            "category": img["category"],
            "source_type": img["source_type"],
            "is_banner": img["is_banner"],
        })

    save_json(index, work_dir / "image_index.json")
    print(f"图片索引已保存：{work_dir / 'image_index.json'}")

    return index


def get_product_categories(images):
    return sorted({
        img["category"]
        for img in images
        if img["source_type"] == "产品"
        and img["category"] != "未分类"
        and not img["is_banner"]
    })


# ============================================================
# 5. 根据设计规范生成筛选任务
# ============================================================

def build_targets(design, images):

    print("\n========== 生成设计任务 ==========")

    targets = []

    product_categories = get_product_categories(images)

    design_sections = [
        ("PC端", design.get("pc_design", {}).get("materials", [])),
        ("落地页", design.get("landing_page", {}).get("modules", [])),
    ]

    for region, items in design_sections:

        print(f"\n正在处理：{region}")

        for item in items:

            if item.get("required") is not True:
                continue

            if is_banner_item(item):
                print(f"跳过轮播图（Stage 4 重新生成）：{item.get('name')}")
                continue

            if is_skip_design_item(item):
                print(f"跳过不需要图片的模块：{item.get('name')}")
                continue

            if is_product_main(item):
                print(f"跳过产品主图（Stage 4 复用 Stage 2 成果）：{item.get('name')}")
                continue

            item_id = str(item.get("id", "")).lower()
            item_name = item.get("name", item_id)

            # ------------------------------------------------
            # 产品及服务范围：一页多产品，每个分类各一张
            # ------------------------------------------------
            if is_product_service(item):

                if not product_categories:
                    print("没有产品分类，跳过多产品任务")
                    continue

                targets.append({
                    "target_id": f"{region}_{item_id}",
                    "module_id": item_id,
                    "name": item_name,
                    "design_type": region,
                    "region": region,
                    "mode": "multi",
                    "category": "不限",
                    "categories": product_categories,
                    "source_type": "产品",
                    "selection_rule": (
                        "需要为每个产品分类各选择一张代表性产品图，"
                        "覆盖全部分类"
                    ),
                    "requirements": item,
                })

                continue

            # ------------------------------------------------
            # 产品细节图：单张
            # ------------------------------------------------
            if is_product_detail(item):

                targets.append({
                    "target_id": f"{region}_{item_id}",
                    "module_id": item_id,
                    "name": item_name,
                    "design_type": region,
                    "region": region,
                    "mode": "single",
                    "category": "不限",
                    "source_type": "产品",
                    "selection_rule": (
                        "优先选择产品细节特写，没有合适细节时"
                        "选择好看的产品图"
                    ),
                    "requirements": item,
                })

                continue

            # ------------------------------------------------
            # 普通设计项目：单张
            # ------------------------------------------------
            targets.append({
                "target_id": f"{region}_{item_id}",
                "module_id": item_id,
                "name": item_name,
                "design_type": region,
                "region": region,
                "mode": "single",
                "category": "不限",
                "source_type": "不限",
                "selection_rule": "选择最适合设计用途的图片",
                "requirements": item,
            })

    print(f"\n需要筛选的设计项目：{len(targets)}个")
    for target in targets:
        print(
            f"  {target['region']} / {target['name']} / "
            f"{target['mode']} / {target['category']}"
        )

    return targets


# ============================================================
# 6. 生成图片索引图
# ============================================================

def get_font(size=16):

    font_paths = [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]

    for font_path in font_paths:
        if Path(font_path).exists():
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                pass

    return ImageFont.load_default()


def create_contact_sheets(images, work_dir=None):

    print("\n========== 生成图片索引图 ==========")

    work_dir = common.abs_path(work_dir or DEFAULT_WORK_DIR)
    work_dir.mkdir(parents=True, exist_ok=True)

    sheet_paths = []
    cell_width = 260
    cell_height = 205
    font = get_font(15)

    for page_index in range(0, len(images), PAGE_SIZE):

        page_images = images[page_index:page_index + PAGE_SIZE]

        sheet = Image.new(
            "RGB",
            (SHEET_COLS * cell_width, SHEET_ROWS * cell_height),
            "#F5F5F5",
        )
        draw = ImageDraw.Draw(sheet)

        for index, item in enumerate(page_images):

            col = index % SHEET_COLS
            row = index // SHEET_COLS
            x = col * cell_width
            y = row * cell_height

            try:
                image = Image.open(item["path"]).convert("RGB")
                image.thumbnail((THUMB_WIDTH, THUMB_HEIGHT))
                sheet.paste(
                    image,
                    (x + (cell_width - image.width) // 2, y + 5),
                )
                draw.text(
                    (x + 8, y + 160),
                    item["id"],
                    fill="#D00000",
                    font=font,
                )
                draw.text(
                    (x + 8, y + 181),
                    item["category"][:20],
                    fill="#222222",
                    font=font,
                )
            except Exception as exc:
                print(f"图片读取失败：{item['path']}，{exc}")

        sheet_path = (
            work_dir / f"sheet_{page_index // PAGE_SIZE + 1:03d}.jpg"
        )
        sheet.save(sheet_path, quality=85)
        sheet_paths.append(sheet_path)

        print(f"已生成：{sheet_path.name}")

    return sheet_paths


# ============================================================
# 7. 企业资料
# ============================================================

def load_company_info(company_file=None):
    company = load_json(company_file or DEFAULT_COMPANY_JSON)
    company_text = json.dumps(company, ensure_ascii=False, indent=2)
    return company_text[:12000]


# ============================================================
# 8. 调用视觉模型筛选图片
# ============================================================

def select_materials(images, targets, sheet_paths, company_info):

    print("\n========== 开始智能素材筛选 ==========")

    candidate_images = [img for img in images if not img["is_banner"]]

    image_manifest = [
        {
            "id": img["id"],
            "filename": img["filename"],
            "relative_path": img["relative_path"],
            "category": img["category"],
            "source_type": img["source_type"],
        }
        for img in candidate_images
    ]

    target_info = [
        {
            "target_id": target["target_id"],
            "name": target["name"],
            "mode": target["mode"],
            "design_type": target["design_type"],
            "region": target["region"],
            "category": target["category"],
            "categories": target.get("categories", []),
            "source_type": target["source_type"],
            "selection_rule": target["selection_rule"],
            "requirements": target["requirements"],
        }
        for target in targets
    ]

    prompt = f"""
你是一名专业企业营销视觉设计师和图片素材筛选专家。

你的任务是根据企业资料和设计规范，
从已有图片中选择适合的素材。

本次只选择原始图片，不生成、不修改图片。

========================
一、企业资料
========================

{company_info}

========================
二、设计任务
========================

{json.dumps(target_info, ensure_ascii=False, indent=2)}

========================
三、图片索引
========================

{json.dumps(image_manifest, ensure_ascii=False, indent=2)}

图片索引图中每张图片下方有唯一ID。
必须结合索引图真实内容判断，不能只看文件名。

========================
四、选图规则
========================

1. mode=single 的任务只选择一张图片，返回 selected_id。

2. mode=multi 的任务（一页多产品）需要为 categories 中
   每一个产品分类各选择一张图片，返回 selected_ids 数组，
   数量应等于 categories 数量，且每个分类一张。

3. PC端内部不能重复使用图片。

4. 落地页内部不能重复使用图片。

5. PC端和落地页之间允许使用同一张图片。

6. 公司介绍图：
   优先选择企业素材，例如生产、施工、团队、
   厂区、仓储、企业相关图片。
   如果没有合适的企业图片，可以从产品图片中
   选择一张漂亮、有代表性的图片作为替代。
   不要因为没有企业素材而返回null。

7. 产品细节图：
   优先选择产品局部、纹理、结构、工艺特写。
   如果没有合适的细节图，可以选择一张好看的
   产品图片代替。只需要一张。

8. 产品及服务范围（mode=multi）：
   必须严格按照每个产品分类选择对应分类的图片，
   不能跨分类选择，不能重复。

9. 产品类任务必须来自产品图片，
   不允许使用工程案例图片代替产品图片。

10. 工程案例图片仅适用于工程案例或相关企业场景，
    不能当作产品实物图。

11. 首屏或头图优先选择主体突出、构图好、
    画面美观的图片。

12. 企业优势优先选择能体现生产、施工、
    团队、经营能力的企业素材。
    如果企业图片不足，可以考虑产品图片，
    但必须真实符合设计用途。

13. 每个任务都应尽量选择不同图片，
    但PC端与落地页之间允许重复。

14. 只能返回图片索引中真实存在的ID。

15. 不确定的图片可以返回null，
    但公司介绍图、产品细节图、产品及服务范围
    尽量按规则使用图片兜底。

========================
五、输出格式
========================

只返回JSON，不要返回Markdown。

格式：

{{
  "selections": [
    {{
      "target_id": "落地页_product_service",
      "selected_ids": ["IMG_00001", "IMG_00002"],
      "reason": "选择理由",
      "confidence": 0.95
    }},
    {{
      "target_id": "落地页_hero",
      "selected_id": "IMG_00003",
      "reason": "选择理由",
      "confidence": 0.9
    }}
  ]
}}

所有target_id必须返回结果。
selected_id / selected_ids 可以为null。
"""

    content = [{"type": "text", "text": prompt}]

    for sheet_path in sheet_paths:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": common.encode_image_thumb(sheet_path),
                "detail": "high",
            },
        })

    print(f"候选图片：{len(candidate_images)}张")
    print(f"设计任务：{len(targets)}个")
    print(f"索引图：{len(sheet_paths)}张")
    print("正在请求视觉理解模型...")

    response = client.chat.completions.create(
        model=common.VISION_MODEL,
        messages=[{"role": "user", "content": content}],
        temperature=TEMPERATURE,
    )

    result_text = response.choices[0].message.content

    if not result_text:
        raise ValueError("模型没有返回内容")

    try:
        return common.extract_json(result_text)
    except json.JSONDecodeError:
        print("模型原始返回：")
        print(result_text)
        raise ValueError("模型返回的内容不是合法JSON")


# ============================================================
# 9. 校验选图结果
# ============================================================

def _validate_one(
    selected_id,
    target,
    image_map,
    used_images,
    allowed_categories=None
):
    """校验单张选中图片，通过后返回图片信息。"""

    image_info = image_map.get(selected_id)

    if not image_info:
        print(f"无效图片ID：{selected_id}")
        return None

    if image_info["is_banner"]:
        print(f"Banner素材禁止参与选图：{selected_id}")
        return None

    if allowed_categories:
        if image_info["category"] not in allowed_categories:
            print(
                f"产品分类不匹配：{target['name']} "
                f"要求={allowed_categories} "
                f"实际={image_info['category']}"
            )
            return None
    elif target["category"] != "不限":
        if image_info["category"] != target["category"]:
            print(
                f"产品分类不匹配：{target['name']} "
                f"要求={target['category']} "
                f"实际={image_info['category']}"
            )
            return None

    if target["source_type"] == "产品":
        if image_info["source_type"] != "产品":
            print(f"素材来源不匹配：{target['name']}")
            return None

    if AVOID_DUPLICATES and selected_id in used_images:
        print(
            f"{target['region']}内部图片重复，跳过：{selected_id}"
        )
        return None

    used_images.add(selected_id)
    return image_info


def validate_selections(selection_result, images, targets):

    print("\n========== 校验筛选结果 ==========")

    image_map = {img["id"]: img for img in images}
    target_list_map = {target["target_id"]: target for target in targets}

    selection_map = {
        item.get("target_id"): item
        for item in selection_result.get("selections", [])
    }

    used_images_by_region = {"PC端": set(), "落地页": set()}
    valid_results = []

    for target in targets:

        target_id = target["target_id"]
        selection = selection_map.get(target_id)

        if not selection:
            print(f"缺少任务结果：{target['name']}")
            continue

        region = target["region"]
        used_images = used_images_by_region.setdefault(region, set())

        if target["mode"] == "multi":

            selected_ids = selection.get("selected_ids") or []
            allowed = set(target.get("categories", []))
            images_selected = []
            covered = set()

            for selected_id in selected_ids:

                info = _validate_one(
                    selected_id, target, image_map,
                    used_images, allowed_categories=allowed
                )

                if not info:
                    continue

                if info["category"] in covered:
                    print(f"分类重复选择，跳过：{info['category']}")
                    continue

                covered.add(info["category"])
                images_selected.append({
                    "selected_id": selected_id,
                    "category": info["category"],
                    "source_type": info["source_type"],
                    "source_rel": info["relative_path"],
                })

            missing = sorted(allowed - covered)
            if missing:
                print(f"多产品任务缺少分类素材：{', '.join(missing)}")

            if not images_selected:
                print(f"多产品任务没有有效选择：{target['name']}")
                continue

            valid_results.append({
                "target_id": target_id,
                "module_id": target["module_id"],
                "name": target["name"],
                "region": region,
                "mode": "multi",
                "category": target["category"],
                "categories": target.get("categories", []),
                "images": images_selected,
                "reason": selection.get("reason", ""),
                "confidence": selection.get("confidence", 0),
            })

            print(
                f"通过：{region} / {target['name']} -> "
                f"{[i['category'] for i in images_selected]}"
            )
            continue

        selected_id = selection.get("selected_id")

        if not selected_id:
            print(f"未找到合适素材：{target['name']}")
            continue

        info = _validate_one(
            selected_id, target, image_map, used_images
        )

        if not info:
            continue

        valid_results.append({
            "target_id": target_id,
            "module_id": target["module_id"],
            "name": target["name"],
            "region": region,
            "mode": "single",
            "category": target["category"],
            "categories": [],
            "images": [{
                "selected_id": selected_id,
                "category": info["category"],
                "source_type": info["source_type"],
                "source_rel": info["relative_path"],
            }],
            "reason": selection.get("reason", ""),
            "confidence": selection.get("confidence", 0),
        })

        print(
            f"通过：{region} / {target['name']} -> {selected_id}"
        )

    return valid_results


# ============================================================
# 10. 归档选中图片
# ============================================================

def _copy_one(image_info, target_dir, category=None):

    source_path = image_info["path"]

    filename = source_path.name
    if category:
        filename = f"{safe_name(category)}_{filename}"

    destination_path = target_dir / filename

    if destination_path.exists():
        destination_path = (
            target_dir / f"{image_info['id']}{source_path.suffix}"
        )

    shutil.copy2(source_path, destination_path)
    return destination_path


def save_selected_materials(selections, images, targets, output_dir=None):

    print("\n========== 开始归档素材 ==========")

    output_dir = common.abs_path(output_dir or DEFAULT_OUTPUT_DIR)
    image_map = {img["id"]: img for img in images}
    target_map = {target["target_id"]: target for target in targets}

    output_dir.mkdir(parents=True, exist_ok=True)

    report = []
    manifest_items = []

    for selection in selections:

        target = target_map[selection["target_id"]]
        region = target["region"]

        target_dir = get_region_folder(region) / safe_name(target["name"])
        target_dir.mkdir(parents=True, exist_ok=True)

        copied_images = []

        for item in selection["images"]:

            image_info = image_map[item["selected_id"]]

            destination_path = _copy_one(
                image_info,
                target_dir,
                category=item["category"] if selection["mode"] == "multi" else None,
            )

            copied_images.append({
                "selected_id": item["selected_id"],
                "category": item["category"],
                "source_rel": item["source_rel"],
                "out_rel": common.rel_path(destination_path),
            })

            print(
                f"归档：{region} / {target['name']} -> "
                f"{destination_path.name}"
            )

        report.append({
            "target_id": selection["target_id"],
            "module_id": selection["module_id"],
            "design_name": target["name"],
            "design_type": target["design_type"],
            "region": region,
            "mode": selection["mode"],
            "category": selection["category"],
            "selected_images": copied_images,
            "reason": selection["reason"],
            "confidence": selection["confidence"],
        })

        manifest_items.append({
            "target_id": selection["target_id"],
            "module_id": selection["module_id"],
            "region": region,
            "mode": selection["mode"],
            "name": target["name"],
            "category": selection["category"],
            "categories": selection.get("categories", []),
            "images": copied_images,
            "reason": selection["reason"],
            "confidence": selection["confidence"],
        })

    save_json(report, output_dir / "selection_report.json")

    selected_targets = {item["target_id"] for item in selections}

    missing_targets = [
        {
            "target_id": target["target_id"],
            "module_id": target["module_id"],
            "design_name": target["name"],
            "region": target["region"],
            "category": target["category"],
            "reason": "没有找到合适素材或校验未通过",
        }
        for target in targets
        if target["target_id"] not in selected_targets
    ]

    save_json(missing_targets, output_dir / "missing_materials.json")

    from datetime import datetime

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_dir": common.rel_path(DEFAULT_INPUT_DIR),
        "input_hash": common.input_hash(
            common.rel_path(img["path"]) for img in images
        ),
        "selections": manifest_items,
    }

    save_json(manifest, output_dir / "selection_manifest.json")
    common.save_manifest("selected", manifest)

    print("\n素材归档完成")
    print(f"成功归档：{len(report)}个任务")
    print(f"未找到素材：{len(missing_targets)}个")
    print(f"输出目录：{output_dir}")

    return report


# ============================================================
# 11. 主流程
# ============================================================

def main(
    input_dir=None,
    design_file=None,
    company_file=None,
    output_dir=None,
    work_dir=None,
    force=False,
):

    global REGION_OUTPUT_DIR

    input_dir = common.abs_path(input_dir or DEFAULT_INPUT_DIR)
    design_file = common.abs_path(design_file or DEFAULT_DESIGN_JSON)
    company_file = common.abs_path(company_file or DEFAULT_COMPANY_JSON)
    output_dir = common.abs_path(output_dir or DEFAULT_OUTPUT_DIR)
    work_dir = common.abs_path(work_dir or DEFAULT_WORK_DIR)

    REGION_OUTPUT_DIR = output_dir

    print("=" * 55)
    print("企业营销设计素材智能筛选系统（Stage 3）")
    print("=" * 55)

    if not input_dir.exists():
        raise FileNotFoundError(f"找不到输入目录：{input_dir}")

    if not design_file.exists():
        raise FileNotFoundError(f"找不到设计规范：{design_file}")

    design = load_json(design_file)
    company_info = load_company_info(company_file)

    images = scan_images(input_dir, output_dir, work_dir)

    if not images:
        print("没有找到任何图片")
        return []

    current_hash = common.input_hash(
        common.rel_path(img["path"]) for img in images
    )

    existing_manifest = load_json(
        output_dir / "selection_manifest.json", default={}
    )

    existing_files_ok = all(
        (common.PROJECT_ROOT / img["out_rel"]).exists()
        for selection in existing_manifest.get("selections", [])
        for img in selection.get("images", [])
    ) and bool(existing_manifest.get("selections"))

    if (
        not force
        and output_dir.exists()
        and existing_manifest.get("input_hash") == current_hash
        and existing_files_ok
        and (output_dir / "selection_report.json").exists()
    ):
        print("输入图片未变化，复用已有选图结果。")
        return load_json(
            output_dir / "selection_report.json", default=[]
        )

    if output_dir.exists() and (
        force or existing_manifest.get("input_hash") != current_hash
    ):
        common.archive_existing(output_dir, "selected_materials")

    work_dir.mkdir(parents=True, exist_ok=True)

    build_image_index(images, work_dir)

    targets = build_targets(design, images)

    if not targets:
        print("没有需要筛选的设计项目")
        return []

    candidate_images = [img for img in images if not img["is_banner"]]

    if not candidate_images:
        print("没有可用于AI筛选的候选图片")
        return []

    sheet_paths = create_contact_sheets(candidate_images, work_dir)

    selection_result = select_materials(
        images, targets, sheet_paths, company_info
    )

    save_json(
        selection_result,
        work_dir / "model_selection_result.json",
    )

    valid_selections = validate_selections(
        selection_result, images, targets
    )

    report = save_selected_materials(
        valid_selections, images, targets, output_dir
    )

    print("\n" + "=" * 55)
    print("Stage 3 完成！")
    print(f"设计任务总数：{len(targets)}")
    print(f"成功选中数量：{len(report)}")
    print(f"最终素材目录：{output_dir}")
    print("=" * 55)

    return report


REGION_OUTPUT_DIR = DEFAULT_OUTPUT_DIR


def _parse_args():
    parser = argparse.ArgumentParser(description="Stage 3：设计素材智能筛选")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--design", default=str(DEFAULT_DESIGN_JSON))
    parser.add_argument("--company", default=str(DEFAULT_COMPANY_JSON))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    main(
        input_dir=args.input_dir,
        design_file=args.design,
        company_file=args.company,
        output_dir=args.output_dir,
        work_dir=args.work_dir,
        force=args.force,
    )
