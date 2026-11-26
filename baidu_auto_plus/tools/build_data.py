"""把上游已生成的数据适配成上传器需要的索引文件。

这个脚本**不生成任何素材，也不编造任何文案**。图片、PSD、产品文案、企业资料
上游 pipeline 都已经产出了，这里只做格式转换 + 建立映射关系，因为
baidu_auto_plus 的三个上传模块需要的是槽位化的索引文件，而上游产出是按
设计模块组织的，两者结构不同。

数据来源（全部是既有产物）：
  marketing_demo/input/company.json            产品文案（权威）、企业资料、品牌信息
  marketing_demo/input/联系我们.txt (GBK)       联系人、完整地址
  output/02_main_images/<产品>/                 主图 / 方图 / 详情图
  output/04_designs/PC端/product_main_*/        产品文案兜底（company.json 未收录的产品）
  output/04_plan.json (region=落地页)           H5 模块导入顺序

产出（图片一律用绝对路径引用，不复制文件）：
  data/products/<产品>/product.json
  data/company.json
  data/h5_site.json

用法：
    ../cv/bin/python tools/build_data.py [--demo-root /path/to/demo]

company.json 里明确写了 "未提供的信息不允许模型自行编造"，本脚本严格遵守：
拼不出来的字段留空并在 _待确认 里说明，绝不填占位文案。
"""

import argparse
import json
import re
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent                       # baidu_auto_plus/
DEFAULT_DEMO_ROOT = ROOT.parent               # demo/

COMPANY_JSON = "marketing_demo/input/company.json"
CONTACT_FILE = "marketing_demo/input/联系我们.txt"
MAIN_IMAGE_DIR = "output/02_main_images"
DESIGN_DIR = "output/04_designs"
PLAN_FILE = "output/04_plan.json"

# 图片角色，已用 Codegen 录制核对过：
#   main_image.jpg     -> 主图槽位 (.dls-icon.vi-upload-add-cross-cross-icon)，4:3
#   main_image_1_1.jpg -> 方图槽位 (#sdk-content-oneToOne)，1:1
#   product_collage.jpg-> 详情图槽位 (.fr-element 富文本)
IMAGE_ROLES = {
    "main": ["main_image.jpg"],
    "square": ["main_image_1_1.jpg"],
    "detail": ["product_collage.jpg", "main_image.jpg"],
}

MAX_TAGS = 6            # 平台限制：最多 6 个标签
TAG_MIN, TAG_MAX = 4, 20  # 平台限制：每个标签 4-20 字符
LONG_TITLE_MIN, LONG_TITLE_MAX = 4, 100
SHORT_TITLE_MIN, SHORT_TITLE_MAX = 4, 24


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ------------------------------------------------------------ 产品文案（真实数据）

def load_product_copy(demo_root):
    """返回 {产品名: {category, tags, description, source}}，全部来自既有文件。"""
    copy = {}

    company_path = demo_root / COMPANY_JSON
    if company_path.exists():
        for product in read_json(company_path).get("products", []):
            advantages = product.get("advantages") or []
            titles = [a["title"] for a in advantages if isinstance(a, dict) and a.get("title")]
            descs = [a["description"] for a in advantages
                     if isinstance(a, dict) and a.get("description")]
            copy[product["name"]] = {
                "category": product.get("category", ""),
                "tags": titles,
                "description": " ".join(descs),
                "main_product": bool(product.get("main_product")),
                "source": "marketing_demo/input/company.json",
            }
    else:
        print(f"[warn] 权威产品文案缺失：{company_path}")

    # company.json 未收录的产品（如 古建戏楼），退到 pipeline 自己记录的 meta。
    for meta_dir in sorted((demo_root / DESIGN_DIR / "PC端").glob("product_main_*")):
        metas = list(meta_dir.glob("*.meta.json"))
        if not metas:
            continue
        product = (read_json(metas[0]).get("product") or {})
        name = product.get("name")
        if not name or name in copy:
            continue
        raw = product.get("advantages") or []
        titles = [a if isinstance(a, str) else a.get("title", "") for a in raw]
        copy[name] = {
            "category": product.get("category", ""),
            "tags": [t for t in titles if t],
            "description": "",
            "main_product": False,
            "source": f"{metas[0].relative_to(demo_root)}（known={product.get('known')}）",
        }
    return copy


