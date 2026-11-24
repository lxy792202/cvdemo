import time
from playwright.sync_api import sync_playwright

# 登录信息：在本地填写
USERNAME = "pangu8888"
PASSWORD = "PANgu8888"

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir="./baidu_browser_data",
        headless=False,
        viewport={"width": 1440, "height": 900}
    )

    page = context.pages[0] if context.pages else context.new_page()

    page.goto("https://www2.baidu.com", wait_until="domcontentloaded")

    # 等待登录框
    page.get_by_placeholder("请输入账号").wait_for(timeout=20000)

    # 填写账号密码
    page.get_by_placeholder("请输入账号").fill(USERNAME)
    page.get_by_placeholder("请输入密码").fill(PASSWORD)

    # 自动点击登录
    page.get_by_role("button", name="登录").click()

    # 等待登录响应
    time.sleep(5)

    # print("当前网址：", page.url)
    # print("当前标题：", page.title())

    # page.screenshot(path="login_result.png", full_page=True)

    # input("检查登录结果，按回车关闭浏览器...")
    # context.close()
        # 找到并点击「基木鱼」
    jimu = page.get_by_text("基木鱼", exact=True)
    jimu.wait_for(state="visible", timeout=30000)
    jimu.click()

    print("已点击基木鱼")
    page.wait_for_timeout(5000)

    print("当前网址：", page.url)
    print("当前标题：", page.title())

    page.screenshot(path="jimu_page.png", full_page=True)

    input("检查基木鱼页面，按回车退出...")
    context.close()