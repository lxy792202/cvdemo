# import time
# from playwright.sync_api import sync_playwright

# # 登录信息：在本地填写
# USERNAME = "pangu8888"
# PASSWORD = "PANgu8888"

# with sync_playwright() as p:
#     context = p.chromium.launch_persistent_context(
#         user_data_dir="./baidu_browser_data",
#         headless=False,
#         viewport={"width": 1440, "height": 900}
#     )

#     page = context.pages[0] if context.pages else context.new_page()

#     page.goto("https://www2.baidu.com", wait_until="domcontentloaded")

#     # 等待登录框
#     page.get_by_placeholder("请输入账号").wait_for(timeout=20000)

#     # 填写账号密码
#     page.get_by_placeholder("请输入账号").fill(USERNAME)
#     page.get_by_placeholder("请输入密码").fill(PASSWORD)

#     # 自动点击登录
#     page.get_by_role("button", name="登录").click()

#     # 等待登录响应
#     time.sleep(5)

#     # print("当前网址：", page.url)
#     # print("当前标题：", page.title())

#     # page.screenshot(path="login_result.png", full_page=True)

#     # input("检查登录结果，按回车关闭浏览器...")
#     # context.close()
#         # 找到并点击「基木鱼」
#     jimu = page.get_by_text("基木鱼", exact=True)
#     jimu.wait_for(state="visible", timeout=30000)
#     jimu.click()

#     print("已点击基木鱼")
#     page.wait_for_timeout(5000)

#     print("当前网址：", page.url)
#     print("当前标题：", page.title())

#     page.screenshot(path="jimu_page.png", full_page=True)

#     input("检查基木鱼页面，按回车退出...")
#     context.close()






import base64
import mimetypes
import requests
from pathlib import Path

from config import BASE_URL, API_KEY, IMAGE_MODEL


def encode_image(image_path):
    path = Path(image_path)

    mime_type, _ = mimetypes.guess_type(str(path))

    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError("不支持的图片格式")

    with open(path, "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime_type};base64,{image_base64}"


def test_seedream(image_path):
    url = BASE_URL.rstrip("/") + "/images/generations"

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": IMAGE_MODEL,
        "prompt": """
        请擦除商品图片中允许处理的水印。
        自然修复水印覆盖区域。
        保留商品原有形状、颜色、纹理和细节。
        不改变商品主体和原始构图。
        """,
        "image": encode_image(image_path),
        "response_format": "b64_json",
        "output_format": "png",
        "watermark": False
    }

    print("请求地址：", url)
    print("调用模型：", IMAGE_MODEL)

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=300
    )

    print("HTTP 状态码：", response.status_code)

    if not response.ok:
        print("错误响应：", response.text)
        response.raise_for_status()

    result = response.json()

    print("响应字段：", list(result.keys()))

    image_data = result["data"][0]

    if image_data.get("b64_json"):
        image_bytes = base64.b64decode(image_data["b64_json"])

        output_path = Path("output")
        output_path.mkdir(parents=True, exist_ok=True)

        save_path = output_path / "test_no_watermark.png"
        save_path.write_bytes(image_bytes)

        print("图片保存成功：", save_path)

    elif image_data.get("url"):
        print("模型返回图片 URL：", image_data["url"])
        print("暂未下载，下一步处理。")

    else:
        print("未找到图片数据：")
        print(result)


if __name__ == "__main__":
    test_seedream("test.jpg")
