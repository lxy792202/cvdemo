
import os
import json
import base64
import shutil
from pathlib import Path
from io import BytesIO

from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont


# ==================================================
# 1. 项目路径
# ==================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

CONFIG_DIR = PROJECT_ROOT / "config"
MATERIAL_DIR = PROJECT_ROOT / "materials" / "products"

WORK_DIR = PROJECT_ROOT / "work" / "product_main"
SELECTED_DIR = WORK_DIR / "selected"
PROMPT_DIR = WORK_DIR / "prompts"
GENERATED_DIR = WORK_DIR / "generated"

OUTPUT_DIR = (
    PROJECT_ROOT
    / "output"
    / "河北硕景园林古建筑工程有限公司"
    / "pc_design"
)

for folder in [
    SELECTED_DIR,
    PROMPT_DIR,
    GENERATED_DIR,
    OUTPUT_DIR
]:
    folder.mkdir(parents=True, exist_ok=True)


# ==================================================
# 2. API 配置
# ==================================================

BASE_URL = "https://wanxiang.hebeirongshi.com/v1"
MODEL = "doubao-seedream-5-5-261028"

# API_KEY = os.getenv("WANXIANG_API_KEY")
API_KEY = "sk-jpln9z3MU9r9GpyH3IjhKlcImKqhcnnsEDYGvi0nEqQpSu8M"

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL
)


# ==================================================
# 3. 读取企业资料
# ==================================================

def load_company_materials():
    config_path = CONFIG_DIR / "company.json"

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ==================================================
# 4. 选择假山产品图片
# ==================================================

def select_product_image():
    """
    优先从名称包含“假山”或“塑石”的分类中选择图片。
    暂时选择分辨率最高的一张。
    """

    if not MATERIAL_DIR.exists():
        raise FileNotFoundError(
            f"产品素材目录不存在：{MATERIAL_DIR}"
        )

    image_extensions = {
        ".jpg", ".jpeg", ".png", ".webp"
    }

    candidates = []

    for path in MATERIAL_DIR.rglob("*"):
        if not path.is_file():
            continue

        if path.suffix.lower() not in image_extensions:
            continue

        category_name = path.parent.name
        file_name = path.stem

        # 只选择假山相关图片
        if not any(
            word in category_name or word in file_name
            for word in ["假山", "塑石"]
        ):
            continue

        try:
            with Image.open(path) as img:
                width, height = img.size
                candidates.append(
                    (path, width * height)
                )
        except Exception:
            continue

    if not candidates:
        raise FileNotFoundError(
            "没有找到假山图片，请检查 materials/products/ 下的分类名称。"
        )

    # 优先选择分辨率最高的图片
    candidates.sort(
        key=lambda item: item[1],
        reverse=True
    )

    selected_image = candidates[0][0]

    # 保存一份选图结果，方便排查
    target_path = SELECTED_DIR / selected_image.name

    shutil.copy2(selected_image, target_path)

    print(f"选中的产品图片：{selected_image}")

    return target_path


# ==================================================
# 5. 生成背景 Prompt
# ==================================================

def build_background_prompt(company):
    """
    只生成背景，不生成产品、不生成文字。
    产品图片和中文文案由程序后续合成。
    """

    company_name = company.get(
        "company", {}
    ).get(
        "short_name",
        "硕景园林"
    )

    prompt = f"""
为一家名为{company_name}的古建园林企业设计一张高品质营销海报背景。

画面主题：中式园林、塑石假山、自然景观。

视觉要求：
1. 整体风格大气、稳重、有传统中式园林气质。
2. 主色调采用暗红色、石材色、米白色、深灰色。
3. 画面体现自然山石、园林水景、东方美学。
4. 背景具有层次感和高级商业设计感。
5. 画面中央预留较大区域，用于后期放置真实假山产品照片。
6. 画面上方预留标题区域。
7. 画面下方预留企业信息区域。
8. 不要出现任何文字、数字、Logo、水印。
9. 不要生成假山产品主体，后续会使用真实产品照片。

画面比例为4:3，适合作为企业产品宣传主图背景。
"""

    return prompt.strip()


# ==================================================
# 6. 调用 Seedream 生成背景
# ==================================================

def generate_background(prompt):
    prompt_path = PROMPT_DIR / "background_prompt.txt"

    prompt_path.write_text(
        prompt,
        encoding="utf-8"
    )

    print("正在调用 Seedream 生成背景...")

    response = client.images.generate(
        model=MODEL,
        prompt=prompt,
        size="1024x1024"
    )

    image_data = response.data[0]

    background_path = GENERATED_DIR / "background.png"

    if getattr(image_data, "b64_json", None):
        image_bytes = base64.b64decode(
            image_data.b64_json
        )

    elif getattr(image_data, "url", None):
        import requests

        result = requests.get(
            image_data.url,
            timeout=120
        )
        result.raise_for_status()
        image_bytes = result.content

    else:
        raise ValueError(
            "模型没有返回图片数据，请检查 API 响应结构。"
        )

    with open(background_path, "wb") as f:
        f.write(image_bytes)

    print(f"背景图片已保存：{background_path}")

    return background_path


