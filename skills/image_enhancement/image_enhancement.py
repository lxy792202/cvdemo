
import base64
import mimetypes
import requests
import sys
from pathlib import Path

# 获取项目根目录 demo
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from config import BASE_URL, API_KEY, IMAGE_MODEL
from test_image_analysis import analyze_image


def encode_image(image_path):
    """将本地图片转换为 Base64 Data URI"""

    path = Path(image_path)

    mime_type, _ = mimetypes.guess_type(str(path))

    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError(f"不支持的图片格式：{path}")

    with open(path, "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime_type};base64,{image_base64}"


def enhance_image(image_path, output_path):
    """调用 Seedream 执行商品图片基础优化"""

    url = BASE_URL.rstrip("/") + "/images/generations"

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    prompt = """
    请对这张商品图片进行专业的基础视觉优化。

    根据图片实际存在的问题进行有针对性的调整，不要机械地执行所有优化。

    优化要求：

    1. 清晰度：
       改善图片清晰度，增强商品细节和纹理表现。
       避免过度锐化和人为制造细节。

    2. 图片方向：
       如果图片存在明显倾斜，请适当调整，使画面保持端正。
       不要随意改变商品的拍摄角度和透视关系。

    3. 明暗和对比度：
       改善明显过暗、过亮或对比度不足的问题。
       保留合理的高光、阴影和细节。

    4. 白平衡：
       修正明显的色温偏差和色偏。
       尽可能还原商品本身的真实颜色。

    5. 杂物清理：
       清理背景中明显的污点、灰尘和无关小杂物。
       不要删除商品本身的结构、配件和必要元素。

    6. 光影质感：
       适当改善光线、明暗层次和整体视觉质感。
       保持自然，不要产生过度修饰的效果。

    最重要的限制：

    - 必须保留商品主体及其原有结构。
    - 不得改变商品的形状、尺寸比例和设计细节。
    - 不得随意修改商品颜色。
    - 不得添加或删除商品配件。
    - 不得生成原图中不存在的产品细节。
    - 保留原图中的正常文字、商标和产品标识。
    - 尽可能保持原始构图、背景和画面比例。
    - 不添加新的文字、Logo 或水印。

    最终输出一张优化后的商品图片。
    """

    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "image": encode_image(image_path),
        "response_format": "b64_json",
        "output_format": "png",
        "watermark": False
    }

    print("正在调用 Seedream 进行图片基础优化...")

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=300
    )

    if not response.ok:
        print("API 错误：", response.text)
        response.raise_for_status()

    result = response.json()
    image_data = result["data"][0]

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if image_data.get("b64_json"):
        image_bytes = base64.b64decode(image_data["b64_json"])
        output_path.write_bytes(image_bytes)

    elif image_data.get("url"):
        image_response = requests.get(
            image_data["url"],
            timeout=120
        )
        image_response.raise_for_status()
        output_path.write_bytes(image_response.content)

    else:
        raise ValueError("模型没有返回有效的图片数据")

    print(f"图片基础优化完成：{output_path}")

    return str(output_path)


def _save_png(src, dest):
    """把图片统一转存为 PNG，避免 JPEG 字节写进 .png。"""
    from PIL import Image
    with Image.open(src) as img:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(dest, format="PNG")


def run(image_path, output_path, required_skills=None, analysis_result=None):
    """图片基础优化 Skill 主入口"""

    image_path = Path(image_path)
    output_path = Path(output_path)

    if not image_path.exists():
        raise FileNotFoundError(f"图片不存在：{image_path}")

    # 第一步：Qwen 图片分析（调用方已提供结果时跳过）
    if required_skills is None:
        if analysis_result is None:
            print(f"正在分析图片：{image_path.name}")
            analysis_result = analyze_image(str(image_path))

        print("图片分析结果：")
        print(analysis_result)

        required_skills = analysis_result.get(
            "required_skills", []
        )

    # 第二步：判断是否需要基础优化
    if "image_enhancement" not in required_skills:

        _save_png(image_path, output_path)

        print("图片无需基础优化，已转存原图")

        return {
            "success": True,
            "image_path": str(output_path),
            "enhanced": False,
            "message": "图片无需基础优化"
        }

    # 第三步：调用 Seedream 优化图片
    enhance_image(
        image_path,
        output_path
    )

    return {
        "success": True,
        "image_path": str(output_path),
        "enhanced": True,
        "message": "图片基础优化成功"
    }


if __name__ == "__main__":

    result = run(
        "test_2.jpg",
        "output/test_enhanced.png"
    )

    print("\n最终执行结果：")
    print(result)
