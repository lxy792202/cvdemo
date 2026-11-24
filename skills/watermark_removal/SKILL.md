
---
name: watermark_removal
description: 检测商品图片是否需要去水印，并调用 Seedream 模型执行水印擦除和图片修复。
---

# Watermark Removal Skill

## 1. 功能说明

本 Skill 用于处理商品图片中的水印。

主要功能：

- 根据图片分析结果判断是否需要去水印。
- 调用 Seedream 图片编辑模型擦除水印。
- 自然修复水印覆盖区域。
- 尽可能保留商品原始外观。
- 保存处理后的图片。

## 2. 适用场景

当图片分析模块返回的 required_skills 中包含：

watermark_removal

则调用本 Skill。

如果 required_skills 中不包含 watermark_removal，则不需要执行去水印操作。

## 3. 输入参数

| 参数 | 类型 | 说明 |
|---|---|---|
| image_path | string | 待处理图片的本地路径 |
| output_path | string | 去水印后的图片保存路径 |

示例：

```python
run(
    "input/product.jpg",
    "output/product.png"
)
```

## 4. 执行流程

### Step 1：检查图片

- 检查输入文件是否存在。
- 检查图片格式是否受支持。
- 读取图片并转换为 Base64 Data URI。

### Step 2：图片分析

调用 Qwen 图片分析模块。

读取分析结果中的 required_skills 字段。

### Step 3：判断是否需要去水印

如果包含 watermark_removal：

- 调用 Seedream 图片编辑接口。
- 执行水印擦除。
- 修复水印覆盖区域。
- 保存处理结果。

如果不包含 watermark_removal：

- 不调用图片编辑模型。
- 将原图复制到输出目录。
- 返回无需处理的结果。

### Step 4：保存结果

将处理后的图片保存到指定 output_path。

### Step 5：返回执行结果

返回 JSON 格式的执行状态。

## 5. 图片处理要求

去水印时需要遵守以下要求：

1. 擦除图片中检测到的水印。
2. 自然修复水印覆盖区域。
3. 保留商品原有形状、颜色和纹理。
4. 不改变商品主体结构。
5. 不随意修改商品上的正常文字、商标和产品标识。
6. 不改变原始构图和主体位置。
7. 不额外添加文字、标识或水印。

## 6. 模型配置

图片编辑模型：

doubao-seedream-5-5-261028

接口：

POST /v1/images/generations

图片输入：

Base64 Data URI

图片输出：

Base64 图片数据或图片 URL。

## 7. 输出格式

成功执行：

```json
{
    "success": true,
    "image_path": "output/product.png",
    "has_watermark": true,
    "message": "图片去水印成功"
}
```

无需处理：

```json
{
    "success": true,
    "image_path": "output/product.png",
    "has_watermark": false,
    "message": "图片无需去水印"
}
```

执行失败时，应返回或抛出明确的错误信息，不得将失败结果标记为成功。

## 8. 调用入口

Python 模块：

skills.watermark_removal.watermark_removal

调用方式：

```python
from skills.watermark_removal.watermark_removal import run

result = run(
    image_path="input/product.jpg",
    output_path="output/product.png"
)
```

## 9. 注意事项

- 本 Skill 只负责去水印，不负责图片清晰度增强、扩图、裁切和拼图。
- 不要在本 Skill 中实现其他图片处理功能。
- 图片分析由独立的 Qwen 分析模块负责。
- 图片编辑由 Seedream 模型负责。
- 输出路径必须与输入路径区分，避免覆盖原始图片。
