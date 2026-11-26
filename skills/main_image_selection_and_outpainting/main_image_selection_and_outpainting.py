
import base64
import json
import math
import mimetypes
import re
import requests

from pathlib import Path
from PIL import Image, ImageDraw, ImageStat
from openai import OpenAI
import sys
from pathlib import Path

# 获取项目根目录 demo
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 添加到 Python 模块搜索路径
sys.path.insert(0, str(PROJECT_ROOT))
from config import BASE_URL, API_KEY, IMAGE_MODEL, VISION_MODEL
from pipeline.common import save_image_within_limit


# =========================================================
# 基础配置
# =========================================================

SUPPORTED_FORMATS = {
    ".jpg", ".jpeg", ".png", ".webp"
}

TARGET_WIDTH_RATIO = 4
TARGET_HEIGHT_RATIO = 3

# 每批最多比较6张图片
BATCH_SIZE = 6

# 图片模型请求超时时间
IMAGE_TIMEOUT = 300


# =========================================================
# Qwen 多模态客户端
# =========================================================

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    timeout=IMAGE_TIMEOUT
)


# =========================================================
# 图片基础工具
# =========================================================

def encode_image(image_path):
    """将图片转换为 Base64 Data URI"""

    path = Path(image_path)

    mime_type, _ = mimetypes.guess_type(str(path))

    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError(f"不支持的图片格式：{path}")

    with open(path, "rb") as f:
        image_base64 = base64.b64encode(
            f.read()
        ).decode("utf-8")

    return f"data:{mime_type};base64,{image_base64}"


def get_image_size(image_path):
    """获取图片尺寸"""

    with Image.open(image_path) as img:
        return img.size


def is_4_3(image_path, tolerance=0.02):
    """判断图片是否为4:3（允许 2% 误差）"""

    width, height = get_image_size(image_path)

    expected_width = height * 4 / 3

    return abs(width - expected_width) <= max(width, height) * tolerance


def collect_images(input_dir, output_dir):
    """
    递归收集图片。

    排除输出目录，防止重复读取最终结果。
    """

    input_dir = Path(input_dir).resolve()
    output_dir = Path(output_dir).resolve()

    if not input_dir.exists():
        raise FileNotFoundError(
            f"输入文件夹不存在：{input_dir}"
        )

    if not input_dir.is_dir():
        raise NotADirectoryError(
            f"输入路径不是文件夹：{input_dir}"
        )

    image_paths = []

    for path in input_dir.rglob("*"):

        if not path.is_file():
            continue

        if path.suffix.lower() not in SUPPORTED_FORMATS:
            continue

        # 排除输出目录中的图片
        try:
            path.resolve().relative_to(output_dir)
            continue
        except ValueError:
            pass

        # 检查图片是否损坏
        try:
            with Image.open(path) as img:
                img.verify()

            with Image.open(path) as img:
                if img.width <= 0 or img.height <= 0:
                    continue

        except Exception as e:
            print(f"跳过无效图片：{path.name}，原因：{e}")
            continue

        image_paths.append(path)

    image_paths.sort()

    return image_paths


# =========================================================
# Qwen 主图优选
# =========================================================

def extract_json(text):
    """从模型输出中提取 JSON"""

    text = text.strip()

    # 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 提取 Markdown JSON
    match = re.search(
        r"\{[\s\S]*\}",
        text
    )

    if match:
        return json.loads(match.group())

    raise ValueError(
        f"模型没有返回有效 JSON：{text}"
    )


