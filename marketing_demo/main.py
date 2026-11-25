import os
import json
import base64
import mimetypes
import requests
from PIL import Image, ImageOps
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv


# =====================================================
# 1. 基础配置
# =====================================================

load_dotenv()

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv(
    "BASE_URL",
    "https://wanxiang.hebeirongshi.com/v1"
)

QWEN_MODEL = "qwen3.8-max"
IMAGE_MODEL = "doubao-seedream-5-5-261028"

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent

# 输入文件
INPUT_DIR = BASE_DIR / "input"

COMPANY_FILE = INPUT_DIR / "company.json"
DESIGN_CONFIG_FILE = INPUT_DIR / "d.json"
DESIGN_TEXT_FILE = INPUT_DIR / "design.txt"
INPUT_IMAGE = INPUT_DIR / "product.jpg"

# 当前处理的产品 ID
PRODUCT_ID = "rockery"

# 输出目录
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

PROMPT_PATH = OUTPUT_DIR / "design_prompt.txt"
RESULT_PATH = OUTPUT_DIR / "marketing.png"


# =====================================================
# 2. 初始化客户端
# =====================================================

def get_client():

    if not API_KEY:
        raise ValueError(
            "请在 .env 文件中配置 API_KEY"
        )

    return OpenAI(
        api_key=API_KEY,
        base_url=BASE_URL
    )


# =====================================================
# 3. 通用文件读取
# =====================================================

def read_json(file_path):

    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(
            f"JSON 文件不存在：{file_path}"
        )

    with open(
        file_path,
        "r",
        encoding="utf-8"
    ) as f:
        return json.load(f)


def read_text(file_path):

    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(
            f"文本文件不存在：{file_path}"
        )

    with open(
        file_path,
        "r",
        encoding="utf-8"
    ) as f:
        return f.read().strip()


# =====================================================
# 4. 图片转 Base64
# =====================================================

def image_to_base64(image_path):

    image_path = Path(image_path)

    if not image_path.exists():
        raise FileNotFoundError(
            f"图片不存在：{image_path}"
        )

    mime_type, _ = mimetypes.guess_type(
        str(image_path)
    )

    mime_type = mime_type or "image/jpeg"

    with open(image_path, "rb") as f:
        image_data = base64.b64encode(
            f.read()
        ).decode("utf-8")

    return f"data:{mime_type};base64,{image_data}"


# =====================================================
# 5. 从 JSON 中查找指定配置
# =====================================================

def resize_to_target(image_path, image_spec):

    width = image_spec["width"]
    height = image_spec["height"]

    with Image.open(image_path) as img:

        img = img.convert("RGB")

        # 等比例缩放，完整保留原图内容
        img = ImageOps.contain(
            img,
            (width, height),
            method=Image.Resampling.LANCZOS
        )

        # 创建指定尺寸的画布
        canvas = Image.new(
            "RGB",
            (width, height),
            "#FFFFFF"
        )

        # 居中放置缩放后的图片
        x = (width - img.width) // 2
        y = (height - img.height) // 2

        canvas.paste(img, (x, y))

        canvas.save(
            image_path,
            format="PNG"
        )

    print(f"最终图片尺寸已校验：{width}×{height}")

def find_by_id(data, target_id):

    """
    递归查找 JSON 中 id 对应的数据。

    兼容：
    1. 根节点就是目标对象
    2. 根节点是数组
    3. 目标对象嵌套在其他对象中
    """

    if isinstance(data, dict):

        if data.get("id") == target_id:
            return data

        for value in data.values():

            result = find_by_id(
                value,
                target_id
            )

            if result is not None:
                return result

    elif isinstance(data, list):

        for item in data:

            result = find_by_id(
                item,
                target_id
            )

            if result is not None:
                return result

    return None


# =====================================================
# 6. 整理公司和产品信息
# =====================================================

def extract_product(company_data, product_id):

    """
    根据产品 ID 获取产品信息。
    """

    products = company_data.get("products", [])

    if isinstance(products, dict):

        product = products.get(product_id)

        if product:
            return product

    elif isinstance(products, list):

        for product in products:

            if product.get("id") == product_id:
                return product

    raise ValueError(
        f"company.json 中没有找到产品：{product_id}"
    )


