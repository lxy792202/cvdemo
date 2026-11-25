"""Stage 4：根据图片、企业资料和设计规范生成营销设计图。

模式：
- seedream：实景图密集模块，Qwen 出方案 + Seedream 出带字成品；
- logo / pil_*：文字密集模块，PIL 精确排版；
- pil_product_grid：一页多产品，复用 Stage 2 主图卡片拼版。
"""

import base64
import hashlib
import io
import json
import re
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image

from pipeline import common
from pipeline import design_renderer as renderer
from pipeline.stage1_process import load_processed
from pipeline.stage2_main_images import load_main_images


# ============================================================
# 配置
# ============================================================

LP_DEFAULT_SIZES = {
    "hero": (750, 1000),
    "product_service": (750, 0),  # 高度动态计算
    "core_advantages": (750, 1200),
    "product_details": (750, 1200),
    "service_process": (750, 900),
    "contact": (750, 800),
}

PRODUCT_GRID_CARD_HEIGHT = 300
PRODUCT_GRID_COLUMNS = 2

MIN_SEEDREAM_EDGE = 1280
MAX_SEEDREAM_EDGE = 4096


# ============================================================
# 数据加载
# ============================================================

def load_company():
    data = common.load_json(common.COMPANY_JSON)
    info = data.get("company", {})
    view = dict(info)
    view["core_advantages"] = data.get("core_advantages", [])
    view["service_process"] = data.get("service_process", [])
    view["company_strength"] = data.get("company_strength", {})
    return data, view


def load_selection_manifest():
    path = common.SELECTED_MATERIALS_DIR / "selection_manifest.json"
    if path.exists():
        return common.load_json(path)
    fallback = common.load_manifest("selected")
    if fallback:
        return fallback
    raise FileNotFoundError(
        f"找不到选图清单：{path}，请先运行 Stage 3"
    )


def selection_map(manifest):
    return {
        (item.get("region"), item.get("module_id")): item
        for item in manifest.get("selections", [])
    }


def selection_images(selection):
    if not selection:
        return []
    return [
        common.PROJECT_ROOT / img["out_rel"]
        for img in selection.get("images", [])
        if (common.PROJECT_ROOT / img["out_rel"]).exists()
    ]


# ============================================================
# 尺寸解析
# ============================================================

def _parse_height(value):
    if isinstance(value, (int, float)):
        return int(value)
    match = re.findall(r"\d+", str(value))
    if not match:
        return None
    numbers = [int(n) for n in match]
    return sum(numbers) // len(numbers)


def resolve_size(region, module_id, cfg, card_count=None, columns=2):
    """返回 (width, height, source)。"""

    cfg = cfg or {}
    size = cfg.get("size") or {}
    width = _parse_height(size.get("width"))
    height = _parse_height(size.get("height"))

    if module_id == "product_service":
        width = width or 750
        count = card_count or 1
        height = renderer.product_grid_height(
            width, count, columns=columns,
            card_height=PRODUCT_GRID_CARD_HEIGHT,
        )
        return width, height, "derived"

    if width and height:
        return width, height, "explicit"

    if region == "落地页":
        default = LP_DEFAULT_SIZES.get(module_id)
        if default:
            width = width or default[0]
            height = height or default[1]
            source = "explicit" if (width and height and size) else "default"
            return width, height, source

    if width and not height:
        default = LP_DEFAULT_SIZES.get(module_id, (width, 1000))
        return width, default[1], "derived"

    if module_id == "home_advantages":
        return 1420, 1200, "derived"

    return width or 800, height or 600, "default"


def seedream_size(width, height):
    """把目标尺寸换算成 Seedream 可接受的 WxH；不可行时返回 None。"""
    ratio = width / max(1, height)

    if ratio >= 1:
        out_h = MIN_SEEDREAM_EDGE
        out_w = out_h * ratio
    else:
        out_w = MIN_SEEDREAM_EDGE
        out_h = out_w / ratio

    longest = max(out_w, out_h)
    if longest > MAX_SEEDREAM_EDGE:
        scale = MAX_SEEDREAM_EDGE / longest
        out_w *= scale
        out_h *= scale

    if min(out_w, out_h) < MIN_SEEDREAM_EDGE - 1:
        return None

    if ratio > 3.2 or ratio < 1 / 3.2:
        return None

    out_w = int(round(out_w / 8) * 8)
    out_h = int(round(out_h / 8) * 8)
    return f"{out_w}x{out_h}"


def load_capabilities():
    return common.load_json(
        common.MANIFEST_DIR / "capabilities.json", default={}
    )


def save_capabilities(data):
    common.save_json(data, common.MANIFEST_DIR / "capabilities.json")


