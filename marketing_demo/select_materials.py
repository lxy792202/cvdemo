
import os
import re
import json
import base64
import shutil
from pathlib import Path
from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont
from openai import OpenAI

from config import API_KEY, BASE_URL, VISION_MODEL, TEMPERATURE


# ============================================================
# 1. 基础配置
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

INPUT_DIR = BASE_DIR / "input"
DESIGN_JSON = INPUT_DIR / "design.json"
COMPANY_JSON = INPUT_DIR / "company.json"

OUTPUT_DIR = BASE_DIR / "output" / "selected_materials"
WORK_DIR = BASE_DIR / "output" / "material_selection_temp"

SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp"
}

PAGE_SIZE = 20
SHEET_COLS = 4
SHEET_ROWS = 5

THUMB_WIDTH = 240
THUMB_HEIGHT = 150

TEMPERATURE = 0.1

# PC端和落地页各自去重
# 两个区域之间允许重复
AVOID_DUPLICATES = True

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    timeout=300,
    max_retries=2   # 超时自动重试2次
)


# ============================================================
# 2. 通用工具
# ============================================================

def load_json(path):
    if not path.exists():
        return {}

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data, path):
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def safe_name(name):
    name = re.sub(r'[\\/:*?"<>|]', "_", str(name))
    return name.strip()


def normalize_category(name):
    name = re.sub(r"^\s*\d+[\s._、-]*", "", str(name))
    return name.strip() or "未分类"


def encode_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def is_banner_image(img):
    return img["is_banner"]


def get_region_folder(region):
    return OUTPUT_DIR / region


def is_skip_design_item(item):
    """
    排除不需要选图的模块：
    服务流程、电话咨询等
    """

    item_id = str(item.get("id", "")).lower()
    item_name = str(item.get("name", "")).lower()

    text = item_id + " " + item_name

    skip_keywords = [
        "服务流程",
        "流程图",
        "service_process",
        "service_flow",
        "process",
        "联系我们",
        "联系咨询",
        "电话咨询",
        "电话",
        "咨询电话",
        "telephone",
        "phone",
        "call"
    ]

    return any(keyword.lower() in text for keyword in skip_keywords)


def is_product_main(item):
    item_id = str(item.get("id", "")).lower()
    item_name = str(item.get("name", ""))

    return (
        item_id == "product_main"
        or "产品主图" in item_name
    )


def is_product_detail(item):
    item_id = str(item.get("id", "")).lower()
    item_name = str(item.get("name", ""))

    return (
        "product_detail" in item_id
        or "产品细节" in item_name
        or "产品详情图" in item_name
    )


