"""流水线公共工具：路径、分类推导、JSON、图片、模型客户端。"""

import base64
import hashlib
import json
import mimetypes
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps
from openai import OpenAI


# ============================================================
# 路径
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

INPUT_DIR = PROJECT_ROOT / "marketing_demo" / "input"
DESIGN_JSON = INPUT_DIR / "design.json"
COMPANY_JSON = INPUT_DIR / "company.json"

OUTPUT_ROOT = PROJECT_ROOT / "output"
PROCESSED_DIR = OUTPUT_ROOT / "01_processed"
MAIN_IMAGE_DIR = OUTPUT_ROOT / "02_main_images"
WORK_ROOT = PROCESSED_DIR / "_work"
MANIFEST_DIR = OUTPUT_ROOT / "_manifests"
DESIGN_DIR = OUTPUT_ROOT / "04_designs"
ARCHIVE_DIR = OUTPUT_ROOT / "archive"

# Stage3 规范输出（用户指定路径）
SELECTED_MATERIALS_DIR = (
    PROJECT_ROOT / "marketing_demo" / "output" / "selected_materials"
)
MATERIAL_TEMP_DIR = (
    PROJECT_ROOT / "marketing_demo" / "output" / "material_selection_temp"
)

SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp"
}

API_TIMEOUT = 300

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import API_KEY, BASE_URL, IMAGE_MODEL, VISION_MODEL  # noqa: E402

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    timeout=API_TIMEOUT,
    max_retries=2,
)


# ============================================================
# JSON / 文件
# ============================================================

def rel_path(path):
    """返回项目相对路径字符串，便于 manifest 移植。"""
    return str(Path(path).resolve().relative_to(PROJECT_ROOT))


def abs_path(path):
    path = Path(path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_json(path, default=None):
    path = abs_path(path)
    if not path.exists():
        return default if default is not None else {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data, path):
    path = abs_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def save_text(text, path):
    path = abs_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def safe_name(name):
    name = re.sub(r'[\\/:*?"<>|]', "_", str(name))
    return name.strip() or "未命名"


def safe_stem(path):
    return safe_name(Path(path).stem)


def normalize_category(name):
    name = re.sub(r"^\s*\d+[\s._、-]*", "", str(name))
    return name.strip() or "未分类"


def file_sha1(path, chunk_size=1 << 20):
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def image_id_for(rel):
    return hashlib.sha1(str(rel).encode("utf-8")).hexdigest()[:8]


def input_hash(rel_paths):
    """输入集合指纹：相对路径 + 文件大小。"""
    digest = hashlib.sha1()
    for rel in sorted(map(str, rel_paths)):
        path = PROJECT_ROOT / rel
        size = path.stat().st_size if path.exists() else -1
        digest.update(f"{rel}:{size}\n".encode("utf-8"))
    return digest.hexdigest()


def load_manifest(name):
    return load_json(MANIFEST_DIR / f"{name}.json", default={})


def save_manifest(name, data):
    return save_json(data, MANIFEST_DIR / f"{name}.json")


def archive_existing(path, label):
    """把旧目录移入 output/archive/<label>_<时间戳>，避免新旧混杂。"""
    path = abs_path(path)
    if not path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = ARCHIVE_DIR / f"{label}_{timestamp}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(target))
    print(f"已归档旧目录：{path} -> {target}")
    return target


# ============================================================
# 分类推导（stage1 / stage3 共用）
# ============================================================

def derive_source_type_category(folder_parts, filename):
    """
    根据相对 INPUT_DIR 的文件夹层级推导图片分类。

    规则与原 select_materials 保持一致：
    - 产品中心/<分类>/...        -> 产品 / 分类
    - 工程案例/...               -> 工程案例 / 末级目录
    - 其他目录                   -> 企业素材 / 末级目录
    """
    folder_parts = list(folder_parts)
    source_type = "其他"
    category = "未分类"

    product_root_index = None
    for index, folder in enumerate(folder_parts):
        if "产品" in folder:
            product_root_index = index
            break

    if product_root_index is not None:
        source_type = "产品"
        if product_root_index + 1 < len(folder_parts):
            category = normalize_category(folder_parts[product_root_index + 1])
    elif any("工程案例" in part for part in folder_parts):
        source_type = "工程案例"
        if folder_parts:
            category = normalize_category(folder_parts[-1])
    elif folder_parts:
        source_type = "企业素材"
        category = normalize_category(folder_parts[-1])

    is_banner = "banner" in Path(filename).name.lower()

    return source_type, category, is_banner


def collect_input_images(input_dir=None, relative_to=None):
    """递归收集输入图片，跳过输出/归档/临时目录与非图片文件。"""
    input_dir = abs_path(input_dir or INPUT_DIR)
    relative_to = abs_path(relative_to or INPUT_DIR)

    skip_dirs = {
        OUTPUT_ROOT.resolve(),
        ARCHIVE_DIR.resolve(),
        PROCESSED_DIR.resolve(),
    }

    images = []
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if any(parent.resolve() in skip_dirs for parent in path.parents):
            continue
        try:
            with Image.open(path) as img:
                img.verify()
            with Image.open(path) as img:
                if img.width <= 0 or img.height <= 0:
                    continue
        except Exception as exc:
            print(f"跳过无效图片：{path.name}（{exc}）")
            continue
        images.append({
            "path": path,
            "rel": str(path.resolve().relative_to(relative_to.resolve())),
            "rel_to_input": str(path.parent.resolve().relative_to(
                relative_to.resolve()
            )) if path.parent.resolve() != relative_to.resolve() else "",
        })
    return images


# ============================================================
# 图片工具
# ============================================================

def encode_image(path):
    """图片转 Base64 Data URI。"""
    path = abs_path(path)
    mime_type, _ = mimetypes.guess_type(str(path))
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/png"
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime_type};base64,{data}"