# ============================================================
# 产品上下文
# ============================================================

def product_context(company_data, category):
    product = common.find_product(company_data, category) if category else None
    if product:
        return {
            "id": product.get("id"),
            "name": product.get("name", category),
            "category": product.get("category", ""),
            "advantages": [
                adv.get("title") or adv.get("description", "")
                for adv in product.get("advantages", [])
                if adv.get("title") or adv.get("description")
            ][:4],
            "known": True,
        }

    generic = [
        item.get("title", "")
        for item in company_data.get("core_advantages", [])
        if item.get("title")
    ][:3]

    return {
        "id": None,
        "name": category,
        "category": "",
        "advantages": generic,
        "known": False,
    }


def product_card(card):
    return {
        "image": str(card["image"]) if card.get("image") else None,
        "name": card.get("name", ""),
        "advantages": card.get("advantages", [])[:2],
        "scope": card.get("scope", ""),
    }


# ============================================================
# 构建任务计划
# ============================================================

def _filename(cfg, region, name, company_name, index=None):
    template = (cfg or {}).get("filename")
    if template:
        try:
            value = template.format(
                company_name=company_name,
                index=index if index is not None else 1,
            )
        except (KeyError, IndexError):
            value = template
    else:
        suffix = f"_{index}" if index is not None else ""
        value = f"{region}_{name}{suffix}.png"
    return common.safe_name(value)


def _module(design, region, module_id):
    cfg = common.module_find(design, region, module_id)
    if not cfg:
        print(f"警告：design.json 中没有 {region} / {module_id}，跳过")
    return cfg