def is_product_advantage(item):
    item_id = str(item.get("id", "")).lower()
    item_name = str(item.get("name", ""))

    return (
        "product_advantage" in item_id
        or "产品优势" in item_name
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
# 3. 自动扫描 input 下所有图片
# ============================================================

def scan_images():

    print("\n========== 开始扫描图片 ==========")

    images = []

    for path in INPUT_DIR.rglob("*"):

        if not path.is_file():
            continue

        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        if OUTPUT_DIR in path.parents:
            continue

        if WORK_DIR in path.parents:
            continue

        relative_path = path.relative_to(INPUT_DIR)

        parts = relative_path.parts
        folder_parts = parts[:-1]

        source_type = "其他"
        category = "未分类"

        product_root_index = None

        # 自动识别产品中心
        for index, folder in enumerate(folder_parts):

            if "产品" in folder:
                product_root_index = index
                break

        if product_root_index is not None:

            source_type = "产品"

            if product_root_index + 1 < len(folder_parts):

                category = normalize_category(
                    folder_parts[product_root_index + 1]
                )

        elif any("工程案例" in p for p in folder_parts):

            source_type = "工程案例"

            if folder_parts:
                category = normalize_category(
                    folder_parts[-1]
                )

        elif folder_parts:

            source_type = "企业素材"

            category = normalize_category(
                folder_parts[-1]
            )

        is_banner = "banner" in path.name.lower()

        image_id = f"IMG_{len(images) + 1:05d}"

        images.append({
            "id": image_id,
            "path": path,
            "filename": path.name,
            "relative_path": str(relative_path),
            "category": category,
            "source_type": source_type,
            "is_banner": is_banner,
            "extension": path.suffix.lower()
        })

    stats = defaultdict(int)

    for img in images:
        stats[(img["source_type"], img["category"])] += 1

    print(f"\n图片总数：{len(images)}")

    print("\n素材分类统计：")

    for (source_type, category), count in sorted(stats.items()):
        print(f"{source_type} / {category}: {count}张")

    banner_count = sum(1 for img in images if img["is_banner"])

    print(f"\nBanner图片：{banner_count}张")

    return images


# ============================================================
# 4. 建立图片索引
# ============================================================

def build_image_index(images):

    print("\n========== 建立图片索引 ==========")

    index = {
        "total": len(images),
        "categories": {},
        "source_types": {},
        "images": []
    }

    for img in images:

        source_type = img["source_type"]
        category = img["category"]

        index["source_types"][source_type] = (
            index["source_types"].get(source_type, 0) + 1
        )

        index["categories"][category] = (
            index["categories"].get(category, 0) + 1
        )

        index["images"].append({
            "id": img["id"],
            "filename": img["filename"],
            "relative_path": img["relative_path"],
            "category": category,
            "source_type": source_type,
            "is_banner": img["is_banner"]
        })

    save_json(index, WORK_DIR / "image_index.json")

    print(f"图片索引已保存：{WORK_DIR / 'image_index.json'}")

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

    # 每个区域独立建立任务
    design_sections = [
        ("PC端", design.get("pc_design", {}).get("materials", [])),
        ("落地页", design.get("landing_page", {}).get("modules", []))
    ]

    for region, items in design_sections:

        print(f"\n正在处理：{region}")

        for item in items:

            if item.get("required") is not True:
                continue

            item_id = str(item.get("id", "")).lower()
            item_name = str(item.get("name", "")).lower()

            # 轮播图不参与AI选图
            if (
                "banner" in item_id
                or "banner" in item_name
                or "轮播" in item_id
                or "轮播" in item_name
                or "carousel" in item_id
                or "carousel" in item_name
            ):
                print(f"跳过AI选图，后续直接复制轮播图：{item.get('name')}")
                continue

            if is_skip_design_item(item):
                print(f"跳过不需要图片的模块：{item.get('name')}")
                continue

            if is_skip_design_item(item):
                print(f"跳过不需要图片的模块：{item.get('name')}")
                continue

            item_id = str(item.get("id", "")).lower()
            item_name = item.get("name", item_id)

            # ------------------------------------------------
            # 产品主图：每个设计项只选择一张
            # 不再按产品分类生成多张任务
            # ------------------------------------------------
            if is_product_main(item):

                targets.append({
                    "target_id": f"{region}_product_main",
                    "name": "产品主图",
                    "design_type": region,
                    "region": region,
                    "category": "不限",
                    "source_type": "产品",
                    "selection_rule": "所有产品中选择一张最具代表性的产品图",
                    "requirements": item
                })

                continue

            # ------------------------------------------------
            # 产品细节图：每个设计项一张
            # ------------------------------------------------
            if is_product_detail(item):

                targets.append({
                    "target_id": f"{region}_{item_id}",
                    "name": item_name,
                    "design_type": region,
                    "region": region,
                    "category": "不限",
                    "source_type": "产品",
                    "selection_rule": "优先选择产品细节特写，没有合适细节时选择好看的产品图",
                    "requirements": item
                })

                continue

            # ------------------------------------------------
            # 产品优势图：每个产品分类一张
            # ------------------------------------------------
            if is_product_advantage(item):

                for category in product_categories:

                    targets.append({
                        "target_id": f"{region}_{item_id}_{category}",
                        "name": f"{item_name}_{category}",
                        "design_type": region,
                        "region": region,
                        "category": category,
                        "source_type": "产品",
                        "selection_rule": "每个产品分类选择一张代表性图片",
                        "requirements": item
                    })

                continue

            # ------------------------------------------------
            # 产品服务范围：按分类选择
            # ------------------------------------------------
            if is_product_service(item):

                for category in product_categories:

                    targets.append({
                        "target_id": f"{region}_{item_id}_{category}",
                        "name": f"{item_name}_{category}",
                        "design_type": region,
                        "region": region,
                        "category": category,
                        "source_type": "产品",
                        "selection_rule": "严格按照产品分类选择图片",
                        "requirements": item
                    })

                continue

            # ------------------------------------------------
            # 普通设计项目：每个项目一张
            # ------------------------------------------------
            targets.append({
                "target_id": f"{region}_{item_id}",
                "name": item_name,
                "design_type": region,
                "region": region,
                "category": "不限",
                "source_type": "不限",
                "selection_rule": "选择最适合设计用途的图片",
                "requirements": item
            })

    print(f"\n需要筛选的设计项目：{len(targets)}个")

    for target in targets:
        print(
            f"  {target['region']} / "
            f"{target['name']} / "
            f"{target['category']}"
        )

    return targets


# ============================================================
# 6. 生成图片索引图
# ============================================================

def get_font(size=16):

    font_paths = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf"
    ]

    for font_path in font_paths:

        if Path(font_path).exists():

            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                pass

    return ImageFont.load_default()