def get_image_size(path):
    with Image.open(abs_path(path)) as img:
        return img.size


def save_as_png(src, dest):
    """把任意格式图片统一转存为 PNG（避免 JPEG 字节写进 .png）。"""
    dest = abs_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(abs_path(src)) as img:
        if dest.suffix.lower() == ".png":
            img.convert("RGB").save(dest, format="PNG")
        else:
            img.save(dest)
    return dest


def _as_image(src):
    if isinstance(src, Image.Image):
        return src.convert("RGB"), False
    return Image.open(abs_path(src)).convert("RGB"), True


def fit_contain(src, size, background="#FFFFFF"):
    """等比例缩放后居中放到指定尺寸画布，不裁切内容。"""
    width, height = size
    img, should_close = _as_image(src)
    try:
        fitted = ImageOps.contain(
            img, (width, height), method=Image.Resampling.LANCZOS
        )
        canvas = Image.new("RGB", (width, height), background)
        canvas.paste(
            fitted,
            ((width - fitted.width) // 2, (height - fitted.height) // 2),
        )
        return canvas
    finally:
        if should_close:
            img.close()


def fit_cover(src, size):
    """等比缩放到铺满目标尺寸并居中裁切。"""
    width, height = size
    img, should_close = _as_image(src)
    try:
        return ImageOps.fit(
            img, (width, height), method=Image.Resampling.LANCZOS
        )
    finally:
        if should_close:
            img.close()


def fill_canvas_fit(src, size, background="#FFFFFF", mode="contain"):
    """生成最终 PNG，尺寸严格等于 size。"""
    if mode == "cover":
        canvas = fit_cover(src, size)
    else:
        canvas = fit_contain(src, size, background)
    return canvas


def encode_image_thumb(path, max_side=1024):
    """小图编码，用于视觉分析节省 token。"""
    path = abs_path(path)
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        import io
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=88)
    data = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{data}"


# ============================================================
# 通用文本处理
# ============================================================

def extract_json(text):
    """从模型输出中提取 JSON。"""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return json.loads(match.group())
    raise ValueError(f"模型没有返回有效 JSON：{text[:200]}")


def company_short_name(company):
    return company.get("short_name") or company.get("name", "")


def load_design_color(default="#C50303"):
    """
    读取 marketing_demo/input/色值*.txt 中的品牌色。

    文件内容可以是 #c50303，也可以只有文件名带色值；
    都读不到时回退到 brand_style.primary_color / 默认值。
    """

    try:
        for path in sorted(INPUT_DIR.glob("色值*.txt")):
            content = path.read_text(encoding="utf-8", errors="ignore")
            match = re.search(r"#[0-9A-Fa-f]{6}", content) or re.search(
                r"#[0-9A-Fa-f]{6}", path.name
            )
            if match:
                return match.group(0).upper()
    except Exception as exc:
        print(f"读取色值文件失败：{exc!r}")

    return default


def find_product(company, category):
    """按图片分类匹配 company.json 中的产品，找不到返回 None。"""
    products = company.get("products", [])
    normalized = normalize_category(category)

    for product in products:
        name = product.get("name", "")
        if name == normalized or normalized in name or name in normalized:
            return product

    if "假山" in normalized:
        for product in products:
            if "假山" in product.get("name", ""):
                return product

    return None


def module_find(design, region, module_id):
    """按 (region, id) 复合键查找设计模块配置。"""
    if region == "PC端":
        items = design.get("pc_design", {}).get("materials", [])
    else:
        items = design.get("landing_page", {}).get("modules", [])

    for item in items:
        if item.get("id") == module_id:
            return item
    return None