def build_plan(design=None, company_data=None, main_images=None,
               selections=None, processed_images=None):

    design = design or common.load_json(common.DESIGN_JSON)
    company_data, company_view = load_company()
    main_images = main_images if main_images is not None else load_main_images()
    selections = selections if selections is not None else selection_map(
        load_selection_manifest()
    )
    if processed_images is None:
        _, processed_images = load_processed()

    brand = dict(company_data.get("brand_style", {}))
    brand["primary_color"] = common.load_design_color(
        brand.get("primary_color", "#C50303")
    )
    company_data["brand_style"] = brand
    palette = renderer.palette_from_brand(brand)
    company_name = company_view.get("name", "企业")
    short_name = company_view.get("short_name", "")
    phone = company_view.get("phone", "")

    # 落地页长图拼接：所有模块宽度必须完全一致
    lp_canvas_width = int(
        design.get("landing_page", {}).get("canvas_width", 750)
    )

    product_categories = sorted(main_images.keys())

    def selected(region, module_id):
        item = selections.get((region, module_id))
        return selection_images(item)

    def main_image(category):
        item = main_images.get(category) or {}
        rel = item.get("four_three_rel")
        if rel and common.abs_path(rel).exists():
            return common.abs_path(rel)
        return None

    plan = []

    def add(region, module_id, mode, name, size, cfg, images=None,
            product=None, extra=None, index=None, filename=None):

        if cfg is None:
            return

        width, height = size[0], size[1]
        if region == "落地页":
            width = lp_canvas_width

        entry = {
            "region": region,
            "module_id": module_id,
            "mode": mode,
            "name": name,
            "width": width,
            "height": height,
            "filename": filename or _filename(
                cfg, region, name, company_name, index=index
            ),
            "images": [str(p) for p in (images or []) if p],
            "product": product,
            "module_config": cfg,
            "extra": extra or {},
        }
        plan.append(entry)

    # -------------------- PC 端 --------------------

    cfg = _module(design, "PC端", "logo")
    size = resolve_size("PC端", "logo", cfg)
    add("PC端", "logo", "logo", "公司Logo", size[:2], cfg)

    cfg = _module(design, "PC端", "company_intro")
    if cfg:
        imgs = selected("PC端", "company_intro")
        if not imgs and product_categories:
            imgs = [main_image(product_categories[0])]
        size = resolve_size("PC端", "company_intro", cfg)
        add("PC端", "company_intro", "seedream", "公司介绍图",
            size[:2], cfg, images=imgs[:1],
            product={"name": "企业实力", "advantages": []})

    cfg = _module(design, "PC端", "banner")
    if cfg:
        size = resolve_size("PC端", "banner", cfg)
        main_category = "假山" if "假山" in main_images else (
            product_categories[0] if product_categories else None
        )
        hero_imgs = [main_image(main_category)] if main_category else []
        hero_imgs += selected("落地页", "hero")
        add("PC端", "banner_1", "seedream", "首页轮播图_主推产品",
            size[:2], cfg, images=hero_imgs[:1],
            product=product_context(company_data, main_category),
            index=1,
            filename=_filename(cfg, "PC端", "首页轮播图", company_name, 1))

        cards = []
        for category in product_categories:
            product = product_context(company_data, category)
            image = main_image(category)
            cards.append({
                "image": image,
                "name": product["name"],
                "advantages": product["advantages"],
                "scope": "按需定制 · 上门施工",
            })
        add("PC端", "banner_2", "pil_product_grid", "首页轮播图_多产品组合",
            size[:2], cfg,
            product={"name": "多产品组合", "advantages": []},
            index=2,
            filename=_filename(cfg, "PC端", "首页轮播图", company_name, 2),
            extra={
                "cards": [product_card(c) for c in cards],
                "title": "多产品组合 · 主营产品介绍",
                "subtitle": f"{short_name} · 设计 / 定制 / 施工一站式",
                "columns": min(6, max(1, len(cards))),
                "card_height": 330,
                "show_footer": False,
            })

    cfg = _module(design, "PC端", "product_main")
    if cfg:
        size = resolve_size("PC端", "product_main", cfg)
        for index, category in enumerate(product_categories, start=1):
            image = main_image(category)
            if not image:
                print(f"警告：{category} 没有 Stage 2 主图，跳过产品主图")
                continue
            add("PC端", f"product_main_{common.safe_name(category)}",
                "seedream", f"产品主图_{category}", size[:2], cfg,
                images=[image],
                product=product_context(company_data, category),
                index=index,
                filename=_filename(
                    cfg, "PC端", "产品主图", company_name, index
                ))

    cfg = _module(design, "PC端", "home_advantages")
    if cfg:
        size = resolve_size("PC端", "home_advantages", cfg)
        imgs = selected("PC端", "home_advantages")
        add("PC端", "home_advantages", "pil_advantage_board",
            "首页企业优势", size[:2], cfg, images=imgs[:1])

    cfg = _module(design, "PC端", "contact")
    if cfg:
        size = resolve_size("PC端", "contact", cfg)
        bg = selected("PC端", "home_advantages") or selected(
            "PC端", "company_intro"
        )
        if not bg and product_categories:
            bg = [main_image(product_categories[0])]
        bg = [img for img in bg if img]
        add("PC端", "contact", "pil_contact", "联系我们",
            size[:2], cfg, images=bg[:1])

    # -------------------- 落地页 --------------------

    cfg = _module(design, "落地页", "hero")
    if cfg:
        imgs = selected("落地页", "hero")
        if not imgs and product_categories:
            imgs = [main_image(product_categories[0])]
        size = resolve_size("落地页", "hero", cfg)
        add("落地页", "hero", "seedream", "首屏头图", size[:2], cfg,
            images=imgs[:1],
            product=product_context(company_data, product_categories[0]
                                    if product_categories else None))

    cfg = _module(design, "落地页", "product_service")
    if cfg:
        selection = selections.get(("落地页", "product_service"))
        selected_by_category = {
            img["category"]: common.PROJECT_ROOT / img["out_rel"]
            for img in (selection or {}).get("images", [])
            if (common.PROJECT_ROOT / img["out_rel"]).exists()
        }

        cards = []
        for category in product_categories:
            product = product_context(company_data, category)
            image = selected_by_category.get(category) or main_image(category)
            cards.append({
                "image": image,
                "name": product["name"],
                "advantages": product["advantages"],
                "scope": "按需定制 · 上门施工",
            })

        size = resolve_size(
            "落地页", "product_service", cfg,
            card_count=len(cards), columns=PRODUCT_GRID_COLUMNS
        )
        add("落地页", "product_service", "pil_product_grid",
            "产品及服务范围", size[:2], cfg,
            product={"name": "产品及服务范围", "advantages": []},
            extra={
                "cards": [product_card(c) for c in cards],
                "title": "产品及服务范围",
                "subtitle": (
                    f"{short_name} · {company_view.get('main_business', '')}"
                ),
                "columns": PRODUCT_GRID_COLUMNS,
                "card_height": PRODUCT_GRID_CARD_HEIGHT,
                "show_footer": True,
            })

    cfg = _module(design, "落地页", "core_advantages")
    if cfg:
        imgs = selected("落地页", "core_advantages")
        size = resolve_size("落地页", "core_advantages", cfg)
        add("落地页", "core_advantages", "pil_core_advantages",
            "企业核心优势", size[:2], cfg, images=imgs[:1])

    cfg = _module(design, "落地页", "product_details")
    if cfg:
        imgs = selected("落地页", "product_details")
        if not imgs and product_categories:
            imgs = [main_image(product_categories[0])]
        size = resolve_size("落地页", "product_details", cfg)
        add("落地页", "product_details", "seedream", "产品细节及优势",
            size[:2], cfg, images=imgs[:1],
            product=product_context(company_data, product_categories[0]
                                    if product_categories else None))

    cfg = _module(design, "落地页", "service_process")
    if cfg:
        size = resolve_size("落地页", "service_process", cfg)
        add("落地页", "service_process", "pil_process",
            "服务流程", size[:2], cfg)

    cfg = _module(design, "落地页", "contact")
    if cfg:
        size = resolve_size("落地页", "contact", cfg)
        bg = selected("落地页", "hero") or selected("落地页", "core_advantages")
        add("落地页", "contact", "pil_contact", "联系咨询",
            size[:2], cfg, images=bg[:1])

    for entry in plan:
        entry["palette_source"] = "brand_style"
        entry["company_name"] = company_name
        entry["phone"] = phone

    return plan, {
        "company_data": company_data,
        "company_view": company_view,
        "palette": palette,
        "product_categories": product_categories,
    }


