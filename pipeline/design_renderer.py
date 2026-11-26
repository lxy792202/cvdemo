"""PIL 版式渲染：Logo、企业优势、服务流程、联系咨询、多产品网格。

所有中文文字在这里精确绘制，避免图片模型写错字。
"""

import contextlib
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pipeline import common


_TEXT_SUPPRESSED = False


@contextlib.contextmanager
def suppress_text():
    """渲染“无文字背景层”时临时关闭所有文字绘制。"""

    global _TEXT_SUPPRESSED
    previous = _TEXT_SUPPRESSED
    _TEXT_SUPPRESSED = True
    try:
        yield
    finally:
        _TEXT_SUPPRESSED = previous


FONT_CANDIDATES = {
    "bold": [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ],
    "regular": [
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ],
}

_FONT_CACHE = {}


def get_font(size, bold=False):
    key = (int(size), bool(bold))
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    for path in FONT_CANDIDATES["bold" if bold else "regular"]:
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, int(size))
                _FONT_CACHE[key] = font
                return font
            except Exception:
                continue

    font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


# ============================================================
# 品牌色
# ============================================================

def _hex(color, default):
    try:
        value = str(color).strip()
        if value.startswith("#") and len(value) in (4, 7):
            if len(value) == 7:
                return value.upper()
            return "#" + "".join(ch * 2 for ch in value[1:]).upper()
    except Exception:
        pass
    return default


def palette_from_brand(brand=None):
    brand = brand or {}
    primary = _hex(brand.get("primary_color"), "#C50303")
    secondary = _hex(brand.get("secondary_color"), "#FFFFFF")
    return {
        "primary": primary,
        # 品牌深红，用于大面积渐变与页脚
        "primary_dark": "#9C0202",
        "primary_deep": "#6E0101",
        "secondary": secondary,
        "stone": "#D9C6A3",
        "gold": "#C9A96A",
        "gold_light": "#E8D6B0",
        "dark": "#2B2B2B",
        "text": "#2B2B2B",
        "muted": "#6B6B6B",
        "bg": "#FAF7F2",
        "soft": "#F7E4E4",
        "card": "#FFFFFF",
        "line": "#E7E0D6",
        "photo_overlay": "#1D1D1D",
        "on_primary": "#FFFFFF",
        "on_primary_muted": "#FFDDDD",
    }


# ============================================================
# 基础绘制
# ============================================================

def _text(draw, xy, text, size, fill, bold=False, anchor="la"):
    if _TEXT_SUPPRESSED:
        return
    draw.text(
        xy,
        str(text),
        font=get_font(size, bold),
        fill=fill,
        anchor=anchor,
    )


def _text_width(draw, text, font):
    box = draw.textbbox((0, 0), str(text), font=font)
    return box[2] - box[0]


def _wrap(draw, text, font, max_width, max_lines=None):
    """按字符折行，兼容中文。"""
    text = str(text or "")
    lines = []
    current = ""

    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        if _text_width(draw, current + char, font) <= max_width:
            current += char
        else:
            lines.append(current)
            current = char

        if max_lines and len(lines) >= max_lines:
            break

    if current:
        lines.append(current)

    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]

    return lines


def _draw_paragraph(draw, xy, text, size, fill, max_width, line_gap=8,
                    bold=False, max_lines=None):
    if _TEXT_SUPPRESSED:
        return xy[1]
    font = get_font(size, bold)
    lines = _wrap(draw, text, font, max_width, max_lines=max_lines)
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += size + line_gap
    return y


def _rounded_rect(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(
        box, radius=radius, fill=fill, outline=outline, width=width
    )


def _paste_cover(canvas, src, box, radius=0):
    """把图片按 cover 方式贴进 box，可加圆角。"""
    x1, y1, x2, y2 = box
    width, height = int(x2 - x1), int(y2 - y1)
    img = common.fit_cover(src, (width, height))

    if radius:
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, width, height), radius=radius, fill=255
        )
        canvas.paste(img, (int(x1), int(y1)), mask)
    else:
        canvas.paste(img, (int(x1), int(y1)))


def _vertical_gradient(size, top, bottom):
    width, height = size
    top_rgb = Image.new("RGB", (1, 1), top).getpixel((0, 0))
    bottom_rgb = Image.new("RGB", (1, 1), bottom).getpixel((0, 0))
    base = Image.new("RGB", size, top)
    draw = ImageDraw.Draw(base)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(
            int(top_rgb[i] + (bottom_rgb[i] - top_rgb[i]) * ratio)
            for i in range(3)
        )
        draw.line([(0, y), (width, y)], fill=color)
    return base