def match_image_dir(name, available):
    """产品名与图片目录名不完全一致（塑石假山 vs 假山），做双向子串匹配。"""
    if name in available:
        return name
    hits = [d for d in available if d in name or name in d]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        print(f"[warn] 产品「{name}」匹配到多个图片目录 {hits}，请人工确认")
    return None


def make_titles(name, category, tags, notes):
    """只用真实字段拼装标题，拼不出来就留空并记录，绝不编造。"""
    if category:
        # category 本身可能已以「工程」结尾（古建工程），避免拼成「工程工程」
        suffix = "" if category.endswith("工程") else "工程"
        long_title = f"{name}定制设计施工厂家承接{category}{suffix}"
    elif tags:
        long_title = f"{name}定制设计施工厂家，{'、'.join(tags[:2])}"
    else:
        long_title = ""
        notes.append("long_title 留空：既无 category 也无 advantages，请人工补写")

    short_title = f"{name}定制施工"

    if long_title and not LONG_TITLE_MIN <= len(long_title) <= LONG_TITLE_MAX:
        notes.append(f"long_title 长度 {len(long_title)} 超出 {LONG_TITLE_MIN}-{LONG_TITLE_MAX}")
    if not SHORT_TITLE_MIN <= len(short_title) <= SHORT_TITLE_MAX:
        notes.append(f"short_title 长度 {len(short_title)} 超出 {SHORT_TITLE_MIN}-{SHORT_TITLE_MAX}")
    return long_title, short_title


def build_products(demo_root, default_category_clicks):
    source_dir = demo_root / MAIN_IMAGE_DIR
    if not source_dir.exists():
        print(f"[skip] 产品图片目录不存在：{source_dir}")
        return []

    available = {p.name for p in source_dir.iterdir() if p.is_dir() and not p.name.startswith("_")}
    copy = load_product_copy(demo_root)
    out_dir = ROOT / "data" / "products"
    built, skipped = [], []

    # 以「有图」为准遍历：用户要求「02_main_images 里有几个产品就上传几次」。
    for image_dir_name in sorted(available):
        product_dir = source_dir / image_dir_name
        matched_name = next(
            (n for n in copy if match_image_dir(n, {image_dir_name}) == image_dir_name),
            None,
        )
        display_name = matched_name or image_dir_name
        info = copy.get(matched_name, {})
        notes = []

        images = {}
        for role, candidates in IMAGE_ROLES.items():
            for candidate in candidates:
                if (product_dir / candidate).exists():
                    images[role] = str((product_dir / candidate).resolve())
                    break
        if "main" not in images or "square" not in images:
            print(f"[skip] {image_dir_name}：缺主图或方图，实际文件 "
                  f"{[p.name for p in product_dir.iterdir() if p.suffix]}")
            skipped.append(image_dir_name)
            continue

        tags = [t for t in (info.get("tags") or []) if TAG_MIN <= len(t) <= TAG_MAX][:MAX_TAGS]
        if not tags:
            notes.append("tags 为空：上游没有符合 4-20 字符的 advantages，请人工补写")
        if not matched_name:
            notes.append(f"company.json 与 pipeline meta 都没有「{image_dir_name}」的文案，"
                         f"标题按目录名拼装，请人工核对")

        long_title, short_title = make_titles(display_name, info.get("category", ""), tags, notes)

        data = {
            "product_name": display_name,
            "long_title": long_title,
            "short_title": short_title,
            "tags": tags,
            "description": info.get("description", ""),
            "category_clicks": default_category_clicks,
            "images": images,
            "_generated_by": "tools/build_data.py",
            "_文案来源": info.get("source", "无（按图片目录名拼装）"),
            "_图片目录": str(product_dir.resolve()),
            "_平台分类": info.get("category", ""),
        }
        if notes:
            data["_待确认"] = notes + [
                "category_clicks 用的是全局默认值，平台分类树与产品实际分类需逐个核对",
            ]
        else:
            data["_待确认"] = ["category_clicks 用的是全局默认值，平台分类树需逐个核对"]

        write_json(out_dir / image_dir_name / "product.json", data)
        built.append(image_dir_name)
        flag = "⚠" if notes else "ok"
        print(f"[{flag}]   {image_dir_name:<8} 名称={display_name:<6} 标签={len(tags)}个 "
              f"分类={info.get('category') or '(空)'}")
        if long_title:
            print(f"        长标题：{long_title}")
        for note in notes:
            print(f"        待确认：{note}")

    if copy:
        no_image = [n for n in copy if not match_image_dir(n, available)]
        if no_image:
            print(f"\n[info] 这些产品有文案但 02_main_images 里没有图，本次不上传：{no_image}")
    return built