# ============================================================
# 通用提示词
# ============================================================

SYSTEM_PROMPT = """
你是一名资深商业视觉设计师、品牌设计师和营销创意总监。

你的任务是根据用户提供的图片、企业资料、产品资料、设计规范和本次设计任务，
制定一份高质量、可直接交给 AI 图片生成模型执行的设计方案。

## 一、设计目标

围绕本次任务的实际目标创意设计：
- 画面应突出什么主体
- 哪些信息需要展示、哪些需要弱化
- 如何组织视觉层次
- 如何安排图片、文字和背景
- 如何形成有吸引力的商业视觉效果

不要把所有设计图都做成相同的宣传海报。

## 二、禁止模板化设计

禁止机械套用固定布局：标题位置、主体位置、图片与文字比例、
卡片排列、装饰元素、背景结构都不要每次相同。

根据当前任务内容主动选择最合适的设计方向，
在保证信息清晰和设计美感的前提下自由探索构图与排版。

## 三、视觉设计原则

- 美观、简洁、有设计感
- 视觉层次清晰，主次明确
- 合理留白，避免拥挤和杂乱
- 避免传统、廉价、机械的宣传单风格
- 避免所有元素简单堆叠在画面中

## 四、品牌统一性

优先使用企业提供的主色、辅助色和视觉关键词。
品牌一致性体现在视觉气质、颜色体系、设计品质和字体风格，
而不是重复同一套版式。

## 五、图片素材使用原则

用户提供的图片是独立的真实产品/场景素材，不是默认背景。

根据图片实际内容和设计目标决定：
- 图片是否作为主要视觉主体
- 如何裁切、如何排列、是否需要局部展示
- 图片与文字如何组合

必须保持真实主体的外观、结构、颜色、材质和重要细节。
不得无依据地改变真实产品、建筑、人物或其他主体的关键特征。

## 六、信息与文字

- 优先展示最重要的信息，避免文字堆积
- 文案简洁、有营销表达力，不重复
- 不虚构企业信息、产品参数、案例、资质和联系方式
- 不擅自添加资料中没有提供的信息
- 文字必须清晰易读，不添加无意义英文、乱码或装饰性文字

## 七、高端海报级设计感

整体要像高端品牌 campaign 主视觉、杂志封面或地产海报，
而不是信息模块的简单排列、电商详情页或企业 PPT。

主动使用精致的小设计元素提升画面层次：
- 大标题与层级化小字的对比、竖排小字、中英文混排的装饰字段
- 细线分隔、角标、序号、页眉页脚线、留白、压边
- 印章式小色块、局部传统纹样（云纹/回纹）、浅纸纹/颗粒质感
- 图片局部放大、非对称构图、色块穿插、叠层、光影和渐变

小元素要克制、精致、服务于视觉层级，不能堆砌。
品牌红作为主设计块大面积使用（约 30%-50%），照片保持原色。
禁止把文字压在复杂照片纹理上，保证信息清晰可读。

## 八、输出要求

输出一份具体、完整、可执行的图片生成设计提示词。

必须明确描述：
- 本次设计的视觉主题
- 画面主体及其视觉重心
- 整体构图与空间安排
- 图片素材的使用方式
- 背景设计
- 文字层级与排版
- 品牌色的使用
- 视觉风格和细节
- 如何避免普通模板化设计

只输出最终设计提示词，不输出分析过程。
"""