def normalize_advantages(advantages, limit=4):

    """
    将不同格式的优势信息统一整理成字符串列表。
    """

    result = []

    if not advantages:
        return result

    if isinstance(advantages, str):
        return [advantages]

    if isinstance(advantages, dict):
        advantages = [advantages]

    if isinstance(advantages, list):

        for item in advantages:

            if isinstance(item, str):

                text = item.strip()

            elif isinstance(item, dict):

                text = (
                    item.get("short_copy")
                    or item.get("title")
                    or item.get("content")
                    or item.get("description")
                    or ""
                )

                text = str(text).strip()

            else:
                continue

            if text and text not in result:
                result.append(text)

            if len(result) >= limit:
                break

    return result


def prepare_marketing_data():

    """
    整理本次营销图片需要使用的所有业务资料。
    """

    print("\n正在读取企业和产品资料...")

    # 读取公司信息
    company_data = read_json(
        COMPANY_FILE
    )

    # 读取产品信息
    product = extract_product(
        company_data,
        PRODUCT_ID
    )

    # 读取品牌信息
    brand = company_data.get(
        "brand",
        company_data.get("brand_style", {})
    )

    # 产品优势
    product_advantages = normalize_advantages(
        product.get("advantages", []),
        limit=4
    )

    # 厂家优势
    factory_advantages = normalize_advantages(
        company_data.get("factory_advantages", []),
        limit=4
    )

    # 如果没有单独的厂家优势，则从核心优势中提取
    if not factory_advantages:

        factory_advantages = normalize_advantages(
            company_data.get("core_advantages", []),
            limit=4
        )

    marketing_data = {

        "company": {
            "name": company_data.get("company_name", ""),
            "short_name": company_data.get("short_name", ""),
            "introduction": company_data.get("company_introduction", ""),
            "phone": company_data.get("phone", "")
        },

        "brand": {
            "primary_color": brand.get(
                "primary_color",
                "#c50303"
            ),
            "secondary_color": brand.get(
                "secondary_color",
                "#FFFFFF"
            ),
            "recommended_colors": brand.get(
                "recommended_colors",
                [
                    "#c50303",
                    "#FFFFFF",
                    "#D9C6A3",
                    "#333333"
                ]
            ),
            "visual_keywords": brand.get(
                "visual_keywords",
                []
            )
        },

        "company_strength": company_data.get(
            "company_strength",
            {}
        ),

        "product": {
            "id": product.get("id", PRODUCT_ID),
            "name": product.get("name", ""),
            "description": product.get("description", ""),
            "advantages": product_advantages
        },

        "factory_advantages": factory_advantages
    }

    print("企业和产品资料整理完成！")
    print(
        f"当前产品：{marketing_data['product']['name']}"
    )

    return marketing_data


# =====================================================
# 7. 整理设计规范
# =====================================================

def prepare_design_config():

    print("\n正在读取图片设计规范...")

    design_data = read_json(
        DESIGN_CONFIG_FILE
    )

    # 查找 product_main 配置
    design_config = find_by_id(
        design_data,
        "product_main"
    )

    if not design_config:
        raise ValueError(
            "d.json 中没有找到 product_main 配置"
        )

    return design_config

def get_image_spec(design_config):
    size = design_config.get("size", {})

    width = int(size.get("width", 800))
    height = int(size.get("height", 600))

    return {
        "width": width,
        "height": height,
        "unit": size.get("unit", "px"),
        "aspect_ratio": design_config.get("aspect_ratio", "4:3"),
        "color_tone": design_config.get("color_tone", "红色")
    }

# =====================================================
# 8. Qwen 分析图片和设计要求
# =====================================================