def compare_images(image_paths):
    """
    使用 Qwen 多模态模型比较一批图片。

    返回：
    {
        "best_index": 1,
        "reason": "..."
    }

    best_index 从1开始。
    """

    content = []

    prompt = """
你是一名专业的电商商品摄影师和视觉设计师。

现在需要从多张经过图片处理的商品图片中，
选出最适合用作电商商品主图的一张。

每张图片前面都有对应的编号。

请按照以下标准综合比较：

1. 商品完整度：
   商品是否完整，有无缺失、遮挡、结构异常。

2. 商品清晰度：
   商品是否清晰，细节是否容易辨认。

3. 构图美观度：
   商品位置、画面布局、视觉比例是否协调。

4. 背景质量：
   背景是否干净、整洁、自然，是否适合商品营销。

5. 光影效果：
   商品亮度、对比度、色彩和光影是否自然。

6. 商业展示价值：
   是否具有专业商品摄影的视觉效果，
   是否能够突出商品本身。

选择原则：

- 优先选择商品完整、清晰的图片。
- 优先选择构图合理、整体美观的图片。
- 不要因为图片分辨率更高就直接选择。
- 不要因为背景经过更换就直接选择。
- 不要选择商品发生明显变形或细节损坏的图片。
- 不要将图片比例是否为4:3作为优选标准。

请在所有图片中选择综合效果最好的一张。

只返回 JSON，不要输出其他内容：

{
    "best_index": 1,
    "reason": "选择原因"
}
"""

    content.append({
        "type": "text",
        "text": prompt
    })

    for index, image_path in enumerate(image_paths, start=1):

        width, height = get_image_size(image_path)

        content.append({
            "type": "text",
            "text": (
                f"\n图片编号：{index}\n"
                f"文件名称：{image_path.name}\n"
                f"图片尺寸：{width}x{height}\n"
            )
        })

        content.append({
            "type": "image_url",
            "image_url": {
                "url": encode_image(image_path)
            }
        })

    print(
        f"正在使用 Qwen 比较 {len(image_paths)} 张图片..."
    )

    response = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[
            {
                "role": "user",
                "content": content
            }
        ]
    )

    result_text = response.choices[0].message.content

    if isinstance(result_text, list):
        result_text = "".join(
            item.get("text", "")
            for item in result_text
            if isinstance(item, dict)
        )

    result = extract_json(result_text)

    best_index = int(result["best_index"])

    if not 1 <= best_index <= len(image_paths):
        raise ValueError(
            f"Qwen 返回了无效图片编号：{best_index}"
        )

    return {
        "best_index": best_index,
        "reason": result.get("reason", "")
    }


def select_best_image(image_paths):
    """
    从所有图片中选择最佳主图。

    超过单批数量时，采用分组淘汰方式，
    最后再对各组胜出图片进行比较。
    """

    if not image_paths:
        raise ValueError("没有可供选择的图片")

    candidates = list(image_paths)

    round_number = 1

    while len(candidates) > 1:

        print(
            f"\n========== 第 {round_number} 轮主图优选 =========="
        )

        winners = []

        for start in range(0, len(candidates), BATCH_SIZE):

            batch = candidates[
                start:start + BATCH_SIZE
            ]

            print(
                f"\n正在比较第 {start // BATCH_SIZE + 1} 组，"
                f"共 {len(batch)} 张图片"
            )

            result = compare_images(batch)

            winner = batch[result["best_index"] - 1]

            print(f"本组胜出：{winner.name}")
            print(f"选择原因：{result['reason']}")

            winners.append(winner)

        candidates = winners

        round_number += 1

    best_image = candidates[0]

    print("\n最终选中的主图：", best_image)

    return best_image


# =========================================================
# 计算4:3扩图画布
# =========================================================

def calculate_canvas_size(width, height):
    """
    计算不缩放原图情况下的最小4:3画布。

    4:3画布尺寸使用4和3的整数倍，
    保证最终比例精确。
    """

    scale = max(
        math.ceil(width / 4),
        math.ceil(height / 3)
    )

    target_width = scale * 4
    target_height = scale * 3

    return target_width, target_height