def build_user_content(entry, company_data, image_labels, design_notes=""):
    brand = company_data.get("brand_style", {})
    company = company_data.get("company", {})
    module_cfg = entry.get("module_config", {})
    product = entry.get("product") or {}

    context = {
        "company": company,
        "brand_style": brand,
        "company_strength": company_data.get("company_strength", {}),
        "core_advantages": company_data.get("core_advantages", []),
        "service_process": company_data.get("service_process", []),
        "content_generation_rules": company_data.get(
            "content_generation_rules", {}
        ),
    }

    image_text = "无图片素材，纯版式设计。"
    if image_labels:
        image_text = "\n".join(
            f"- 图片{i}：{label}"
            for i, label in enumerate(image_labels, start=1)
        )

    mode_text = {
        "seedream": "实景图片营销设计图（图片为主体素材）",
        "logo": "企业文字 Logo",
        "pil_product_grid": "一页多产品展示",
    }.get(entry["mode"], "版式设计")

    return f"""
请根据以下资料，为本次设计任务制定图片生成设计方案。

【本次任务】
投放端：{entry['region']}
设计项：{entry['name']}
任务类型：{mode_text}
画布尺寸：{entry['width']}×{entry['height']}px

【画布规格（最高优先级）】
宽度：{entry['width']}px
高度：{entry['height']}px
整体色调：品牌红 {brand.get('primary_color', '#C50303')} 作为主设计色（约 30%-50%），照片保持原色
不允许自行修改画布比例和尺寸。

【图片设计规范】
{json.dumps(module_cfg, ensure_ascii=False, indent=2)}

【企业资料】
{json.dumps(context, ensure_ascii=False, indent=2)}

【产品资料】
{json.dumps(product, ensure_ascii=False, indent=2)}

【图片素材说明】
{image_text}

【用户补充设计要求】
{design_notes or "无"}

【特别注意】
1. 品牌主色是 {brand.get('primary_color', '#C50303')}，
   作为主设计色使用，约占画面 30%-50%：
   用于侧边文字面板、标题带、页脚、卡片名称条、部分卡片底色等设计块。
2. 绝不能整片覆盖产品或场景照片：素材图片保持真实颜色、
   光影和材质，不加红色滤镜、不加红色遮罩。
3. 左文右图/上文下图时，文字区可以用红色大色块；
   红块与照片之间用斜切、弧形、圆角、渐变或留白自然衔接，
   避免生硬直线分割。
4. 红色区域内可以加入低对比度的中式小装饰：
   祥云/如意云纹、细线回纹角饰、双线边框、印章式小色块等，
   呼应古建园林行业气质，但要克制，不能抢主体和文字。
5. 文字和图片分开排版，文字清晰易读，整体以美观为第一优先。
6. 不要添加资料中没有提供的信息（参数、案例、荣誉、电话）。
7. 参考高端品牌海报、杂志封面、地产 campaign 主视觉的设计质感：
   大标题排版、竖排小字、细线、角标、序号、印章、留白、
   浅纸纹/颗粒、非对称构图、色块穿插、局部放大、光影层次。
   小元素要精致克制，避免堆砌和廉价模板感。
8. 不要做成企业 PPT 或电商详情页；要有完整的设计概念和艺术指导。
"""