def analyze_design(
    client,
    image_path,
    marketing_data,
    design_config,
    design_text,
    image_spec
):
    

    print("\n正在调用 Qwen 分析产品图片...")

    image_data = image_to_base64(
        image_path
    )

    system_prompt = """
你是一名资深商业视觉设计师、品牌设计师和营销海报设计师。

你需要根据用户提供的：
1. 产品原始图片
2. 企业信息
3. 产品信息
4. 图片设计规范
5. 人工补充的设计要求

制定一份可以直接交给 AI 图片生成模型执行的完整设计方案。

==============================
一、核心设计原则
==============================

1. 产品图片是独立的产品素材，不是海报背景。
2. 必须保留产品原本的真实外观、结构、颜色、材质和细节。
3. 不允许擅自修改产品造型，不允许凭空增加产品结构。
4. 产品应当作为画面主体，具有明确的视觉中心。
5. 背景需要重新设计，不能直接把产品原图铺满作为背景。
6. 背景应当简洁、明亮、有层次，类似高品质 PPT 商业模板。
7. 图片、文字、背景应当是三个独立的视觉组成部分。
8. 不要让产品、背景和文字混在一起。

==============================
二、品牌视觉规范
==============================

优先遵循企业提供的品牌颜色。

如果品牌主色为红色：

- 使用品牌红作为核心强调色。
- 可以使用红色标题、红色细线、红色信息标签。
- 大面积背景优先采用白色、浅米色、暖灰色。
- 可以少量使用石材色作为辅助色。
- 深灰色仅用于正文文字。
- 不要使用大面积暗红色、黑色或深棕色背景。
- 不要让画面整体显得昏暗、沉重。

整体要求：
明亮、大气、专业、简洁、有品质感。

==============================
三、版式设计要求
==============================

不要采用机械、死板的固定模板。

根据产品图片的实际构图，灵活安排：

- 产品主体区域
- 产品名称区域
- 产品优势展示区域
- 厂家优势展示区域
- 辅助装饰区域

可以采用：
- 左文右图
- 上文下图
- 主标题与产品主体错位排版
- 卡片式卖点展示
- 不对称留白布局

但必须保证：
1. 产品主体足够突出。
2. 文字层级清楚。
3. 版面有呼吸感。
4. 不要堆积大量文字。
5. 不要使用过多装饰元素。
6. 不要出现密密麻麻的小字。

==============================
四、文字要求
==============================

1. 产品名称应当突出。
2. 产品优势使用简洁短句。
3. 厂家优势可以作为辅助信息展示。
4. 不允许虚构企业信息、产品参数和工程案例。
5. 不允许擅自添加未经提供的联系方式。
6. 中文文字尽量保持准确，不要生成乱码。
7. 不要擅自增加英文装饰文字。
8. 不要重复展示相同卖点。

==============================
五、输出要求
==============================

请根据实际图片判断最合适的构图和版式。

不要只给出笼统的设计建议。

请输出一份具体、完整、可执行的图片生成提示词。

提示词中必须明确：
- 产品主体如何摆放
- 背景如何设计
- 文字如何布局
- 主色调如何使用
- 产品优势如何展示
- 厂家优势如何展示
- 整体视觉效果如何控制

只输出最终设计提示词，不要输出分析过程。
"""



    user_content = f"""
请根据以下资料和产品原图，设计一张商业营销产品主图。
【最高优先级：画布规格，必须严格遵守】

画布宽度：{image_spec['width']}px
画布高度：{image_spec['height']}px
画面比例：{image_spec['aspect_ratio']}
整体色调：{image_spec['color_tone']}

注意：
1. 这是固定规格，不允许自行修改。
2. 禁止生成竖版、横版以外的其他比例。
3. 禁止自行将画布修改为1080×1440、1024×1536等尺寸。
4. 不需要在设计方案中重新描述或推测画布尺寸。
5. 只负责设计内容、背景、产品布局和文字排版。

【企业及产品资料】

{json.dumps(marketing_data, ensure_ascii=False, indent=2)}

【图片设计规范】

{json.dumps(design_config, ensure_ascii=False, indent=2)}

【用户补充的设计要求】

{design_text}

请特别注意：

1. 企业品牌主色是红色，必须在设计中体现。
2. 背景要简洁明亮，不要直接使用产品图片作为背景。
3. 产品原图是独立的主体素材，需要突出展示。
4. 文字和产品图片需要分开排版。
5. 整体效果参考高品质 PPT 商业模板，不要做成传统、死板的宣传单。
6. 不要添加资料中没有提供的信息。
7. 根据产品本身的特点选择最合适的布局。
"""

    response = client.chat.completions.create(

        model=QWEN_MODEL,

        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user_content
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_data
                        }
                    }
                ]
            }
        ],

        temperature=0.7
    )

    design_prompt = (
        response.choices[0].message.content
    )

    if not design_prompt:
        raise ValueError(
            "Qwen 没有返回设计提示词"
        )

    # 保存中间提示词
    with open(
        PROMPT_PATH,
        "w",
        encoding="utf-8"
    ) as f:
        f.write(design_prompt)

    print("\n设计提示词生成成功！")
    print("=" * 50)
    print(design_prompt)
    print("=" * 50)

    return design_prompt


