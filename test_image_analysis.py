
import json
import base64
import mimetypes
import sys
from pathlib import Path
from openai import OpenAI

# 获取项目根目录 demo（本文件位于 demo/ 下）
PROJECT_ROOT = Path(__file__).resolve().parents[0]

# 添加到 Python 模块搜索路径
sys.path.insert(0, str(PROJECT_ROOT))
# =========================
# 1. API 配置
# =========================
from config import BASE_URL, API_KEY, IMAGE_MODEL,VISION_MODEL

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    timeout=180
)


# =========================
# 2. 图片转 Base64
# =========================

def encode_image(image_path):

    mime_type, _ = mimetypes.guess_type(str(image_path))

    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError(f"不支持的图片格式: {image_path}")

    with open(image_path, "rb") as f:
        image_base64 = base64.b64encode(
            f.read()
        ).decode("utf-8")

    return f"data:{mime_type};base64,{image_base64}"


# =========================
# 3. 图片分析
# =========================

def analyze_image(image_path):

    image_path = Path(image_path)

    image_url = encode_image(image_path)

    # 使用文件名作为图片 ID
    image_id = image_path.stem


    prompt = """
你是一名专业的商品图片质量检测专家。

你的任务是分析输入的商品图片，
判断需要调用哪些图片处理 Skill。

目前系统有三个 Skill：

1. watermark_removal
   功能：识别并擦除图片中的水印。

2. image_enhancement
   功能：商品图片基础优化，包含：
   - clarity：提升清晰度
   - straighten：图片转正
   - lighting：调整明暗
   - contrast：调整对比度
   - white_balance：调整白平衡
   - color_restore：还原产品颜色
   - stain_removal：清理污点、杂物
   - lighting_effect：优化光影质感

3. background_replacement
   功能：商品图片背景美化和背景更换。
   根据商品类型和营销展示需求，
   判断原背景是否需要美化或更换。

检测规则：

一、水印检测
- 如果图片存在明显水印，加入 watermark_removal。
- 如果没有水印，不加入 watermark_removal。
- 不判断水印属于哪个公司。
- 不需要描述水印内容。

二、基础优化检测
- 如果图片存在模糊、倾斜、曝光异常、
  对比度异常、色偏、污点、杂物等问题，
  加入 image_enhancement。
- 只要存在一项需要优化的问题，
  就加入 image_enhancement。
- 如果图片质量正常，不加入该 Skill。

三、背景检测
从商品营销和商业摄影的角度，
判断当前背景是否适合商品展示。

以下情况需要加入 background_replacement：

- 背景存在大量泥土、碎石、建筑垃圾、
  施工废料等影响美观的元素。
- 商品处于杂乱的施工现场、仓库、
  凌乱厂房等环境。
- 背景脏乱、破败、简陋，
  明显不适合商品营销展示。
- 背景颜色、环境与商品不协调。
- 背景存在大量无关物品，
  明显影响商品视觉效果。
- 原始背景虽然真实，
  但整体缺乏商业展示效果。

背景判断原则：

- 商品主体清晰，不代表背景适合营销。
- 不要只判断背景是否影响商品识别，
  还要判断整体画面是否美观。
- 真实施工现场不代表适合商品宣传。
- 对于明显不美观的背景，
  应加入 background_replacement。
- 如果背景干净、美观、协调，
  且有利于突出商品，则不加入该 Skill。
- 不要因为背景是室外、工厂或施工环境，
  就一律判断需要更换，要综合判断实际视觉效果。

四、任务分配
- 三个 Skill 可以同时触发。
- 只需要一个时，只输出对应 Skill。
- 不需要任何处理时，required_skills 输出空数组。
- 不要因为触发一个 Skill 而忽略其他 Skill。

五、商品主体保护
- 商品主体是图片的核心。
- 背景需要更换时，应保留商品主体。
- 不要因为商品主体清晰就忽略背景问题。

六、输出要求
- 只输出 JSON。
- 不要输出 Markdown。
- 不要输出图片描述。
- 不要输出问题解释。
- 不要输出其他额外字段。
- image_id 使用我提供的图片 ID。

JSON 格式：

{
    "image_id": "图片ID",
    "required_skills": [
        "watermark_removal",
        "image_enhancement",
        "background_replacement"
    ]
}

Skill 名称只能使用：
watermark_removal
image_enhancement
background_replacement
"""


    print(f"正在分析图片：{image_id}")

    response = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }
        ],
        response_format={
            "type": "json_object"
        },
        extra_body={
            "enable_thinking": False
        }
    )

    content = response.choices[0].message.content

    result = json.loads(content)

    # =========================
    # 4. 结果校验
    # =========================

    allowed_skills = {
        "watermark_removal",
        "image_enhancement",
        "background_replacement"
    }

    if not isinstance(result.get("required_skills"), list):
        raise ValueError("required_skills 必须是数组")

    # 只接受预定义的 Skill
    skills = result["required_skills"]

    if not all(
        isinstance(skill, str) and skill in allowed_skills
        for skill in skills
    ):
        raise ValueError(f"模型返回了非法 Skill: {skills}")

    # image_id 由 Python 控制，不依赖模型生成
    result = {
        "image_id": image_id,
        "required_skills": list(dict.fromkeys(skills))
    }

    return result


