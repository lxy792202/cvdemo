
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
    """将图片转换为 Base64 Data URI"""

    path = Path(image_path)

    mime_type, _ = mimetypes.guess_type(str(path))

    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError(f"不支持的图片格式：{path}")

    with open(path, "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime_type};base64,{image_base64}"


def replace_background(image_path, output_path):
    """调用 Seedream 更换商品图片背景"""

    url = BASE_URL.rstrip("/") + "/images/generations"

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    prompt = """

       
你是一名专业的电商商品摄影后期设计师。请对输入图片进行完整的商业背景替换。

【核心任务】

将商品主体以外的整个原始环境替换为干净、美观、自然的商业摄影场景。

注意：这是整体背景重建任务，不是局部物体删除任务，也不是简单的背景清理任务。

【一、必须完整替换的区域】

请检查原图中的所有背景区域，包括：

1. 商品后方的天空、建筑、墙面及远景。
2. 商品下方的地面，包括泥土地面、沙土地面、碎石地面、水泥地面等。
3. 商品周围的施工废料、杂物、红旗、警示牌、围挡等。
4. 所有其他不适合商业展示的环境元素。

无论不合适的元素位于图片的哪个位置，都必须进行处理。

特别注意：

- 如果原图是泥土地面，必须将整个可见泥土地面替换为新的干净地面。
- 不能只删除红旗、垃圾等显眼物体，却保留原来的泥土地面。
- 不能只修改商品周围的一小块区域。
- 不能保留任何明显不适合商业展示的原始背景区域。
- 必须保证新背景在画面中完整、连续、自然。

【二、新背景设计】

根据商品类型和实际使用场景，生成适合电商营销的商业摄影环境。

要求：

- 环境干净整洁。
- 地面平整、干净，与商品使用场景协调。
- 背景简洁，突出商品主体。
- 光照自然，符合真实摄影效果。
- 商品与地面之间具有自然的接触关系和阴影。
- 不要生成过度复杂或喧宾夺主的环境。

【三、商品主体保护】

商品是图片中必须保留的核心内容。

严格要求：

1. 保留原始商品的完整结构。
2. 不改变商品的外观、形状、颜色和尺寸比例。
3. 不增加、删除或修改商品零部件。
4. 不改变商品表面的文字、Logo、品牌标识。
5. 不得将商品重新设计成其他产品。
6. 商品与新背景融合时，只允许调整合理的光影和接触阴影。

【四、最终效果】

最终图片应当呈现为一张经过专业商业摄影处理的商品图片。

原始不合适的背景必须整体消失，包括原来的地面。

不要采用只删除局部杂物的处理方式。

优先保证商品主体不变，同时完成整个背景的自然重建。


    """

    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "image": encode_image(image_path),
        "response_format": "b64_json",
        "output_format": "png",
        "watermark": False
    }

    print("正在调用 Seedream 更换背景...")

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

    print(f"背景更换完成：{output_path}")

    return str(output_path)


def _save_png(src, dest):
    """把图片统一转存为 PNG，避免 JPEG 字节写进 .png。"""
    from PIL import Image
    with Image.open(src) as img:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(dest, format="PNG")


def run(image_path, output_path, required_skills=None, analysis_result=None):
    """背景更换 Skill 主入口"""

    image_path = Path(image_path)
    output_path = Path(output_path)

    if not image_path.exists():
        raise FileNotFoundError(f"图片不存在：{image_path}")

    # 第一步：Qwen 图片分析（调用方已提供结果时跳过）
    if required_skills is None:
        if analysis_result is None:
            print(f"正在分析图片：{image_path.name}")
            analysis_result = analyze_image(str(image_path))

        print("图片分析结果：", analysis_result)

        required_skills = analysis_result.get(
            "required_skills", []
        )

    # 第二步：判断是否需要背景更换
    if "background_replacement" not in required_skills:

        _save_png(image_path, output_path)

        print("图片无需更换背景，已转存原图")

        return {
            "success": True,
            "image_path": str(output_path),
            "replaced": False,
            "message": "图片无需更换背景"
        }

    # 第三步：调用 Seedream 更换背景
    replace_background(
        image_path,
        output_path
    )

    return {
        "success": True,
        "image_path": str(output_path),
        "replaced": True,
        "message": "商品背景更换成功"
    }


if __name__ == "__main__":

    result = run(
        "test_2.jpg",
        "output/test_background.png"
    )

    print("\n最终执行结果：")
    print(result)