# =====================================================
# 9. Seedream 生成营销图片
# =====================================================

def generate_marketing_image(
    image_path,
    design_prompt,
    image_spec
):

    print("\n正在调用 Seedream 生成营销图片...")

    image_data = image_to_base64(
        image_path
    )

    final_prompt = f"""
请基于提供的真实产品图片，设计并生成一张高质量商业营销主图。
【最高优先级：固定画布规格】

最终图片必须严格采用以下规格：

宽度：{image_spec['width']}px
高度：{image_spec['height']}px
比例：{image_spec['aspect_ratio']}
整体色调：{image_spec['color_tone']}

严禁修改画布方向、比例和尺寸。
如果后续设计方案中出现与以上规格冲突的描述，
一律以本规格为准。

画面尺寸比例：4:3。

================================================
一、产品图片的使用方式
================================================

输入图片是独立的真实产品素材，不是背景图片。

必须严格保留原图中的产品：

- 产品真实造型
- 产品结构
- 产品颜色
- 产品材质
- 产品纹理
- 产品关键细节

不能重新设计产品，不能凭空增加或删除产品结构。

允许对产品进行合理的构图调整：
可以调整产品在画面中的位置、大小和展示角度，
但不能改变产品本身的真实特征。

产品必须清晰、完整，成为画面的视觉中心。

================================================
二、背景设计
================================================

请重新设计海报背景。

不要直接将产品原图作为背景。

背景采用简洁、明亮、有品质感的商业设计风格。

建议使用：
- 白色
- 浅米色
- 淡暖灰色
- 少量石材色

背景可以有轻微的渐变、几何色块、柔和光影，
但不能出现复杂纹理、杂乱装饰和过多视觉元素。

背景需要衬托产品，而不是抢夺产品的视觉注意力。

禁止：
- 大面积黑色背景
- 大面积深红色背景
- 昏暗的场景
- 复杂的山水背景
- 过度装饰
- 将产品原图直接铺满背景

================================================
三、品牌颜色
================================================

必须体现企业红色品牌视觉。

品牌主色：#c50303

建议搭配：
- 品牌红：#c50303
- 白色：#FFFFFF
- 浅米色：#D9C6A3
- 深灰色：#333333

红色主要用于：
- 产品标题
- 重点卖点
- 小面积装饰色块
- 分隔线
- 重点信息强调

不要整张图片都使用红色。

整体应当明亮、大气、简洁、专业。

================================================
四、文字排版
================================================

文字和产品图片必须作为独立内容进行排版。

不要把文字直接覆盖在复杂的产品纹理上。

产品名称作为主标题，字体需要清晰、有力量感，
但不要使用过于夸张、花哨或难以识别的字体。

产品优势采用简短的卖点展示方式。

厂家优势作为辅助信息，可以使用简洁的信息卡片、
标签或分区排版。

排版要求：

1. 标题醒目，层级明确。
2. 正文清晰，不能太小。
3. 文字与产品保持合理距离。
4. 留白充足。
5. 不要堆积大量文字。
6. 不要使用复杂的艺术字。
7. 不要出现乱码和无意义的装饰文字。
8. 不要重复展示卖点。

================================================
五、整体设计方向
================================================

不要做成传统宣传单或机械的固定模板。

参考高品质 PPT 商业展示页的设计效果：

- 简洁的版式
- 明确的视觉层级
- 合理的留白
- 明亮的背景
- 红色品牌强调
- 产品主体突出
- 文字清晰易读

可以根据产品图片灵活采用左文右图、上文下图、
不对称布局、卡片式卖点展示等设计方式。

不要每次都采用完全相同的固定布局。

================================================
六、具体设计方案
================================================

以下是根据产品图片和业务资料制定的设计方案：

{design_prompt}

================================================
七、最终质量要求
================================================

最终图片必须满足：

1. 产品真实，不改变原始产品外观。
2. 产品主体清晰突出。
3. 背景简洁明亮。
4. 红色品牌视觉明确。
5. 文字清晰，层次分明。
6. 画面美观，不死板。
7. 不添加未经提供的品牌、参数、电话或其他信息。
8. 不要生成无意义的装饰文字。
9. 不要出现水印。
10. 整体具有专业商业营销设计质感。
"""

    url = (
        f"{BASE_URL.rstrip('/')}/images/generations"
    )

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": IMAGE_MODEL,
        "prompt": final_prompt,
        "image": image_data,
        "size": "2K",
        "response_format": "b64_json",
        "output_format": "png",
        "watermark": False
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=300
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"图片生成失败：{response.status_code}\n"
            f"{response.text}"
        )

    result = response.json()

    if not result.get("data"):

        raise ValueError(
            f"模型没有返回图片：{result}"
        )

    image_result = result["data"][0]

    # 优先处理 Base64 返回
    if image_result.get("b64_json"):

        image_bytes = base64.b64decode(
            image_result["b64_json"]
        )

    # 兼容 URL 返回
    elif image_result.get("url"):

        image_response = requests.get(
            image_result["url"],
            timeout=120
        )

        image_response.raise_for_status()

        image_bytes = image_response.content

    else:

        raise ValueError(
            f"无法识别图片返回格式：{image_result.keys()}"
        )

    # 保存最终图片
    with open(
        RESULT_PATH,
        "wb"
    ) as f:
        f.write(image_bytes)

    print("\n营销图片生成成功！")
    print(f"图片位置：{RESULT_PATH}")

    return RESULT_PATH