def process_image(
    image_path,
    final_dir,
    process_dir,
    required_skills=None,
    final_filename=None,
    analysis_result=None
):
    """
    处理单张图片。

    required_skills 由调用方传入时，跳过重复的视觉分析；
    否则本函数自行分析一次。
    """

    image_path = Path(image_path)

    # 1. AI 分析（只执行一次）
    if required_skills is None:
        if analysis_result is None:
            analysis_result = analyze_image(image_path)
        required_skills = analysis_result["required_skills"]

    print("\n需要执行的 Skill：", required_skills)

    # 2. 创建当前图片的过程目录
    image_process_dir = process_dir / image_path.stem
    image_process_dir.mkdir(parents=True, exist_ok=True)

    # 3. 注册 Skill
    from skills.watermark_removal.watermark_removal import run as watermark_run
    from skills.image_enhancement.image_enhancement import run as enhancement_run
    from skills.background_replacement.background_replacement import run as background_run

    skill_registry = {
        "watermark_removal": watermark_run,
        "image_enhancement": enhancement_run,
        "background_replacement": background_run
    }

    # 4. Skill 执行顺序
    execution_order = [
        "watermark_removal",
        "image_enhancement",
        "background_replacement"
    ]

    current_image = image_path

    # 最终图片路径
    final_path = final_dir / (
        final_filename or f"{image_path.stem}_final.png"
    )

    # 实际需要执行的 Skill
    selected_skills = [
        skill for skill in execution_order
        if skill in required_skills
    ]

    # 5. 依次执行
    for index, skill_name in enumerate(selected_skills, 1):

        print(f"\n开始执行：{skill_name}")

        output_path = image_process_dir / f"{index:02d}_{skill_name}.png"

        result = skill_registry[skill_name](
            image_path=str(current_image),
            output_path=str(output_path),
            required_skills=required_skills
        )

        if not result["success"]:
            raise RuntimeError(f"{skill_name} 执行失败")

        current_image = Path(result["image_path"])

    # 6. 输出最终结果到独立目录
    final_path.parent.mkdir(parents=True, exist_ok=True)

    from PIL import Image

    if selected_skills:
        with Image.open(current_image) as img:
            img.convert("RGB").save(final_path, format="PNG")
    else:
        # 不需要处理时，把原图统一转存为 PNG
        with Image.open(image_path) as img:
            img.convert("RGB").save(final_path, format="PNG")

    return {
        "success": True,
        "original_image": str(image_path),
        "final_image": str(final_path),
        "process_directory": str(image_process_dir),
        "executed_skills": selected_skills,
        "required_skills": list(required_skills)
    }

def process_folder(
    folder_path,
    final_dir=None,
    process_dir=None,
    recursive=True,
    limit=None
):

    folder_path = Path(folder_path)

    if not folder_path.exists() or not folder_path.is_dir():
        raise FileNotFoundError(f"文件夹不存在：{folder_path}")

    image_extensions = {
        ".jpg", ".jpeg", ".png", ".webp", ".bmp"
    }

    iterator = folder_path.rglob("*") if recursive else folder_path.iterdir()

    image_files = sorted([
        p for p in iterator
        if p.is_file() and p.suffix.lower() in image_extensions
    ])

    if limit:
        image_files = image_files[:limit]

    if not image_files:
        print("没有找到图片")
        return []

    # 两个独立输出目录（可外部指定）
    final_dir = Path(final_dir or (folder_path / "final_output"))
    process_dir = Path(process_dir or (folder_path / "process_output"))

    final_dir.mkdir(parents=True, exist_ok=True)
    process_dir.mkdir(parents=True, exist_ok=True)

    results = []

    print(f"共发现 {len(image_files)} 张图片")

    for index, image_path in enumerate(image_files, 1):

        print(f"\n========== {index}/{len(image_files)} ==========")

        try:
            result = process_image(
                image_path,
                final_dir,
                process_dir
            )

            results.append(result)

            print("处理成功：", result["final_image"])

        except Exception as e:

            print("处理失败：", image_path.name, repr(e))

            results.append({
                "success": False,
                "original_image": str(image_path),
                "error": repr(e)
            })

    success_count = sum(r["success"] for r in results)

    print("\n========== 批量处理完成 ==========")
    print(f"总数：{len(image_files)}")
    print(f"成功：{success_count}")
    print(f"失败：{len(results) - success_count}")

    return results

# =========================
# 5. 测试入口
# =========================

if __name__ == "__main__":

    folder_path = "input"

    try:

        results = process_folder(folder_path)

        output_path = Path("image_processing_results.json")

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(
                results,
                f,
                ensure_ascii=False,
                indent=4
            )

        print(f"\n处理记录已保存：{output_path}")

    except Exception as e:

        print("\n调用失败：", repr(e))