def _overlay(img, color="#000000", alpha=120):
    layer = Image.new("RGBA", img.size, color)
    layer.putalpha(alpha)
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def _rgba(color, alpha):
    rgb = Image.new("RGB", (1, 1), color).getpixel((0, 0))
    return (rgb[0], rgb[1], rgb[2], int(alpha))


def red_panel(canvas, box, color, edge=None, feather=90, radius=0):
    """
    在画布上绘制品牌红面板。

    edge="right"/"left"/"bottom"/"top" 时，面板朝照片一侧
    用渐隐蒙版过渡，避免生硬直切。
    """

    x1, y1, x2, y2 = (int(v) for v in box)
    width, height = x2 - x1, y2 - y1

    if width <= 0 or height <= 0:
        return

    layer = Image.new("RGBA", (width, height), _rgba(color, 255))

    if radius:
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, width - 1, height - 1), radius=radius, fill=255
        )
        layer.putalpha(mask)

    if feather and edge:
        fade = max(1, min(feather, width, height))
        alpha_mask = layer.getchannel("A").copy()
        fade_draw = ImageDraw.Draw(alpha_mask)

        for index in range(fade):
            # 最外侧完全透明，向内逐渐恢复不透明
            value = int(255 * index / fade)

            if edge == "right":
                fade_draw.line(
                    (width - 1 - index, 0, width - 1 - index, height - 1),
                    fill=value,
                )
            elif edge == "left":
                fade_draw.line(
                    (index, 0, index, height - 1), fill=value
                )
            elif edge == "bottom":
                fade_draw.line(
                    (0, height - 1 - index, width - 1, height - 1 - index),
                    fill=value,
                )
            elif edge == "top":
                fade_draw.line(
                    (0, index, width - 1, index), fill=value
                )

        layer.putalpha(alpha_mask)

    canvas.paste(layer, (x1, y1), layer)


def draw_ruyi_cloud(draw, cx, cy, scale, color, width=2):
    """用圆弧绘制简化的如意云纹，低对比度装饰用。"""

    r = max(6, scale)

    for factor, angle in (
        (1.0, (200, 340)),
        (0.66, (20, 160)),
        (0.34, (200, 340)),
    ):
        draw.arc(
            (cx - r * factor, cy - r * factor,
             cx + r * factor, cy + r * factor),
            angle[0], angle[1], fill=color, width=width,
        )

    for offset in (-1.35, 1.35):
        draw.arc(
            (cx + r * offset - r * 0.5, cy - r * 0.5,
             cx + r * offset + r * 0.5, cy + r * 0.5),
            200, 340, fill=color, width=width,
        )
        draw.arc(
            (cx + r * offset - r * 0.28, cy - r * 0.28,
             cx + r * offset + r * 0.28, cy + r * 0.28),
            20, 160, fill=color, width=width,
        )

    draw.arc(
        (cx - r * 2.1, cy - r * 0.05, cx - r * 0.9, cy + r * 1.15),
        280, 360, fill=color, width=width,
    )


def _vertical_text(draw, x, y, text, size, fill, gap=6, bold=False):
    """竖排文字，用于增加东方海报气质。"""

    if _TEXT_SUPPRESSED:
        return

    font = get_font(size, bold)
    for char in str(text):
        draw.text((x, y), char, font=font, fill=fill)
        y += size + gap


def _seal(draw, x, y, size, text, palette, on_red=True):
    """印章式小色块。"""

    fill = palette["card"] if on_red else palette["primary"]
    text_color = palette["primary"] if on_red else "#FFFFFF"

    _rounded_rect(
        draw, (x, y, x + size, y + size), 4,
        fill=fill, outline=palette["gold"], width=1,
    )

    chars = [c for c in str(text)][:2]
    font_size = int(size * 0.4)

    if len(chars) == 1:
        _text(
            draw, (x + size / 2, y + size / 2),
            chars[0], font_size, text_color, bold=True, anchor="mm",
        )
    else:
        _text(
            draw, (x + size / 2, y + size * 0.30),
            chars[0], font_size, text_color, bold=True, anchor="mm",
        )
        _text(
            draw, (x + size / 2, y + size * 0.70),
            chars[1], font_size, text_color, bold=True, anchor="mm",
        )