# =====================================================
# 10. 主流程
# =====================================================

def main():

    print("\n========== 营销图片生成任务开始 ==========")

    # 初始化客户端
    client = get_client()

    # 检查输入图片
    if not INPUT_IMAGE.exists():

        raise FileNotFoundError(
            f"产品图片不存在：{INPUT_IMAGE}"
        )

    # 第一步：整理企业和产品资料
    marketing_data = prepare_marketing_data()

    # 第二步：读取设计规范
    design_config = prepare_design_config()

    # 第三步：读取人工补充设计要求
    design_text = read_text(
        DESIGN_TEXT_FILE
    )
    # 获取固定画布规格
    image_spec = get_image_spec(design_config)

    print(
        f"本次图片规格："
        f"{image_spec['width']}×{image_spec['height']}，"
        f"{image_spec['aspect_ratio']}"
    )


    # 第四步：Qwen 分析图片并生成设计提示词
    design_prompt = analyze_design(
        client=client,
        image_path=INPUT_IMAGE,
        marketing_data=marketing_data,
        design_config=design_config,
        design_text=design_text,
        image_spec=image_spec
    )

    # 第五步：Seedream 根据提示词生成营销图片
    result = generate_marketing_image(
        image_path=INPUT_IMAGE,
        design_prompt=design_prompt,
        image_spec=image_spec
    )

    print("\n========== 任务完成 ==========")
    print(f"产品 ID：{PRODUCT_ID}")
    print(f"设计提示词：{PROMPT_PATH}")
    print(f"最终营销图片：{result}")


if __name__ == "__main__":
    main()