def build_final_prompt(entry, company_data, design_prompt, image_labels):
    brand = company_data.get("brand_style", {})
    module_cfg = entry.get("module_config", {})
    product = entry.get("product") or {}

    images_text = "\n".join(
        f"- 图片{i}：{label}"
        for i, label in enumerate(image_labels, start=1)
    ) or "- 无真实图片素材，请用品牌色、图形和文字完成版式设计。"

    return f"""
请基于提供的真实素材，设计并生成一张高质量商业营销设计图。

【最高优先级：固定画布规格】
宽度：{entry['width']}px
高度：{entry['height']}px
整体色调：品牌红 {brand.get('primary_color', '#C50303')} 作为主设计色（约 30%-50%），照片保持原色
严禁修改画布方向、比例和尺寸。
若设计方案描述与本规格冲突，一律以本规格为准。

【一、本次任务】
投放端：{entry['region']}
设计项：{entry['name']}
设计规范：
{json.dumps(module_cfg, ensure_ascii=False, indent=2)}

【二、图片素材的使用方式】
{images_text}

输入图片是独立的真实素材，不是背景。
必须保留主体的真实造型、结构、颜色、材质、纹理和关键细节。
不能重新设计产品，不能凭空增加或删除产品结构。
允许调整主体在画面中的位置、大小和展示角度，但不得改变真实特征。

【三、背景设计】
重新设计海报背景，不要直接把原图铺满作为背景。
背景简洁、明亮、有品质感，可以有轻微渐变、几何色块、柔和光影，
但不能出现复杂纹理、杂乱装饰和过多视觉元素。

【四、品牌颜色（主设计色，30%-50%）】
品牌主色：{brand.get('primary_color', '#C50303')}
辅助色：{', '.join(brand.get('recommended_colors', [])[:4])}
品牌视觉关键词：{', '.join(brand.get('visual_keywords', [])[:6])}

品牌主色使用规则：
- 用于侧边文字面板、顶部标题带、页脚、卡片名称条、部分卡片底色
- 主色约占画面 30%-50%，其余用白色/浅米色/暖灰留白
- 绝不能用红色滤镜或红色遮罩覆盖产品、建筑、人物照片
- 红块与照片之间用斜切、弧形、圆角、渐变或留白过渡，禁止生硬直切
- 红色区域内可加入低对比度祥云/如意云纹、细线回纹、双线边框、
  印章式小色块等中式装饰，克制不抢主体
- 红底白字、浅底深字，保证文字清晰

【五、文字排版】
文字与图片必须作为独立内容排版。
标题醒目、层级明确；正文清晰；留白充足；不堆积文字。
不得虚构企业信息、产品参数、案例、资质和联系方式。
不得出现乱码、无意义装饰文字和错误电话号码。

【六、产品资料（只允许使用真实信息）】
{json.dumps(product, ensure_ascii=False, indent=2)}

【七、具体设计方案】
{design_prompt}

【八、高端海报设计方向】
整体必须达到高端品牌海报 / 杂志封面 / 地产 campaign 主视觉的完成度：
- 有明确的设计概念、视觉重心和非对称构图，不是模块排列
- 大标题与层级化小字形成强烈对比，可用竖排小字增加东方气质
- 适量加入精致小元素：细线分隔、角标、序号、页眉页脚线、
  印章式小色块、云纹/回纹、浅纸纹或颗粒质感、局部光影渐变
- 通过留白、压边、叠层、色块穿插、图片局部放大制造空间层次
- 品牌红作为大面积设计块（约 30%-50%），照片保持真实原色
- 小元素克制、精致、清晰，避免堆砌、廉价装饰和模板感

【九、最终质量要求】
1. 主体真实，不改变原始外观。
2. 主体清晰突出，画面有高端海报的设计感和艺术性。
3. 品牌红使用充分但合理，照片不被红色覆盖。
4. 文字清晰、层次分明、无错字、无乱码。
5. 不添加未经提供的信息和水印。
6. 整体达到可交付的品牌级视觉品质。
"""


# ============================================================
# Qwen / Seedream
# ============================================================

def analyze_design(entry, company_data, image_paths, out_dir, design_notes=""):
    labels = []
    for index, path in enumerate(image_paths, start=1):
        product = entry.get("product") or {}
        category = product.get("name") or entry["name"]
        labels.append(f"{category}（{Path(path).name}）")

    user_content = build_user_content(
        entry, company_data, labels, design_notes
    )

    content = [{"type": "text", "text": user_content}]

    for path in image_paths[:3]:
        content.append({
            "type": "image_url",
            "image_url": {"url": common.encode_image_thumb(path)},
        })

    print(f"  正在生成设计方案：{entry['region']} / {entry['name']}")

    response = common.client.chat.completions.create(
        model=common.VISION_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        temperature=0.7,
    )

    design_prompt = response.choices[0].message.content

    if not design_prompt:
        raise ValueError("Qwen 没有返回设计提示词")

    prompt_path = out_dir / "design_prompt.txt"
    common.save_text(design_prompt, prompt_path)
    print(f"  设计提示词：{prompt_path}")

    return design_prompt, labels


def call_seedream(prompt, image_path, target_size, out_path):
    """调用 Seedream 生成，再按目标尺寸 contain 到最终画布。"""

    width, height = target_size
    capabilities = load_capabilities()

    preferred = seedream_size(width, height)
    if capabilities.get("size_wxh") is False:
        preferred = None

    payload = {
        "model": common.IMAGE_MODEL,
        "prompt": prompt,
        "response_format": "b64_json",
        "output_format": "png",
        "watermark": False,
    }

    if image_path:
        payload["image"] = common.encode_image(image_path)

    url = common.BASE_URL.rstrip("/") + "/images/generations"
    headers = {
        "Authorization": f"Bearer {common.API_KEY}",
        "Content-Type": "application/json",
    }

    def request(size_value):
        body = dict(payload)
        body["size"] = size_value
        return requests.post(
            url, headers=headers, json=body, timeout=common.API_TIMEOUT
        )

    print(
        f"  正在调用 Seedream：目标 {width}×{height}，"
        f"生成尺寸 {preferred or '2K'}"
    )

    response = request(preferred or "2K")

    if preferred and not response.ok:
        print(f"  尺寸 {preferred} 失败，回退 2K：{response.text[:200]}")
        capabilities["size_wxh"] = False
        save_capabilities(capabilities)
        response = request("2K")

    elif preferred and response.ok:
        capabilities["size_wxh"] = True
        save_capabilities(capabilities)

    if not response.ok:
        raise RuntimeError(
            f"Seedream 调用失败：{response.status_code}\n{response.text[:500]}"
        )

    result = response.json()
    image_data = (result.get("data") or [{}])[0]

    if image_data.get("b64_json"):
        image_bytes = base64.b64decode(image_data["b64_json"])
    elif image_data.get("url"):
        image_response = requests.get(image_data["url"], timeout=120)
        image_response.raise_for_status()
        image_bytes = image_response.content
    else:
        raise ValueError(f"Seedream 没有返回有效图片：{result}")

    generated = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    final = common.fit_contain(generated, (width, height))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    final.save(out_path, format="PNG")

    print(f"  已生成：{out_path}")
    return out_path