def gold_frame(draw, box, palette, inset=14):
    """在红色/浅色面板内绘制金色双线框。"""

    x1, y1, x2, y2 = box
    draw.rectangle(
        (x1 + inset, y1 + inset, x2 - inset, y2 - inset),
        outline=palette["gold"], width=1,
    )
    draw.rectangle(
        (x1 + inset + 4, y1 + inset + 4, x2 - inset - 4, y2 - inset - 4),
        outline=palette["gold_light"], width=1,
    )


def deco_header(canvas, box, palette, vertical_label="", seal_text="硕景",
                edge=None, feather=0, frame=True):
    """统一的高端红标题带：装饰 + 金线 + 竖排小字 + 印章。"""

    red_panel(
        canvas, box, palette["primary"], edge=edge, feather=feather
    )
    decorate_red_panel(canvas, box, palette)
    draw = ImageDraw.Draw(canvas)

    x1, y1, x2, y2 = box

    if frame:
        gold_frame(draw, box, palette, inset=12)

    if vertical_label:
        _vertical_text(
            draw, x2 - 48, y1 + 28, vertical_label, 15,
            palette["gold_light"], gap=5,
        )

    if seal_text:
        _seal(
            draw, x2 - 110, y1 + 30, 46, seal_text, palette, on_red=True,
        )

    return draw


def decorate_red_panel(canvas, box, palette):
    """在红块上叠加低透明度云纹、双线边框与角饰。"""

    x1, y1, x2, y2 = (int(v) for v in box)
    width, height = x2 - x1, y2 - y1

    if width < 60 or height < 60:
        return

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    stone = palette["stone"]
    white = "#FFFFFF"

    draw.rectangle(
        (14, 14, width - 15, height - 15),
        outline=_rgba(stone, 70), width=1,
    )
    draw.rectangle(
        (20, 20, width - 21, height - 21),
        outline=_rgba(stone, 36), width=1,
    )

    scale = max(14, min(width, height) * 0.07)

    draw_ruyi_cloud(
        draw, width * 0.84, height * 0.16, scale,
        _rgba(white, 30), 2,
    )
    draw_ruyi_cloud(
        draw, width * 0.18, height * 0.85, scale * 0.85,
        _rgba(white, 24), 2,
    )

    # 角饰：细线回纹
    corner = 34
    for ox, oy in ((26, 26), (width - 26, height - 26)):
        direction = 1 if ox < width / 2 else -1
        draw.line(
            (ox, oy, ox + direction * corner, oy),
            fill=_rgba(stone, 60), width=2,
        )
        draw.line(
            (ox, oy, ox, oy + direction * corner),
            fill=_rgba(stone, 60), width=2,
        )

    canvas.paste(layer, (x1, y1), layer)


def _footer(canvas, draw, palette, company, height=64):
    width, total_height = canvas.size
    draw.rectangle(
        (0, total_height - height, width, total_height),
        fill=palette["primary"],
    )
    _text(
        draw,
        (width - 32, total_height - height + 16),
        f"咨询热线  {company.get('phone', '')}",
        24,
        "#FFFFFF",
        bold=True,
        anchor="ra",
    )
    _text(
        draw,
        (32, total_height - height + 18),
        company.get("name", ""),
        22,
        "#FFFFFF",
        anchor="la",
    )


def _title_block(draw, palette, x, y, title, subtitle=None, title_size=48):
    draw.rectangle((x, y + 6, x + 8, y + title_size + 6), fill=palette["primary"])
    _text(draw, (x + 24, y), title, title_size, palette["dark"], bold=True)
    if subtitle:
        _text(
            draw, (x + 26, y + title_size + 14),
            subtitle, 22, palette["muted"]
        )
        return y + title_size + 52
    return y + title_size + 20


# ============================================================
# 1. Logo（1120×217）
# ============================================================