# ------------------------------------------------------- 企业资料（合并两个真实来源）

def parse_contact_file(path):
    """联系我们.txt 是 GBK 系编码，逐行解析「键：值」，首行为公司全称。"""
    if not path.exists():
        print(f"[skip] 联系信息文档不存在：{path}")
        return {}
    raw = path.read_bytes()
    text = None
    for encoding in ("utf-8", "gb18030", "gbk", "utf-16"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise RuntimeError(f"无法解码：{path}")

    alias = {"联系人": "contact", "联系电话": "phone", "电话": "phone",
             "地址": "address", "联系地址": "address", "公司名称": "company_name"}
    fields = {}
    for line in (l.strip() for l in text.splitlines()):
        if not line:
            continue
        match = re.match(r"^([^：:]{1,12})[：:]\s*(.+)$", line)
        if match and match.group(1).strip() in alias:
            fields[alias[match.group(1).strip()]] = match.group(2).strip()
        elif "company_name" not in fields:
            fields["company_name"] = line
    return fields


def derive_region(address):
    match = re.match(
        r"^(.*?(?:省|自治区|北京市|天津市|上海市|重庆市))(.*?(?:市|自治州|地区|盟))",
        address or "",
    )
    return {"province": match.group(1), "city": match.group(2)} if match else {}


def build_company(demo_root):
    path = ROOT / "data" / "company.json"
    existing = read_json(path) if path.exists() else {}

    src_path = demo_root / COMPANY_JSON
    company = read_json(src_path)["company"] if src_path.exists() else {}
    contact = parse_contact_file(demo_root / CONTACT_FILE)

    # 简介用 company_profile（153 字真实文案），不要用之前那句手写占位。
    intro = company.get("company_profile") or existing.get("intro", "")
    address = contact.get("address") or company.get("location", "")
    phone = contact.get("phone") or company.get("phone", "")
    contact_name = contact.get("contact", "")

    notes = []
    if len(intro) > 1000:
        notes.append(f"商家简介 {len(intro)} 字，超出平台上限 1000，需截断")
    if contact_name and len(contact_name) > 10:
        notes.append(f"联系人「{contact_name}」超出平台上限 10 字符")
    if not address:
        notes.append("address 为空：联系我们.txt 与 company.json 都没有完整地址")
    if not phone:
        notes.append("phone 为空")
    if not contact_name:
        notes.append("contact 为空：联系人字段将跳过不填")

    region = derive_region(address)
    data = {
        **existing,
        "company_name": company.get("name") or contact.get("company_name")
                        or existing.get("company_name", ""),
        "short_name": company.get("short_name", ""),
        "intro": intro,
        "address": address,
        "phone": phone,
        "contact": contact_name,
        # 录制里地区控件当前值就是「河北省保定市」，与地址一致，默认不改动。
        # 平台上显示的不是这个值时，填 {"current_text": "<当前显示>", "city": "<目标市>", "nth": 4}
        "region": existing.get("region", {}),
        "images": existing.get("images", {}),
        "_文案来源": {
            "company_name/intro/phone": str(src_path.resolve()) if src_path.exists() else "(缺)",
            "contact/address": str((demo_root / CONTACT_FILE).resolve()),
        },
        "_region_从地址推导": region,
    }
    if notes:
        data["_待确认"] = notes

    write_json(path, data)
    print(f"[ok]   商户资料 -> data/company.json")
    print(f"       公司：{data['company_name']}（简称 {data['short_name']}）")
    print(f"       联系人：{contact_name or '(空)'}  电话：{phone or '(空)'}")
    print(f"       地址：{address or '(空)'}")
    print(f"       简介：{len(intro)} 字（真实 company_profile）")
    print(f"       推导地区：{region or '(未识别)'} —— 默认不执行地区变更")
    for note in notes:
        print(f"       待确认：{note}")
    return data


# ------------------------------------------------------------- H5 落地页 PSD 清单

def build_h5_manifest(demo_root):
    plan_path = demo_root / PLAN_FILE
    if not plan_path.exists():
        print(f"[skip] 设计方案不存在：{plan_path}")
        return None
    landing = [m for m in read_json(plan_path) if m.get("region") == "落地页"]
    if not landing:
        print("[skip] 04_plan.json 里没有 region=落地页 的模块")
        return None

    modules = []
    for entry in landing:                       # plan 顺序即落地页从上到下的顺序
        module_id = entry["module_id"]
        psd = (demo_root / DESIGN_DIR / "落地页" / module_id
               / (Path(entry["filename"]).stem + ".psd"))
        if not psd.exists():
            print(f"[warn] 落地页模块 {module_id} 缺 PSD：{psd}")
            continue
        modules.append({
            "module_id": module_id,
            "name": entry.get("name", module_id),
            "psd": str(psd.resolve()),
            "png_preview": str(psd.with_suffix(".png").resolve()),
            "width": entry.get("width"),
            "height": entry.get("height"),
            # 录制里的图层上移 / 插入空白模块步骤依赖具体 PSD 组合，默认不做。
            # 需要时填 {"insert_below": true, "move_up": 1}
            "layer_ops": {},
        })

    company_path = ROOT / "data" / "company.json"
    company = read_json(company_path) if company_path.exists() else {}
    site_name = (company.get("company_name") or "落地页")[:30]

    data = {
        "site_name": site_name,
        "modules": modules,
        "_generated_by": "tools/build_data.py",
        "_source_plan": str(plan_path.resolve()),
        "_说明": "modules 顺序取自 04_plan.json 中 region=落地页 的条目顺序。",
    }
    write_json(ROOT / "data" / "h5_site.json", data)
    print(f"[ok]   H5 站点清单 -> data/h5_site.json  站点名「{site_name}」，{len(modules)} 个 PSD：")
    for i, m in enumerate(modules, 1):
        size = f"{m['width']}x{m['height']}" if m["width"] else "?"
        print(f"       {i}. {m['module_id']:<18} {m['name']:<10} {size:<9} {Path(m['psd']).name}")
    return data


def main():
    parser = argparse.ArgumentParser(description="把上游产物适配成上传器索引文件")
    parser.add_argument("--demo-root", default=str(DEFAULT_DEMO_ROOT))
    args = parser.parse_args()
    demo_root = Path(args.demo_root).resolve()

    print(f"demo 根目录：{demo_root}")
    print("说明：本脚本只做格式适配，不生成素材、不编造文案。\n")

    config = read_json(ROOT / "config.json")
    default_clicks = config.get("default_category_clicks", [
        {"text": "其他", "index": 0},
        {"text": "其他", "index": 2},
        {"text": "其他", "index": 3},
    ])

    print("=== 产品 ===")
    products = build_products(demo_root, default_clicks)
    print("\n=== 商户 ===")
    build_company(demo_root)
    print("\n=== H5 落地页 ===")
    build_h5_manifest(demo_root)
    print(f"\n完成：{len(products)} 个产品可上传。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
