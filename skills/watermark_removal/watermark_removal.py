
import base64
import mimetypes
import requests
import shutil
import json

from pathlib import Path
import sys
from pathlib import Path

# 获取项目根目录 demo
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 添加到 Python 模块搜索路径
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


def remove_watermark(image_path, output_path):
    """调用 Seedream 去除图片水印"""

    url = BASE_URL.rstrip("/") + "/images/generations"

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": IMAGE_MODEL,
        "prompt": """
        请擦除商品图片中的水印，并自然修复水印覆盖区域。

        要求：
        1. 保留商品原有形状、颜色、纹理和细节。
        2. 不改变商品主体及原始构图。
        3. 不添加新的文字、标识或水印。
        4. 尽可能保持图片原始视觉效果。
        """,
        "image": encode_image(image_path),
        "response_format": "b64_json",
        "output_format": "png",
        "watermark": False
    }

    print("正在调用 Seedream 去水印...")

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

    print(f"去水印完成：{output_path}")

    return str(output_path)


def _save_png(src, dest):
    """把图片统一转存为 PNG，避免 JPEG 字节写进 .png。"""
    from PIL import Image
    with Image.open(src) as img:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(dest, format="PNG")


def run(image_path, output_path, required_skills=None, analysis_result=None):
    """水印去除 Skill 主入口"""

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
        print(json.dumps(
            analysis_result,
            ensure_ascii=False,
            indent=4
        ))

        required_skills = analysis_result.get(
            "required_skills", []
        )

    # 第二步：判断是否需要去水印
    if "watermark_removal" not in required_skills:

        _save_png(image_path, output_path)

        print("图片无需去水印，已转存原图")

        return {
            "success": True,
            "image_path": str(output_path),
            "has_watermark": False,
            "message": "图片无需去水印"
        }

    # 第三步：直接调用 Seedream 去水印
    remove_watermark(
        image_path,
        output_path
    )

    return {
        "success": True,
        "image_path": str(output_path),
        "has_watermark": True,
        "message": "图片去水印成功"
    }


if __name__ == "__main__":

    result = run(
        "test_3.jpg",
        "output/test_no_watermark.png"
    )

    print("\n最终执行结果：")
    print(json.dumps(
        result,
        ensure_ascii=False,
        indent=4
    ))
