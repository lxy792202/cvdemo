"""导出带图层的 PSD。

- PIL 版式：拆成「背景」+「文字」两个图层；
- Seedream 生成的图：保留为「设计图（AI生成）」一个图层；
- PSD 使用 RLE 压缩，带合并预览，文件 < 50MB。
"""

from pathlib import Path

import numpy as np
import packbits

import pytoshop.codecs
from PIL import Image
from pytoshop import core, enums
from pytoshop.user import nested_layers

from pipeline import common


# pytoshop 的 RLE 压缩依赖 packbits，但不会自动导入
pytoshop.codecs.packbits = packbits

MAX_PSD_BYTES = 50 * 1024 * 1024
TEXT_DIFF_THRESHOLD = 6


def _to_layer(image, name):
    """PIL RGBA 图 -> pytoshop 图层。"""

    image = image.convert("RGBA")
    channels = np.array(image)

    return nested_layers.Image(
        name=name,
        top=0,
        left=0,
        channels={
            0: channels[:, :, 0],
            1: channels[:, :, 1],
            2: channels[:, :, 2],
            -1: channels[:, :, 3],
        },
        color_mode=enums.ColorMode.rgb,
    )


def text_layer_from_diff(final_image, background_image):
    """用“成品 - 无文字背景”的像素差异生成文字层。"""

    final = final_image.convert("RGB")
    background = background_image.convert("RGB")

    if background.size != final.size:
        background = background.resize(
            final.size, Image.Resampling.LANCZOS
        )

    final_arr = np.asarray(final, dtype=np.int16)
    base_arr = np.asarray(background, dtype=np.int16)

    diff = np.max(np.abs(final_arr - base_arr), axis=2)
    mask = diff > TEXT_DIFF_THRESHOLD

    rgba = np.zeros((final.height, final.width, 4), dtype=np.uint8)
    rgba[:, :, :3] = np.asarray(final, dtype=np.uint8)
    rgba[:, :, 3] = 255
    rgba[~mask, 3] = 0

    return Image.fromarray(rgba, mode="RGBA")


def write_layered_psd(layers, final_image, out_path):
    """
    写出 PSD。

    layers：从下到上的 [(name, PIL.Image), ...]
    final_image：合并预览图（也作为 PSD 的合并图像数据）
    """

    out_path = common.abs_path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    final = final_image.convert("RGB")
    width, height = final.size

    psd_layers = [_to_layer(image, name) for name, image in layers]

    psd = nested_layers.nested_layers_to_psd(
        psd_layers,
        color_mode=enums.ColorMode.rgb,
        size=(width, height),
        compression=enums.Compression.rle,
    )

    # 写入合并预览，保证某些预览器不显示黑图
    composite = np.asarray(final, dtype=np.uint8).transpose(2, 0, 1)
    psd.image_data = core.ImageData(
        channels=composite,
        compression=enums.Compression.rle,
    )

    with open(out_path, "wb") as f:
        psd.write(f)

    size = out_path.stat().st_size
    if size > MAX_PSD_BYTES:
        raise ValueError(
            f"PSD 超过 50MB：{size / 1024 / 1024:.1f}MB -> {out_path}"
        )

    return out_path


def export_design_psds(design_dir=None, exclude_module_ids=("logo",),
                       force=False, background_provider=None):
    """
    导出全部设计图 PSD。

    background_provider(png_path, module_id) -> PIL.Image | None
      返回无文字背景层；返回 None 时只写单层 AI 设计图。
    """

    design_dir = common.abs_path(design_dir or common.DESIGN_DIR)

    exported = []
    skipped = []

    for png in sorted(design_dir.rglob("*.png")):
        module_id = png.parent.name

        if module_id in exclude_module_ids:
            continue

        psd = png.with_suffix(".psd")

        if (
            not force
            and psd.exists()
            and psd.stat().st_mtime >= png.stat().st_mtime
        ):
            skipped.append(common.rel_path(psd))
            continue

        try:
            with Image.open(png) as img:
                final_image = img.convert("RGBA").copy()

            background = None
            if background_provider:
                background = background_provider(png, module_id)

            if background is not None:
                text_layer = text_layer_from_diff(final_image, background)
                layers = [
                    ("背景", background),
                    ("文字", text_layer),
                ]
            else:
                layers = [("设计图（AI生成）", final_image)]

            write_layered_psd(layers, final_image, psd)
            size_mb = psd.stat().st_size / (1024 * 1024)

            print(
                f"PSD 导出：{common.rel_path(psd)}"
                f"（{len(layers)} 层 / {size_mb:.2f}MB）"
            )
            exported.append({
                "source": common.rel_path(png),
                "psd": common.rel_path(psd),
                "layers": [name for name, _ in layers],
                "size_mb": round(size_mb, 2),
            })

        except Exception as exc:
            print(f"PSD 导出失败：{common.rel_path(png)} -> {exc!r}")

    return {"exported": exported, "skipped": skipped}