def make_logo_spec(company_view, logo_cfg):
    """让 Qwen 决定 Logo 文字排版样式，失败时用确定性兜底。"""

    prompt = f"""
你是一名品牌视觉设计师。请为企业设计一个“纯文字”Logo 的排版方案。

企业资料：
{json.dumps(company_view, ensure_ascii=False, indent=2)}

Logo 设计规范：
{json.dumps(logo_cfg, ensure_ascii=False, indent=2)}

要求：
1. 纯白背景，上下两排排版，只使用文字和简单线条/色块。
2. 可使用公司简称或全称，突出一个核心优势词。
3. 必须包含真实联系电话。
4. 不得虚构信息。

只返回 JSON：
{{
  "line1": "第一排主文字",
  "line2": "第二排辅助文字",
  "emphasis": "line1 中需要品牌色强调的词",
  "phone": "电话",
  "accent_color": "#C50303",
  "line1_size": 62,
  "line2_size": 26,
  "phone_size": 42,
  "layout": "排版说明"
}}
"""

    try:
        response = common.client.chat.completions.create(
            model=common.VISION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            response_format={"type": "json_object"},
        )
        spec = common.extract_json(response.choices[0].message.content)
        print(f"  Logo 排版方案：{json.dumps(spec, ensure_ascii=False)}")
        return spec
    except Exception as exc:
        print(f"  Logo 排版 AI 失败，使用兜底方案：{exc!r}")
        return renderer.build_logo_fallback_spec(company_view)


# ============================================================
# 执行
# ============================================================

def _out_dir(entry):
    return common.DESIGN_DIR / entry["region"] / entry["module_id"]