def create_contact_sheets(images):

    print("\n========== 生成图片索引图 ==========")

    WORK_DIR.mkdir(parents=True, exist_ok=True)

    sheet_paths = []

    cell_width = 260
    cell_height = 205

    font = get_font(15)

    for page_index in range(0, len(images), PAGE_SIZE):

        page_images = images[
            page_index:page_index + PAGE_SIZE
        ]

        sheet = Image.new(
            "RGB",
            (SHEET_COLS * cell_width, SHEET_ROWS * cell_height),
            "#F5F5F5"
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

                paste_x = x + (cell_width - image.width) // 2
                paste_y = y + 5

                sheet.paste(image, (paste_x, paste_y))

                draw.text(
                    (x + 8, y + 160),
                    item["id"],
                    fill="#D00000",
                    font=font
                )

                draw.text(
                    (x + 8, y + 181),
                    item["category"][:20],
                    fill="#222222",
                    font=font
                )

            except Exception as e:

                print(f"图片读取失败：{item['path']}，{e}")

        sheet_path = (
            WORK_DIR /
            f"sheet_{page_index // PAGE_SIZE + 1:03d}.jpg"
        )

        sheet.save(sheet_path, quality=85)

        sheet_paths.append(sheet_path)

        print(f"已生成：{sheet_path.name}")

    return sheet_paths


# ============================================================
# 7. 企业资料
# ============================================================

def load_company_info():

    company = load_json(COMPANY_JSON)

    company_text = json.dumps(
        company,
        ensure_ascii=False,
        indent=2
    )

    return company_text[:12000]


# ============================================================
# 8. 调用视觉模型筛选图片
# ============================================================

def select_materials(
    images,
    targets,
    sheet_paths,
    company_info
):

    print("\n========== 开始智能素材筛选 ==========")

    # Banner 不参与AI选图
    candidate_images = [
        img for img in images
        if not img["is_banner"]
    ]

    image_manifest = [
        {
            "id": img["id"],
            "filename": img["filename"],
            "relative_path": img["relative_path"],
            "category": img["category"],
            "source_type": img["source_type"]
        }
        for img in candidate_images
    ]

    target_info = [
        {
            "target_id": target["target_id"],
            "name": target["name"],
            "design_type": target["design_type"],
            "region": target["region"],
            "category": target["category"],
            "source_type": target["source_type"],
            "selection_rule": target["selection_rule"],
            "requirements": target["requirements"]
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

1. 每个设计任务只选择一张图片。

2. PC端内部不能重复使用图片。

3. 落地页内部不能重复使用图片。

4. PC端和落地页之间允许使用同一张图片。

5. 产品主图：
   每个区域只需要一张，不管有几个产品分类。
   从全部产品图片中选一张最有代表性、
   画面美观、主体清晰的产品图。

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

8. 产品优势图：
   每个产品分类对应一个任务。
   每个分类必须从对应分类的图片中选择一张。
   不能跨分类选择。

9. 产品及服务范围：
   必须严格按照对应产品分类选择。

10. 产品类任务必须来自产品图片，
    不允许使用工程案例图片代替产品图片。

11. Logo必须是真正的企业Logo，
    没有合适Logo时返回null，不允许拿产品图代替Logo。

12. 工程案例图片仅适用于工程案例或相关企业场景，
    不能当作产品实物图。

13. 首屏或头图优先选择主体突出、构图好、
    画面美观的图片。

14. 企业优势优先选择能体现生产、施工、
    团队、经营能力的企业素材。
    如果企业图片不足，可以考虑产品图片，
    但必须真实符合设计用途。

15. 联系我们，联系咨询不需要选择图。

16. 每个任务都应尽量选择不同图片，
    但PC端与落地页之间允许重复。

17. 只能返回图片索引中真实存在的ID。

18. 不确定的图片可以返回null，
    但公司介绍图、产品主图、产品细节图、
    产品优势图尽量按规则使用图片兜底。

========================
五、输出格式
========================

只返回JSON，不要返回Markdown。

格式：

{{
  "selections": [
    {{
      "target_id": "PC端_product_main",
      "selected_id": "IMG_00001",
      "reason": "选择理由",
      "confidence": 0.95
    }}
  ]
}}

所有target_id必须返回结果。
selected_id可以为null。
"""

    content = [
        {
            "type": "text",
            "text": prompt
        }
    ]

    # 添加索引图
    for sheet_path in sheet_paths:

        image_data = encode_image(sheet_path)

        content.append({
            "type": "image_url",
            "image_url": {
                "url": "data:image/jpeg;base64," + image_data,
                "detail": "high"
            }
        })

    print(f"候选图片：{len(candidate_images)}张")
    print(f"设计任务：{len(targets)}个")
    print(f"索引图：{len(sheet_paths)}张")

    print("正在请求视觉理解模型...")

    response = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[
            {
                "role": "user",
                "content": content
            }
        ],
        temperature=TEMPERATURE
    )

    result_text = response.choices[0].message.content

    if not result_text:
        raise ValueError("模型没有返回内容")

    result_text = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        result_text.strip(),
        flags=re.IGNORECASE
    )

    try:
        result = json.loads(result_text)

    except json.JSONDecodeError:

        print("模型原始返回：")
        print(result_text)

        raise ValueError("模型返回的内容不是合法JSON")

    return result


# ============================================================
# 9. 校验选图结果
# ============================================================

def validate_selections(
    selection_result,
    images,
    targets
):

    print("\n========== 校验筛选结果 ==========")

    image_map = {
        img["id"]: img
        for img in images
    }

    target_map = {
        target["target_id"]: target
        for target in targets
    }

    selection_map = {}

    for item in selection_result.get("selections", []):

        selection_map[item.get("target_id")] = item

    valid_results = []

    # PC端和落地页分别维护已使用图片
    used_images_by_region = {
        "PC端": set(),
        "落地页": set()
    }

    for target in targets:

        target_id = target["target_id"]

        selection = selection_map.get(target_id)

        if not selection:

            print(f"缺少任务结果：{target['name']}")
            continue

        selected_id = selection.get("selected_id")

        if not selected_id:

            print(f"未找到合适素材：{target['name']}")
            continue

        image_info = image_map.get(selected_id)

        if not image_info:

            print(f"无效图片ID：{selected_id}")
            continue

        if image_info["is_banner"]:

            print(f"Banner素材禁止参与选图：{selected_id}")
            continue

        # ----------------------------------------------------
        # 分类校验
        # ----------------------------------------------------

        if target["category"] != "不限":

            if image_info["category"] != target["category"]:

                print(
                    f"产品分类不匹配：{target['name']} "
                    f"要求={target['category']} "
                    f"实际={image_info['category']}"
                )

                continue

        # ----------------------------------------------------
        # 来源校验
        # ----------------------------------------------------

        if target["source_type"] == "产品":

            if image_info["source_type"] != "产品":

                print(
                    f"素材来源不匹配：{target['name']}"
                )

                continue

        # ----------------------------------------------------
        # 区域内部去重
        # PC端与落地页不互相限制
        # ----------------------------------------------------

        region = target["region"]

        used_images = used_images_by_region[region]

        if AVOID_DUPLICATES and selected_id in used_images:

            print(
                f"{region}内部图片重复，跳过：{selected_id}"
            )

            continue

        used_images.add(selected_id)

        valid_results.append({
            "target_id": target_id,
            "selected_id": selected_id,
            "reason": selection.get("reason", ""),
            "confidence": selection.get("confidence", 0)
        })

        print(
            f"通过：{region} / {target['name']} "
            f"-> {selected_id}"
        )

    return valid_results


# ============================================================
# 10. 复制Banner图片
# ============================================================

def copy_banner_images(image_index):
    """
    轮播图只复制到PC端，不复制到落地页
    """
    banner_images = []

    for image in image_index:
        image_path = Path(image["path"])

        if "banner" in image_path.name.lower():
            banner_images.append(image_path)

    if not banner_images:
        print("没有找到轮播图")
        return

    # 仅复制到 PC 端目录
    pc_banner_dir = OUTPUT_DIR / "PC端" / "轮播图"
    pc_banner_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n找到 {len(banner_images)} 张轮播图，仅复制到PC端")

    for index, image_path in enumerate(banner_images, 1):
        destination = pc_banner_dir / image_path.name

        shutil.copy2(image_path, destination)

        print(f"已复制轮播图：{image_path.name}")

# ============================================================
# 11. 归档选中图片
# ============================================================

def save_selected_materials(
    selections,
    images,
    targets
):

    print("\n========== 开始归档素材 ==========")

    image_map = {
        img["id"]: img
        for img in images
    }

    target_map = {
        target["target_id"]: target
        for target in targets
    }

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    report = []

    for selection in selections:

        target_id = selection["target_id"]

        selected_id = selection["selected_id"]

        target = target_map[target_id]

        image_info = image_map[selected_id]

        source_path = image_info["path"]

        region = target["region"]

        # 目录结构：selected_materials/PC端/设计项名称/
        target_dir = (
            get_region_folder(region) /
            safe_name(target["name"])
        )

        target_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        destination_path = target_dir / source_path.name

        if destination_path.exists():

            destination_path = (
                target_dir /
                f"{selected_id}{source_path.suffix}"
            )

        # 只复制原图
        shutil.copy2(
            source_path,
            destination_path
        )

        result = {
            "target_id": target_id,
            "design_name": target["name"],
            "design_type": target["design_type"],
            "region": region,
            "category": target["category"],
            "selected_id": selected_id,
            "source_image": image_info["relative_path"],
            "selected_image": str(
                destination_path.relative_to(BASE_DIR)
            ),
            "reason": selection["reason"],
            "confidence": selection["confidence"]
        }

        report.append(result)

        print(f"归档成功：{region} / {target['name']}")
        print(f"  原图：{image_info['relative_path']}")
        print(f"  目标：{destination_path}")

    # --------------------------------------------------------
    # 复制Banner
    # --------------------------------------------------------

    banner_report = copy_banner_images(images)

    # --------------------------------------------------------
    # 保存报告
    # --------------------------------------------------------

    save_json(
        report,
        OUTPUT_DIR / "selection_report.json"
    )

    save_json(
        banner_report,
        OUTPUT_DIR / "banner_copy_report.json"
    )

    selected_targets = {
        item["target_id"]
        for item in selections
    }

    missing_targets = [
        {
            "target_id": target["target_id"],
            "design_name": target["name"],
            "region": target["region"],
            "category": target["category"],
            "reason": "没有找到合适素材或校验未通过"
        }
        for target in targets
        if target["target_id"] not in selected_targets
    ]

    save_json(
        missing_targets,
        OUTPUT_DIR / "missing_materials.json"
    )

    print("\n素材归档完成")

    print(f"成功归档：{len(report)}个")
    print(f"Banner复制：{len(banner_report)}张")
    print(f"未找到素材：{len(missing_targets)}个")
    print(f"输出目录：{OUTPUT_DIR}")

    return report


# ============================================================
# 12. 主程序
# ============================================================

def main():

    print("=" * 55)
    print("企业营销设计素材智能筛选系统")
    print("=" * 55)

    # --------------------------------------------------------
    # 配置检查
    # --------------------------------------------------------

    if not API_KEY:
        raise ValueError("config.py 中没有配置 API_KEY")

    if not BASE_URL:
        raise ValueError("config.py 中没有配置 BASE_URL")

    if not VISION_MODEL:
        raise ValueError("config.py 中没有配置 VISION_MODEL")

    if not INPUT_DIR.exists():
        raise FileNotFoundError(
            f"找不到输入目录：{INPUT_DIR}"
        )

    if not DESIGN_JSON.exists():
        raise FileNotFoundError(
            f"找不到设计规范：{DESIGN_JSON}"
        )

    # --------------------------------------------------------
    # 1. 读取资料
    # --------------------------------------------------------

    print("\n[1/7] 读取设计规范...")

    design = load_json(DESIGN_JSON)

    company_info = load_company_info()

    # --------------------------------------------------------
    # 2. 扫描图片
    # --------------------------------------------------------

    print("\n[2/7] 扫描图片...")

    images = scan_images()

    if not images:
        print("没有找到任何图片")
        return

    # --------------------------------------------------------
    # 3. 建立索引
    # --------------------------------------------------------

    print("\n[3/7] 建立图片索引...")

    build_image_index(images)

    # --------------------------------------------------------
    # 4. 生成筛选任务
    # --------------------------------------------------------

    print("\n[4/7] 生成设计任务...")

    targets = build_targets(
        design,
        images
    )

    if not targets:
        print("没有需要筛选的设计项目")
        return

    # --------------------------------------------------------
    # 5. 生成图片索引图
    # --------------------------------------------------------

    print("\n[5/7] 生成图片索引图...")

    candidate_images = [
        img for img in images
        if not img["is_banner"]
    ]

    if not candidate_images:
        print("没有可用于AI筛选的候选图片")
        return

    sheet_paths = create_contact_sheets(
        candidate_images
    )

    # --------------------------------------------------------
    # 6. 调用模型
    # --------------------------------------------------------

    print("\n[6/7] 调用视觉理解模型...")

    selection_result = select_materials(
        images,
        targets,
        sheet_paths,
        company_info
    )

    save_json(
        selection_result,
        WORK_DIR / "model_selection_result.json"
    )

    # --------------------------------------------------------
    # 7. 校验与归档
    # --------------------------------------------------------

    print("\n[7/7] 校验并归档...")

    valid_selections = validate_selections(
        selection_result,
        images,
        targets
    )

    report = save_selected_materials(
        valid_selections,
        images,
        targets
    )

    print("\n" + "=" * 55)
    print("全部处理完成！")
    print(f"设计任务总数：{len(targets)}")
    print(f"成功选中数量：{len(report)}")
    print(f"最终素材目录：{OUTPUT_DIR}")
    print("=" * 55)


if __name__ == "__main__":
    main()