# ==================================================
# 7. 中文字体
# ==================================================

def get_chinese_font(size):
    font_paths = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
    ]

    for font_path in font_paths:
        if os.path.exists(font_path):
            return ImageFont.truetype(
                font_path,
                size
            )

    return ImageFont.load_default()


# ==================================================
# 8. 合成最终产品主图
# ==================================================


def compose_product_main(background_path, product_path, company):
    """
    明亮自然风格：
    真实产品照片作为主体，不再放进白色矩形卡片。
    """

    target_width = 800
    target_height = 600

    # 直接使用真实产品图片
    image = Image.open(product_path).convert("RGB")

    # 按 4:3 比例居中裁切
    src_w, src_h = image.size
    target_ratio = target_width / target_height
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        new_w = int(src_h * target_ratio)
        left = (src_w - new_w) // 2
        image = image.crop((left, 0, left + new_w, src_h))
    else:
        new_h = int(src_w / target_ratio)
        top = (src_h - new_h) // 2
        image = image.crop((0, top, src_w, top + new_h))

    image = image.resize(
        (target_width, target_height),
        Image.Resampling.LANCZOS
    ).convert("RGBA")

    # 整体略微提亮
    from PIL import ImageEnhance

    rgb = image.convert("RGB")
    rgb = ImageEnhance.Brightness(rgb).enhance(1.12)
    rgb = ImageEnhance.Color(rgb).enhance(1.08)
    image = rgb.convert("RGBA")

    # 左侧增加柔和的浅色渐变，方便放置文字
    gradient = Image.new(
        "RGBA",
        (target_width, target_height),
        (0, 0, 0, 0)
    )

    pixels = gradient.load()

    for x in range(420):
        alpha = int(175 * (1 - x / 420) ** 1.5)

        for y in range(target_height):
            pixels[x, y] = (255, 252, 240, alpha)

    image = Image.alpha_composite(image, gradient)

    draw = ImageDraw.Draw(image)

    company_info = company.get("company", {})

    company_name = company_info.get(
        "short_name",
        "硕景园林"
    )

    phone = company_info.get(
        "phone",
        "15130297856"
    )

    title_font = get_chinese_font(38)
    subtitle_font = get_chinese_font(18)
    info_font = get_chinese_font(16)

    # 品牌红色
    brand_red = "#c50303"
    dark_color = "#37352f"

    # 标题
    draw.text(
        (38, 55),
        "塑石假山定制",
        font=title_font,
        fill=brand_red
    )

    draw.text(
        (40, 112),
        "让自然景观融入每一处空间",
        font=subtitle_font,
        fill=dark_color
    )

    # 装饰线
    draw.rounded_rectangle(
        (40, 153, 105, 157),
        radius=2,
        fill=brand_red
    )

    # 简短卖点
    advantages = [
        "源头厂家 · 按需定制",
        "专业团队 · 全国施工",
        "设计生产 · 一站式落地"
    ]

    for index, text in enumerate(advantages):
        y = 190 + index * 38

        draw.ellipse(
            (42, y + 7, 52, y + 17),
            fill=brand_red
        )

        draw.text(
            (65, y),
            text,
            font=subtitle_font,
            fill=dark_color
        )

    # 底部轻量信息
    draw.rounded_rectangle(
        (35, 520, 765, 575),
        radius=8,
        fill=(255, 255, 255, 205)
    )

    draw.text(
        (52, 537),
        company_name,
        font=info_font,
        fill=dark_color
    )

    draw.text(
        (530, 537),
        f"咨询电话：{phone}",
        font=info_font,
        fill=brand_red
    )

    final_path = OUTPUT_DIR / "product_main.png"

    image.convert("RGB").save(
        final_path,
        quality=95
    )

    print(f"最终图片已保存：{final_path}")

    return final_path


# ==================================================
# 9. 主函数
# ==================================================

def generate_product_main():
    print("开始生成假山产品主图...")

    # 读取企业资料
    company = load_company_materials()

    # 选择真实产品图片
    product_path = select_product_image()

    # 生成背景 Prompt
    # prompt = build_background_prompt(company)

    # 调用模型生成背景
    final_path = compose_product_main(
        None,
        product_path,
        company
    )

    print("产品主图生成完成！")

    return {
        "success": True,
        "selected_image": str(product_path),
        # "background_image": str(background_path),
        "image_path": str(final_path)
    }


if __name__ == "__main__":
    result = generate_product_main()
    print(json.dumps(
        result,
        ensure_ascii=False,
        indent=4
    ))