def generate_entry(entry, env, force=False, design_notes=""):
    company_data = env["company_data"]
    company_view = env["company_view"]
    palette = env["palette"]

    out_dir = _out_dir(entry)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / entry["filename"]

    meta_path = out_path.with_suffix(".meta.json")

    if not force and out_path.exists() and meta_path.exists():
        print(f"跳过已完成：{entry['region']} / {entry['name']}")
        return {"success": True, "skipped": True, "output": common.rel_path(out_path)}

    mode = entry["mode"]
    image_paths = [Path(p) for p in entry["images"]]

    print(
        f"\n>>> {entry['region']} / {entry['name']} "
        f"[{mode}] {entry['width']}×{entry['height']}"
    )

    if mode == "logo":
        spec = make_logo_spec(company_view, entry["module_config"])
        renderer.render_logo(
            spec,
            (entry["width"], entry["height"]),
            out_path, palette, company_view,
        )
        prompt_text = json.dumps(spec, ensure_ascii=False, indent=2)

    elif mode == "pil_advantage_board":
        renderer.render_advantage_board(
            (entry["width"], entry["height"]),
            out_path, palette, company_view,
            image_path=image_paths[0] if image_paths else None,
        )
        prompt_text = "PIL: 企业核心优势 + 服务流程"

    elif mode == "pil_core_advantages":
        renderer.render_core_advantages_lp(
            (entry["width"], entry["height"]),
            out_path, palette, company_view,
            image_path=image_paths[0] if image_paths else None,
        )
        prompt_text = "PIL: 落地页企业核心优势"

    elif mode == "pil_process":
        renderer.render_process_steps(
            (entry["width"], entry["height"]),
            out_path, palette, company_view,
        )
        prompt_text = "PIL: 服务流程"

    elif mode == "pil_contact":
        renderer.render_contact(
            (entry["width"], entry["height"]),
            out_path, palette, company_view,
            image_path=image_paths[0] if image_paths else None,
            left_blank=500 if entry["region"] == "PC端" else None,
        )
        prompt_text = "PIL: 联系我们"

    elif mode == "pil_product_grid":
        extra = entry["extra"]
        renderer.render_product_grid(
            (entry["width"], entry["height"]),
            out_path, palette, company_view,
            cards=extra.get("cards", []),
            title=extra.get("title", entry["name"]),
            subtitle=extra.get("subtitle"),
            columns=extra.get("columns", 2),
            card_height=extra.get("card_height", 300),
            show_footer=extra.get("show_footer", True),
        )
        prompt_text = json.dumps(
            {"mode": "multi_grid", "cards": entry["extra"].get("cards")},
            ensure_ascii=False, indent=2,
        )

    elif mode == "seedream":
        design_prompt, labels = analyze_design(
            entry, company_data, image_paths, out_dir, design_notes
        )
        final_prompt = build_final_prompt(
            entry, company_data, design_prompt, labels
        )
        prompt_path = out_dir / "final_prompt.txt"
        common.save_text(final_prompt, prompt_path)

        call_seedream(
            final_prompt,
            image_paths[0] if image_paths else None,
            (entry["width"], entry["height"]),
            out_path,
        )
        prompt_text = final_prompt

    else:
        raise ValueError(f"未知模式：{mode}")

    meta = {
        "region": entry["region"],
        "module_id": entry["module_id"],
        "name": entry["name"],
        "mode": mode,
        "width": entry["width"],
        "height": entry["height"],
        "filename": entry["filename"],
        "images": entry["images"],
        "product": entry.get("product"),
        "prompt": prompt_text,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    common.save_json(meta, meta_path)

    final_size = common.get_image_size(out_path)

    # 硬约束：落地页宽度必须一致，便于上下拼接长图
    if entry["region"] == "落地页" and final_size[0] != entry["width"]:
        print(
            f"  宽度不一致（{final_size[0]}），修正为 {entry['width']}px"
        )
        fixed = common.fit_contain(
            out_path, (entry["width"], final_size[1])
        )
        fixed.save(out_path, format="PNG")
        final_size = common.get_image_size(out_path)

    return {
        "success": True,
        "skipped": False,
        "output": common.rel_path(out_path),
        "size": list(final_size),
    }


def run(dry_run=False, force=False, only=None, design_notes=""):
    print("=" * 60)
    print("Stage 4：生成营销设计图")
    print("=" * 60)

    plan, env = build_plan()

    if only:
        plan = [
            item for item in plan
            if item["module_id"] in only
            or item["name"] in only
            or f"{item['region']}/{item['module_id']}" in only
        ]

    report_path = common.OUTPUT_ROOT / "04_plan.json"
    common.save_json(plan, report_path)

    plan_hash = hashlib.sha1(
        json.dumps(plan, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    previous = common.load_manifest("designs")
    if (
        previous
        and previous.get("plan_hash")
        and previous.get("plan_hash") != plan_hash
        and not force
    ):
        print("提示：选图或主图已变化，建议使用 --force 重新生成设计图。")

    print(f"\n共 {len(plan)} 个设计项，计划已保存：{report_path}")
    for item in plan:
        print(
            f"  {item['region']} / {item['module_id']} / {item['name']} "
            f"[{item['mode']}] {item['width']}×{item['height']}"
            f" / 图片 {len(item['images'])} 张"
        )

    if dry_run:
        print("\n[dry-run] 未调用任何生成接口。")
        return {"plan": plan, "results": []}

    results = []

    for item in plan:
        try:
            result = generate_entry(
                item, env, force=force, design_notes=design_notes
            )
            result.update({
                "region": item["region"],
                "module_id": item["module_id"],
                "name": item["name"],
                "mode": item["mode"],
            })
        except Exception as exc:
            print(f"失败：{item['region']} / {item['name']} -> {exc!r}")
            result = {
                "success": False,
                "region": item["region"],
                "module_id": item["module_id"],
                "name": item["name"],
                "mode": item["mode"],
                "error": repr(exc),
            }
        results.append(result)

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "plan_hash": plan_hash,
        "total": len(results),
        "success": sum(1 for r in results if r.get("success")),
        "designs": results,
    }
    common.save_manifest("designs", manifest)

    # 落地页长图：确认所有模块宽度一致
    lp_widths = {
        result["size"][0]
        for result in results
        if result.get("success")
        and result.get("size")
        and result.get("region") == "落地页"
    }
    if len(lp_widths) > 1:
        print(f"警告：落地页宽度不一致：{sorted(lp_widths)}")
    elif lp_widths:
        print(f"落地页宽度已统一：{lp_widths.pop()}px")

    print(
        f"\nStage 4 完成：{manifest['success']}/{manifest['total']} 个设计项成功"
    )
    print(f"输出目录：{common.DESIGN_DIR}")

    return {"plan": plan, "results": results}