def render_logo(spec, size, out_path, palette, company):
    """
    spec: Qwen 返回的排版 JSON（可缺省，缺省用 company 资料兜底）
      {line1, line2, tagline, phone, emphasis, layout}
    """
    width, height = size
    spec = spec or {}

    line1 = spec.get("line1") or company.get("short_name") or company.get("name", "")
    line2 = spec.get("line2") or spec.get("tagline") or company.get("main_business", "")
    phone = spec.get("phone") or company.get("phone", "")
    emphasis = spec.get("emphasis") or ""
    accent = _hex(spec.get("accent_color"), palette["primary"])

    canvas = Image.new("RGB", (width, height), "#FFFFFF")
    draw = ImageDraw.Draw(canvas)

    left = 64

    # 左侧品牌竖条
    draw.rectangle((left, 52, left + 10, height - 52), fill=accent)

    # 主名称（强调词用品牌色）
    x = left + 34
    y = 48
    name_size = int(spec.get("line1_size") or 62)

    # 给右侧电话色块留出空间，文字过长时自动缩小
    reserved = 440 if phone else 0
    available = width - x - 64 - reserved
    while name_size > 32 and _text_width(
        draw, line1, get_font(name_size, bold=True)
    ) > available:
        name_size -= 2

    font = get_font(name_size, bold=True)

    if emphasis and emphasis in line1:
        prefix, rest = line1.split(emphasis, 1)
        cursor = x
        for chunk, color in (
            (prefix, palette["dark"]),
            (emphasis, accent),
            (rest, palette["dark"]),
        ):
            if not chunk:
                continue
            draw.text((cursor, y), chunk, font=font, fill=color)
            cursor += _text_width(draw, chunk, font)
    else:
        draw.text((x, y), line1, font=font, fill=palette["dark"])

    # 分隔线
    draw.line(
        (x, y + name_size + 16, width - 64, y + name_size + 16),
        fill=palette["gold"],
        width=1,
    )

    # 副标题 / 优势
    line2_size = int(spec.get("line2_size") or 26)
    while line2_size > 17 and _text_width(
        draw, line2, get_font(line2_size)
    ) > width - x - 64:
        line2_size -= 1

    _text(
        draw, (x, y + name_size + 28),
        line2, line2_size, palette["muted"]
    )

    # 右侧电话：大面积品牌红色块
    if phone:
        block_x1 = width - 430
        block_y1 = 38
        block_x2 = width - 46
        block_y2 = height - 38

        _rounded_rect(
            draw, (block_x1, block_y1, block_x2, block_y2),
            14, fill=accent,
        )

        _text(
            draw, (block_x1 + 28, block_y1 + 16),
            "咨询热线", 18, "#FFE1E1",
        )

        phone_size = int(spec.get("phone_size") or 44)
        while phone_size > 24 and _text_width(
            draw, phone, get_font(phone_size, bold=True)
        ) > (block_x2 - block_x1 - 52):
            phone_size -= 2

        _text(
            draw, (block_x2 - 26, block_y2 - 26),
            phone, phone_size, "#FFFFFF",
            bold=True, anchor="rb",
        )

    canvas.save(out_path, format="PNG")
    return out_path


def build_logo_fallback_spec(company):
    """Qwen 不可用时的确定性兜底排版。"""
    advantages = [
        item.get("short_copy") or item.get("title", "")
        for item in company.get("core_advantages", [])
        if item.get("short_copy") or item.get("title")
    ][:2]
    line2 = "   ".join(advantages) or company.get("main_business", "")
    return {
        "line1": company.get("short_name") or company.get("name", ""),
        "line2": line2,
        "emphasis": company.get("short_name", "")[:2],
        "phone": company.get("phone", ""),
        "accent_color": "#C50303",
        "layout": "上下两排",
    }


# ============================================================
# 2. PC 企业优势（核心优势 + 服务流程）
# ============================================================