def get_background_color(image):
    """
    获取原图边缘的平均颜色。

    用于生成扩图前的临时画布。
    """

    rgb = image.convert("RGB")

    width, height = rgb.size

    pixels = []

    # 取上下左右边缘
    border = max(1, min(width, height) // 100)

    regions = [
        (0, 0, width, border),
        (0, height - border, width, height),
        (0, 0, border, height),
        (width - border, 0, width, height)
    ]

    for region in regions:

        crop = rgb.crop(region)

        pixels.append(crop)

    colors = []

    for crop in pixels:

        stat = ImageStat.Stat(crop)

        colors.append(stat.mean)

    average = tuple(
        int(sum(c[i] for c in colors) / len(colors))
        for i in range(3)
    )

    return average


def _blurred_extend_background(original, target_size):
    """用原图拉伸+模糊生成扩展区底色，给模型连续的背景参考。"""

    from PIL import ImageFilter

    width, height = target_size

    background = original.resize(
        (width, height),
        Image.Resampling.LANCZOS
    ).filter(
        ImageFilter.GaussianBlur(
            radius=max(12, min(width, height) // 50)
        )
    )

    return background


def create_outpainting_canvas(image_path):
    """
    创建4:3画布。

    原始图片保持原尺寸，不裁切、不拉伸。
    扩展区域使用原图拉伸+模糊的底色，避免纯色画布导致
    模型生成与原图背景不连续的色块。
    """

    original = Image.open(image_path).convert("RGB")

    width, height = original.size

    target_width, target_height = calculate_canvas_size(
        width,
        height
    )

    canvas = _blurred_extend_background(
        original,
        (target_width, target_height)
    )

    # 居中放置原图
    offset_x = (target_width - width) // 2
    offset_y = (target_height - height) // 2

    canvas.paste(
        original,
        (offset_x, offset_y)
    )

    return (
        canvas,
        original,
        (offset_x, offset_y),
        (target_width, target_height)
    )


# =========================================================
# Seedream 扩图
# =========================================================

def _feather_mask(size, feather):
    """生成边缘羽化蒙版，让原图与生成背景过渡自然。"""

    width, height = size
    mask = Image.new("L", size, 255)
    draw = ImageDraw.Draw(mask)

    feather = max(1, min(feather, width // 2, height // 2))

    for index in range(feather):
        alpha = int(255 * (index + 1) / feather)
        draw.rectangle(
            (index, index, width - 1 - index, height - 1 - index),
            outline=alpha,
        )

    return mask


def expand_image(image_path, output_path):
    """
    调用 Seedream 扩展背景。

    保留原图全部像素，只生成画布外的扩展区域。
    """

    url = BASE_URL.rstrip("/") + "/images/generations"

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    (
        canvas,
        original,
        offset,
        target_size
    ) = create_outpainting_canvas(image_path)

    target_width, target_height = target_size

    print(
        f"原图尺寸：{original.width}x{original.height}"
    )

    print(
        f"目标画布：{target_width}x{target_height}"
    )

    # 保存临时画布（放在系统临时目录，避免污染最终输出目录）
    import tempfile

    temp_file = tempfile.NamedTemporaryFile(
        suffix=".png",
        prefix="outpainting_input_",
        delete=False
    )
    temp_path = Path(temp_file.name)
    temp_file.close()

    canvas.save(temp_path, format="PNG")

    prompt = """
你是一名专业的电商商品商业摄影后期设计师。

请对输入图片执行智能扩图任务。

【核心要求】

输入图片中间区域包含原始商品图片，
周围新增的纯色区域是需要生成的扩展区域。

请只对原图边界之外的空白扩展区域进行背景生成。

【商品保护】

1. 原始图片中的商品必须完整保留。
2. 不得改变商品的结构、形状、颜色和尺寸。
3. 不得增加、删除或修改商品零部件。
4. 不得修改商品表面的文字、Logo。
5. 不得裁切、拉伸或压缩原始图片。
6. 不得重新设计商品。

【扩图要求】

1. 自然填充原始图片四周的空白区域。
2. 新背景必须与原始背景自然衔接。
3. 保持原始场景的透视关系、光照和材质。
4. 不得出现明显拼接痕迹。
5. 不得生成无关物体或额外商品。
6. 不要覆盖或重绘原始图片区域。

【边界无缝要求（最高优先级）】

1. 原始图片与扩展区域的交界处必须完全连续，
   不得出现任何水平线、竖直线、分界线、色带、
   暗边、亮边、边框或明暗突变。
2. 天空、地面、墙体、建筑线条必须跨越原图边界
   自然延续，不能断开或错位。
3. 扩展区域的色温、亮度、饱和度必须与原始图片
   边缘保持一致，不能出现上下或左右两块色差。
4. 不要在原图边界处生成任何线条、阴影或装饰。
5. 这是同一张照片的自然延伸，不是两张图片拼接。

【最终效果】

生成一张完整的横向4:3商品图片。

这是背景扩展任务，不是商品重绘任务。
"""

    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "image": encode_image(temp_path),
        "response_format": "b64_json",
        "output_format": "png",
        "watermark": False
    }

    try:

        print("\n正在调用 Seedream 执行扩图...")

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=IMAGE_TIMEOUT
        )

        if not response.ok:

            print("API 错误：", response.text)

            response.raise_for_status()

        result = response.json()

        image_data = result["data"][0]

        if image_data.get("b64_json"):

            image_bytes = base64.b64decode(
                image_data["b64_json"]
            )

        elif image_data.get("url"):

            image_response = requests.get(
                image_data["url"],
                timeout=120
            )

            image_response.raise_for_status()

            image_bytes = image_response.content

        else:

            raise ValueError(
                "Seedream 没有返回有效图片"
            )

        # 读取生成结果
        import io

        generated = Image.open(
            io.BytesIO(image_bytes)
        ).convert("RGB")

        # 统一生成图尺寸，便于还原原图区域
        if generated.size != target_size:

            print(
                f"模型输出尺寸：{generated.size}，"
                f"调整为目标画布：{target_size}"
            )

            generated = generated.resize(
                target_size,
                Image.Resampling.LANCZOS
            )

        # 关键操作：
        # 把原始图片像素覆盖回生成结果，保护商品不被重绘。
        # 边界使用羽化蒙版过渡，避免出现水平接缝/色带。
        offset_x, offset_y = offset

        feather = max(
            24,
            min(original.width, original.height) // 40
        )

        generated.paste(
            original,
            (offset_x, offset_y),
            _feather_mask(original.size, feather)
        )

        output_path = Path(output_path)

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        # 输出高质量 JPEG，并自动压到 2MB 以下
        save_image_within_limit(generated, output_path)

        # 检查最终尺寸
        width, height = get_image_size(output_path)

        if width * 3 != height * 4:

            raise ValueError(
                "扩图结果不符合4:3比例"
            )

        print(
            f"\n扩图成功：{output_path}"
        )

        return str(output_path)

    finally:

        if temp_path.exists():
            temp_path.unlink()


# =========================================================
# 主图选择与扩图 Skill
# =========================================================

def run(input_dir=None, output_dir=None, image_paths=None):
    """
    Skill 主入口。

    输入（二选一）：
        input_dir：前三个 Skill 处理完成的图片文件夹
        image_paths：直接指定候选图片列表（跨目录按类别挑选时使用）
        output_dir：最终主图输出文件夹

    输出：
        最佳4:3商品主图 + 1:1裁切图
    """

    if image_paths is None:

        if input_dir is None:
            raise ValueError("必须提供 input_dir 或 image_paths")

        input_dir = Path(input_dir).resolve()

        output_dir = Path(output_dir).resolve()

        print("\n【第一步】收集处理后的图片")

        image_paths = collect_images(
            input_dir,
            output_dir
        )

    else:

        image_paths = [Path(p) for p in image_paths]

    if output_dir is None:
        raise ValueError("必须提供 output_dir")

    output_dir = Path(output_dir).resolve()

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    print("=" * 50)
    print("商品主图优选与4:3智能扩图 Skill")
    print("=" * 50)

    if not image_paths:

        raise ValueError(
            "文件夹中没有找到有效的商品图片"
        )

    print(
        f"共找到 {len(image_paths)} 张有效图片"
    )

    for path in image_paths:

        width, height = get_image_size(path)

        print(
            f"- {path.name} ({width}x{height})"
        )

    # -----------------------------------------------------
    # 第二步：AI优选
    # -----------------------------------------------------

    print("\n【第二步】AI选择最佳商品主图")

    best_image = select_best_image(
        image_paths
    )

    original_width, original_height = get_image_size(
        best_image
    )

    # -----------------------------------------------------
    # 第三步：比例判断
    # -----------------------------------------------------

    print("\n【第三步】判断图片比例")

    final_image_path = output_dir / "main_image.jpg"

    selection_result = {
        "selected_image": best_image.name,
        "original_size": [
            original_width,
            original_height
        ],
        "target_ratio": "4:3"
    }

    if is_4_3(best_image):

        print(
            "原图已经是4:3，无需扩图"
        )

        save_image_within_limit(
            best_image,
            final_image_path
        )

        selection_result.update({
            "operation": "skip",
            "reason": "原图已经符合4:3比例",
            "final_size": [
                original_width,
                original_height
            ],
            "success": True
        })

    else:

        print(
            "原图不是4:3，需要执行扩图"
        )

        # -------------------------------------------------
        # 第四步：Seedream扩图
        # -------------------------------------------------

        print("\n【第四步】调用Seedream扩图")

        expand_image(
            best_image,
            final_image_path
        )

        final_width, final_height = get_image_size(
            final_image_path
        )

        selection_result.update({
            "operation": "outpainting",
            "final_size": [
                final_width,
                final_height
            ],
            "success": True
        })

    # -----------------------------------------------------
    # 第五步：保存结果记录
    # -----------------------------------------------------

    result_json = output_dir / "selection_result.json"

    with open(
        result_json,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            selection_result,
            f,
            ensure_ascii=False,
            indent=4
        )

    print("\n" + "=" * 50)
    print("主图处理完成")
    print("=" * 50)

    print("选中图片：", best_image.name)
    print("最终图片：", final_image_path)
    print("结果记录：", result_json)

    print(
        "最终尺寸：",
        get_image_size(final_image_path)
    )

    # 生成 1:1 图片
    square_path = output_dir / "main_image_1_1.jpg"

    crop_to_square(
        final_image_path,
        square_path
    )

    # 保存选择记录
    # （如果你的 selection_result.json 已经在前面保存，这里不需要重复保存）

    return {
        "success": True,
        "selected_image": str(best_image),
        "image_path": str(final_image_path),
        "square_image_path": str(square_path),
        "result_json": str(result_json),
        "operation": selection_result["operation"]
    }

def crop_to_square(image_path, output_path):
    with Image.open(image_path) as img:
        img = img.convert("RGB")

        width, height = img.size
        side = min(width, height)

        left = (width - side) // 2
        top = (height - side) // 2

        right = left + side
        bottom = top + side

        square = img.crop((left, top, right, bottom))
        save_image_within_limit(square, output_path)

    return output_path

# =========================================================
# 本地测试
# =========================================================

if __name__ == "__main__":

    result = run(
        input_dir="test_images/processed",
        output_dir="output/main_image"
    )

    print("\n最终执行结果：")

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=4
        )
    )