def render_advantage_board(size, out_path, palette, company, image_path=None):

    width, height = size
    canvas = Image.new("RGB", (width, height), palette["bg"])
    draw = ImageDraw.Draw(canvas)

    margin = 70
    content_width = width - margin * 2

    # ---------------- 上半部：红色主视觉区 ----------------
    hero_bottom = 500
    draw = deco_header(
        canvas, (0, 0, width, hero_bottom), palette,
        vertical_label="匠心做工  保质守信", seal_text="硕景",
    )

    draw.rectangle((margin, 58, margin + 8, 58 + 52), fill="#FFFFFF")
    _text(draw, (margin + 24, 52), "企业核心优势", 48, "#FFFFFF", bold=True)
    _text(
        draw, (margin + 26, 118),
        f"{company.get('short_name', '')} · {company.get('main_business', '')}",
        22, palette["on_primary_muted"],
    )
    draw.line(
        (margin + 26, 160, margin + 330, 160),
        fill=palette["gold"], width=1,
    )

    advantages = company.get("core_advantages", [])[:4]

    col_gap = 60
    col_width = (content_width - col_gap) // 2
    row_height = 140
    start_y = 180

    for index, item in enumerate(advantages):
        row, col = divmod(index, 2)
        x = margin + col * (col_width + col_gap)
        cy = start_y + row * row_height

        _text(
            draw, (x, cy),
            f"{index + 1:02d}", 34, palette["stone"], bold=True,
        )

        _text(
            draw, (x + 62, cy - 2),
            item.get("title", ""), 28, "#FFFFFF", bold=True,
        )
        _draw_paragraph(
            draw, (x + 62, cy + 42),
            item.get("description", ""), 19,
            palette["on_primary_muted"],
            col_width - 62, line_gap=7, max_lines=2,
        )

        if index == 1:
            draw.line(
                (x + col_width - 20, start_y + 20,
                 x + col_width - 20, start_y + row_height * 2 - 20),
                fill=_rgba(palette["stone"], 60), width=1,
            )

    # ---------------- 下半部：浅色服务流程 ----------------
    _text(draw, (margin, hero_bottom + 40), "服务流程", 40, palette["dark"], bold=True)
    draw.rectangle(
        (margin + 2, hero_bottom + 96, margin + 86, hero_bottom + 101),
        fill=palette["primary"],
    )

    steps = company.get("service_process", [])[:4]
    step_area_y = hero_bottom + 130
    step_width = content_width // max(1, len(steps))

    for index, step in enumerate(steps):
        cx = margin + step_width * index + step_width // 2

        if index < len(steps) - 1:
            draw.line(
                (cx + 40, step_area_y + 34,
                 cx + step_width - 40, step_area_y + 34),
                fill=palette["line"], width=3,
            )

        draw.ellipse(
            (cx - 34, step_area_y, cx + 34, step_area_y + 68),
            fill=palette["primary"],
        )
        _text(
            draw, (cx, step_area_y + 34),
            str(step.get("step", index + 1)), 26, "#FFFFFF",
            bold=True, anchor="mm",
        )
        _text(
            draw, (cx, step_area_y + 90),
            step.get("name", ""), 25, palette["dark"],
            bold=True, anchor="ma",
        )
        _draw_paragraph(
            draw, (cx - step_width // 2 + 20, step_area_y + 128),
            step.get("description", ""), 18, palette["muted"],
            step_width - 40, line_gap=6, max_lines=2,
        )

    # ---------------- 企业实力数据卡 ----------------
    strength = company.get("company_strength", {}) or {}
    stats = [
        (strength.get("factory_area", ""), "自有生产资源"),
        (strength.get("industry_experience", ""), "专注行业经验"),
        ("设计·定制·施工", "一站式落地"),
    ]
    stats = [(value, label) for value, label in stats if value]

    if stats:
        stat_gap = 24
        stat_y = height - 260
        stat_h = 120
        stat_width = (content_width - stat_gap * (len(stats) - 1)) // len(stats)

        for index, (value, label) in enumerate(stats):
            sx = margin + index * (stat_width + stat_gap)
            _rounded_rect(
                draw, (sx, stat_y, sx + stat_width, stat_y + stat_h),
                12, fill=palette["card"], outline=palette["line"], width=1,
            )
            draw.rectangle(
                (sx, stat_y + 24, sx + 5, stat_y + stat_h - 24),
                fill=palette["primary"],
            )
            _text(
                draw, (sx + 26, stat_y + 24),
                value, 28, palette["primary"], bold=True,
            )
            _text(
                draw, (sx + 26, stat_y + 72),
                label, 18, palette["muted"],
            )

    # 底部信息
    footer_y = height - 92
    draw.rectangle((0, footer_y, width, height), fill=palette["primary"])
    draw.line((0, footer_y, width, footer_y), fill=palette["gold"], width=1)
    _text(
        draw, (margin, footer_y + 26),
        f"{company.get('name', '')}", 24, "#FFFFFF",
    )
    _text(
        draw, (width - margin, footer_y + 26),
        f"咨询热线  {company.get('phone', '')}", 28, "#FFFFFF",
        bold=True, anchor="ra",
    )

    canvas.save(out_path, format="PNG")
    return out_path


# ============================================================
# 3. 落地页企业优势（750×动态）
# ============================================================

def render_core_advantages_lp(size, out_path, palette, company, image_path=None):

    width, height = size
    canvas = Image.new("RGB", (width, height), palette["bg"])
    draw = ImageDraw.Draw(canvas)

    photo_height = 380

    # 顶部照片保持原色，不叠加任何颜色遮罩
    if image_path and Path(image_path).exists():
        _paste_cover(canvas, image_path, (0, 0, width, photo_height))
    else:
        draw.rectangle(
            (0, 0, width, photo_height), fill=palette["soft"]
        )

    draw = ImageDraw.Draw(canvas)

    # 红色标题带：向照片底部延伸并羽化，与照片自然衔接
    title_top = photo_height
    title_bottom = photo_height + 180
    red_panel(
        canvas, (0, title_top - 36, width, title_bottom),
        palette["primary"], edge="top", feather=36,
    )
    decorate_red_panel(canvas, (0, title_top, width, title_bottom), palette)
    draw = ImageDraw.Draw(canvas)

    gold_frame(draw, (0, title_top, width, title_bottom), palette, inset=10)
    _seal(draw, width - 104, title_top + 22, 42, "硕景", palette)
    _vertical_text(
        draw, width - 46, title_top + 20, "匠心工艺",
        14, palette["gold_light"], gap=4,
    )

    _text(draw, (40, title_top + 34), "企业核心优势", 44, "#FFFFFF", bold=True)
    _text(
        draw, (42, title_top + 96),
        f"{company.get('main_business', '')} · 源头厂家直供",
        22, palette["on_primary_muted"],
    )
    _text(
        draw, (42, title_top + 136),
        company.get("name", ""), 18, palette["on_primary_muted"],
    )
    draw.line(
        (42, title_top + 124, 300, title_top + 124),
        fill=palette["gold"], width=1,
    )

    y = title_bottom + 30
    margin = 30
    content_width = width - margin * 2
    card_height = 115
    gap = 10

    for index, item in enumerate(company.get("core_advantages", [])[:4]):

        cy = y + index * (card_height + gap)
        is_red = index % 2 == 0

        _rounded_rect(
            draw, (margin, cy, margin + content_width, cy + card_height),
            12,
            fill=palette["primary"] if is_red else palette["card"],
            outline=None if is_red else palette["line"],
            width=1,
        )

        circle_x = margin + 48
        circle_y = cy + card_height // 2

        draw.ellipse(
            (circle_x - 24, circle_y - 24, circle_x + 24, circle_y + 24),
            fill="#FFFFFF" if is_red else palette["primary"],
        )
        _text(
            draw, (circle_x, circle_y),
            f"{index + 1:02d}", 20,
            palette["primary"] if is_red else "#FFFFFF",
            bold=True, anchor="mm",
        )

        title_color = "#FFFFFF" if is_red else palette["dark"]
        desc_color = palette["on_primary_muted"] if is_red else palette["muted"]

        _text(
            draw, (margin + 92, cy + 20),
            item.get("title", ""), 27, title_color, bold=True,
        )
        _draw_paragraph(
            draw, (margin + 92, cy + 60),
            item.get("description", ""), 19, desc_color,
            content_width - 120, line_gap=5, max_lines=2,
        )

    draw.rectangle(
        (0, height - 120, width, height), fill=palette["primary_deep"]
    )
    draw.line((0, height - 120, width, height - 120), fill=palette["gold"], width=1)
    _text(draw, (margin, height - 92), "咨询热线", 22, "#FFE1E1")
    _text(
        draw, (margin, height - 60),
        company.get("phone", ""), 42, "#FFFFFF", bold=True,
    )
    _text(
        draw, (width - margin, height - 66),
        company.get("short_name", ""), 24, "#FFFFFF", anchor="ra",
    )

    canvas.save(out_path, format="PNG")
    return out_path


# ============================================================
# 4. 落地页服务流程（750×900）
# ============================================================

def render_process_steps(size, out_path, palette, company):

    width, height = size
    canvas = Image.new("RGB", (width, height), palette["bg"])
    draw = ImageDraw.Draw(canvas)

    header_bottom = 230
    draw = deco_header(
        canvas, (0, 0, width, header_bottom), palette,
        vertical_label="一站式整体解决方案", seal_text="硕景",
    )

    draw.rectangle((40, 54, 46, 108), fill="#FFFFFF")
    _text(draw, (66, 48), "服务流程", 46, "#FFFFFF", bold=True)
    _text(
        draw, (68, 116),
        "设计 · 生产 · 施工 · 指导，一站式对接", 22,
        palette["on_primary_muted"],
    )
    draw.line(
        (68, 154, 420, 154), fill=palette["gold"], width=1,
    )
    draw.line(
        (0, header_bottom, width, header_bottom),
        fill=palette["stone"], width=3,
    )

    # 浅色区域右侧的中式云纹装饰
    deco = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    deco_draw = ImageDraw.Draw(deco)
    draw_ruyi_cloud(
        deco_draw, width - 120, 420, 64,
        _rgba(palette["primary"], 20), 2,
    )
    draw_ruyi_cloud(
        deco_draw, width - 66, 560, 36,
        _rgba(palette["primary"], 14), 2,
    )
    canvas.paste(deco, (0, 0), deco)
    draw = ImageDraw.Draw(canvas)

    steps = company.get("service_process", [])
    y = 280
    margin = 56

    for index, step in enumerate(steps):

        draw.ellipse((margin, y, margin + 76, y + 76), fill=palette["primary"])
        _text(
            draw, (margin + 38, y + 38),
            str(step.get("step", index + 1)), 30, "#FFFFFF",
            bold=True, anchor="mm",
        )

        if index < len(steps) - 1:
            draw.line(
                (margin + 38, y + 80, margin + 38, y + 148),
                fill=palette["line"], width=3,
            )

        _text(
            draw, (margin + 104, y + 8),
            step.get("name", ""), 30, palette["dark"], bold=True,
        )
        _text(
            draw, (margin + 104, y + 52),
            step.get("description", ""), 21, palette["muted"],
        )

        y += 148

    draw.rectangle(
        (0, height - 88, width, height), fill=palette["primary"]
    )
    draw.line((0, height - 88, width, height - 88), fill=palette["gold"], width=1)
    _text(
        draw, (margin, height - 62),
        f"咨询热线  {company.get('phone', '')}", 28, "#FFFFFF", bold=True,
    )

    canvas.save(out_path, format="PNG")
    return out_path


# ============================================================
# 5. 联系咨询（PC 1420×444 / 落地页 750×800）
# ============================================================

def render_contact(size, out_path, palette, company, image_path=None,
                   left_blank=None):

    width, height = size

    # PC 端联系我们：只输出无文字的背景框，供后续在 CMS 里叠加内容
    if width > 900 and height <= 520:
        canvas = _vertical_gradient(
            (width, height), palette["primary_dark"], palette["primary_deep"]
        )
        decorate_red_panel(canvas, (0, 0, width, height), palette)
        draw = ImageDraw.Draw(canvas)
        gold_frame(draw, (0, 0, width, height), palette, inset=18)
        draw.line(
            (40, 28, 40, height - 28),
            fill=palette["gold"], width=3,
        )
        canvas.save(out_path, format="PNG")
        return out_path

    # 照片保持原色，不加红色遮罩
    if image_path and Path(image_path).exists():
        canvas = common.fit_cover(image_path, (width, height))
    else:
        canvas = _vertical_gradient(
            (width, height), palette["soft"], "#FFFFFF"
        )

    # 品牌红文字面板与照片用渐变过渡衔接
    if width > 900:
        panel_box = (0, 0, int(width * 0.46), height)
        edge = "right"
    else:
        panel_box = (0, 0, width, int(height * 0.46))
        edge = "bottom"

    draw = deco_header(
        canvas, panel_box, palette,
        vertical_label="", seal_text="",
        edge=edge, feather=110, frame=False,
    )

    x = 70 if width > 900 else 44
    top = 52 if width > 900 else 38

    # 印章与竖排小字放在红色实色区域，避开渐变过渡带
    if width > 900:
        _seal(draw, x + 360, height - 120, 44, "硕景", palette)
        _vertical_text(
            draw, panel_box[2] - 170, top + 10,
            "量身定制  一站式落地", 15, palette["gold_light"], gap=5,
        )
    else:
        _seal(draw, width - 104, top + 10, 42, "硕景", palette)
        _vertical_text(
            draw, width - 46, top + 14,
            "量身定制  一站式落地", 14, palette["gold_light"], gap=4,
        )

    _text(draw, (x, top), "联系我们", 46, "#FFFFFF", bold=True)
    draw.rectangle(
        (x, top + 68, x + 90, top + 74), fill=palette["gold"]
    )

    _text(
        draw, (x, top + 96),
        company.get("name", ""), 24, "#FFFFFF",
    )
    _text(
        draw, (x, top + 140),
        "咨询热线", 20, "#FFE1E1",
    )
    _text(
        draw, (x, top + 168),
        company.get("phone", ""), 58, "#FFFFFF", bold=True,
    )

    cta = "免费上门勘测 · 按需定制 · 一站式施工"
    _text(
        draw, (x, top + (262 if width > 900 else 252)),
        cta, 20, "#FFFFFF",
    )

    canvas.save(out_path, format="PNG")
    return out_path


# ============================================================
# 6. 多产品网格（落地页 / banner）
# ============================================================

def product_grid_height(width, count, columns=2, card_height=300,
                        header=170, footer=70, gap=20, margin=30):
    rows = math.ceil(max(1, count) / columns)
    return (
        header
        + rows * card_height
        + max(0, rows - 1) * gap
        + footer
        + margin
    )


def render_product_grid(size, out_path, palette, company, cards,
                        title="产品及服务范围", subtitle=None,
                        columns=2, card_height=300, show_footer=True):

    width, height = size
    canvas = Image.new("RGB", (width, height), palette["bg"])
    draw = ImageDraw.Draw(canvas)

    margin = 30
    gap = 20
    footer_height = 70 if show_footer else 0
    header_height = 180 if height >= 700 else 150

    # 顶部品牌红标题带（带中式装饰）
    draw = deco_header(
        canvas, (0, 0, width, header_height), palette,
        vertical_label="园林古建", seal_text="硕景",
    )

    _text(draw, (margin + 8, 40), title, 40, "#FFFFFF", bold=True)
    if subtitle:
        _text(
            draw, (margin + 10, 98), subtitle, 20,
            palette["on_primary_muted"],
        )

    draw.rectangle(
        (margin + 8, 30, margin + 14, 86), fill="#FFFFFF"
    )
    draw.line(
        (0, header_height, width, header_height),
        fill=palette["stone"], width=3,
    )

    content_width = width - margin * 2
    card_width = (content_width - gap * (columns - 1)) // columns

    grid_top = header_height + 24

    for index, card in enumerate(cards):

        row, col = divmod(index, columns)
        x = margin + col * (card_width + gap)
        y = grid_top + row * (card_height + gap)

        _rounded_rect(
            draw, (x, y, x + card_width, y + card_height),
            16, fill=palette["card"], outline=palette["line"], width=1,
        )

        image_height = int(card_height * 0.52)
        image_box = (x + 8, y + 8, x + card_width - 8, y + image_height)

        if card.get("image") and Path(card["image"]).exists():
            _paste_cover(canvas, card["image"], image_box, radius=12)
            draw = ImageDraw.Draw(canvas)
        else:
            draw.rectangle(image_box, fill=palette["stone"])

        # 卡片名称条：品牌红底白字
        bar_top = y + image_height + 8
        bar_height = 40
        draw.rectangle(
            (x + 8, bar_top, x + card_width - 8, bar_top + bar_height),
            fill=palette["primary"],
        )
        name_size = 22 if card_width >= 260 else 18
        _text(
            draw, (x + 18, bar_top + 8),
            card.get("name", ""), name_size, "#FFFFFF", bold=True,
        )
        draw.line(
            (x + 8, bar_top + bar_height, x + card_width - 8, bar_top + bar_height),
            fill=palette["gold"], width=1,
        )

        text_y = bar_top + bar_height + 10
        adv_size = 17 if card_width >= 260 else 14

        advantages = card.get("advantages", [])[:2]
        for adv_index, advantage in enumerate(advantages):
            _text(
                draw, (x + 14, text_y + adv_index * 23),
                advantage, adv_size, palette["primary"],
            )

        scope_y = text_y + len(advantages) * 23 + 2
        if card.get("scope"):
            _text(
                draw, (x + 14, scope_y),
                card["scope"], 14, palette["muted"],
            )

    if show_footer:
        draw.rectangle(
            (0, height - footer_height, width, height),
            fill=palette["primary"],
        )
        draw.line(
            (0, height - footer_height, width, height - footer_height),
            fill=palette["gold"], width=1,
        )
        _text(
            draw, (margin, height - footer_height + 22),
            f"{company.get('short_name', '')}  ·  咨询热线 {company.get('phone', '')}",
            22, "#FFFFFF", bold=True,
        )

    canvas.save(out_path, format="PNG")
    return out_path
