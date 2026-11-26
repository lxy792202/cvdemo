# 2026-11 Codegen 录制存档（凭据已脱敏，登录段已删除）。
#
# 这是 product / merchant / site 三条流程选择器的唯一事实来源。
# 标注约定：
#   [OK]    语义定位，稳定，可直接移植
#   [FRAG]  依赖 nth / SVG path / 空文本，页面一改就失效，移植时必须改配置驱动
#   [MISS]  录制只 click 未 fill，字段规则未知，需要补充信息
#   [NOISE] 录制噪音，移植时丢弃
#
# 不要直接运行本文件；它保存的是原始证据，不是可执行流程。
# 登录由 browser.py 的持久化 profile 完成，不在此处硬编码账号密码。

import re
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    # ===== 登录段已删除：账号密码请走环境变量或持久化 profile，勿写入代码 =====

    # ================= 产品流程 =================
    with page.expect_popup() as page1_info:
        page.locator("div").filter(has_text=re.compile(r"^基木鱼$")).nth(4).click()   # [FRAG] nth(4)
    page1 = page1_info.value
    page1.locator(".index__step-close-14SC3").click()                                  # [FRAG] 带 hash 的 css module 类名
    with page1.expect_popup() as page2_info:
        page1.get_by_text("内容中心").click()                                          # [OK]
    page2 = page2_info.value
    page2.locator("span").nth(5).click()                                              # [FRAG] 裸 span + nth(5)，语义未知
    page2.get_by_role("button", name="新建产品").click()                               # [OK]
    page2.locator("span").filter(has_text="手动录入").first.click()                     # [OK]
    page2.get_by_text("其他").first.click()                                            # [OK] 一级分类
    page2.get_by_text("其他").nth(2).click()                                           # [FRAG] 分类层级随账号而变
    page2.get_by_role("textbox").first.click()                                        # [NOISE] 无 name
    page2.get_by_role("textbox", name="请输入产品长标题，4-100字符").click()             # [OK]
    page2.get_by_role("textbox", name="请输入产品长标题，4-100字符").fill("12345666")
    page2.get_by_role("textbox", name="请输入产品短标题，4-24字符").click()              # [OK]
    page2.get_by_role("textbox", name="请输入产品短标题，4-24字符").fill("1233")
    page2.get_by_text("每个标签4-20字符，最多可以添加6个标签").click()                   # [OK] 聚焦标签控件
    # 标签是自由输入 + Enter，不是点预设项（与旧录制不同，product_uploader 已改为两者兼容）
    page2.get_by_role("list").locator("#sdk-for-product-tag-input-select").fill("1111")     # [OK]
    page2.get_by_role("list").locator("#sdk-for-product-tag-input-select").press("Enter")
    page2.get_by_role("list").locator("#sdk-for-product-tag-input-select").fill("111111")
    page2.get_by_role("list").locator("#sdk-for-product-tag-input-select").press("Enter")
    page2.get_by_role("list").locator("#sdk-for-product-tag-input-select").fill("133333")
    page2.get_by_role("list").locator("#sdk-for-product-tag-input-select").press("Enter")

    # 主图：[OK] 类名虽长但是组件库稳定类名
    page2.locator(".dls-icon.vi-upload-add-cross-cross-icon").first.click()
    page2.get_by_role("button", name="本地上传").click()                                # [OK] 关键：先点本地上传
    page2.locator("input[type=\"file\"]").first.set_input_files("main_image.jpg")
    page2.get_by_role("button", name="确定").nth(1).click()                             # [FRAG] 双确定，nth(1) 先点
    page2.get_by_role("button", name="确定").click()

    # 方图（1:1）：[FRAG] 整条路径都是 nth-child 级
    page2.locator("#sdk-content-oneToOne > .one-col-19 > .picture-container > .inner-picture > .common-image-uploader > .vi.vi-ccsdk-image-upload > .vi.vi-image-upload-anchor > .vi.vi-upload-anchor > .vi").click()
    page2.get_by_role("button", name="本地上传").click()
    page2.locator("input[type=\"file\"]").first.set_input_files("main_image_1_1.jpg")
    page2.get_by_role("button", name="确定").nth(1).click()
    page2.get_by_role("button", name="确定").click()

    # 详情图：[OK] .fr-element 是富文本编辑器容器
    page2.locator(".fr-element").click()
    page2.get_by_role("button", name="本地上传").click()
    page2.locator("input[type=\"file\"]").first.set_input_files("main_image.jpg")
    page2.get_by_role("button", name="确定").nth(1).click()
    page2.get_by_role("button", name="确定").click()

    # 提交：第一次点击被遮挡层吃掉，需先「关闭」再点一次 —— 此时尚未提交，重试安全
    page2.get_by_role("button", name="提交审核").click()                                # [OK]
    page2.get_by_role("button", name="关闭").click()                                    # [OK] 遮挡层
    page2.get_by_role("button", name="提交审核").click()

    # ================= 商户信息流程 =================
    with page2.expect_popup() as page3_info:
        page2.locator(".one-menu-item.one-tooltip-open > .one-menu-item-span > .one-menu-item-custom-icon > .dls-icon").click()   # [FRAG] 纯图标菜单
    page3 = page3_info.value
    page3.get_by_text("商家管理").click()                                               # [OK]
    page3.get_by_text("商户信息").click()                                               # [OK]
    page3.get_by_role("button", name="编辑").click()                                    # [OK] 说明是编辑已有商户，非新建

    # 图片位 logo：[FRAG] trigger 是裸 SVG path
    page3.locator("span:nth-child(3) > .dls-icon > path").first.click()
    page3.locator(".vi.vi-upload-add-cross").first.click()                              # [OK] opener
    page3.get_by_role("button", name="本地上传").click()
    page3.locator("input[type=\"file\"]").first.set_input_files("河北硕景园林古建筑工程有限公司_logo.png")
    page3.get_by_role("button", name="确定").nth(1).click()
    page3.get_by_role("button", name="确定").click()

    # 图片位 gallery（图片选择器第 2 行）：[FRAG] 最脆弱的一条，用途待确认
    page3.locator("div:nth-child(2) > .sdk-img-picker-rows-item-main > .sdk-img-picker-rows-item-main-picker > .common-image-uploader > .vi.vi-ccsdk-image-upload > .vi.vi-image-upload-anchor > .vi > .vi-image-upload-anchor-uploader-container > .vi-image-upload-anchor-operation-container > span:nth-child(3) > .dls-icon > path:nth-child(2)").click()
    page3.locator(".vi.vi-upload-add-cross").first.click()
    page3.get_by_role("button", name="本地上传").click()
    page3.locator("input[type=\"file\"]").first.set_input_files("519760adec58974ca1a003294522b69b.jpg")
    page3.get_by_role("button", name="确定").nth(1).click()
    page3.get_by_role("button", name="确定").click()

    page3.get_by_role("textbox", name="请输入商家简介，1-1000个字符").click()            # [OK]
    page3.get_by_role("textbox", name="请输入商家简介，1-1000个字符").click()            # [NOISE] 重复 click
    page3.get_by_role("textbox", name="请输入商家简介，1-1000个字符").fill("       111122233411邯郸中电光谷智造园发展有限公司……")

    # 图片位 main：[FRAG] path 级，且录制里连点两次（第一次可能没生效）
    page3.locator(".inner-picture > .common-image-uploader > .vi.vi-ccsdk-image-upload > .vi.vi-image-upload-anchor > .vi > .vi-image-upload-anchor-uploader-container > .vi-image-upload-anchor-operation-container > span:nth-child(3) > .dls-icon > path:nth-child(2)").first.click()
    page3.locator(".inner-picture > .common-image-uploader > .vi.vi-ccsdk-image-upload > .vi.vi-image-upload-anchor > .vi > .vi-image-upload-anchor-uploader-container > .vi-image-upload-anchor-operation-container > span:nth-child(3) > .dls-icon > path:nth-child(2)").first.click()
    page3.locator(".dls-icon.vi-upload-add-cross-cross-icon").first.click()
    page3.get_by_role("button", name="本地上传").click()
    page3.locator("input[type=\"file\"]").first.set_input_files("main_image.jpg")
    page3.get_by_role("button", name="确定").nth(1).click()
    page3.get_by_role("button", name="确定").click()

    page3.get_by_role("button", name="知道了").click()                                  # [OK] 引导弹窗
    page3.get_by_role("button", name="确定").click()                                    # [FRAG] 语义不明的确定

    # 地区两级联动：先点当前地区 chip，再点目标城市
    page3.locator("div").filter(has_text=re.compile(r"^河北省保定市$")).nth(4).click()   # [FRAG] 硬编码当前值 + nth(4)
    page3.get_by_text("沧州市").click()                                                 # [OK] 但这次切换疑似手工测试

    page3.get_by_role("textbox", name="请输入联系地址").click()                          # [MISS] 未 fill，长度/必填未知
    page3.get_by_role("textbox", name="请输入联系地址").click()                          # [NOISE]
    page3.get_by_role("textbox", name="请输入联系电话").click()                          # [MISS]
    page3.get_by_role("textbox", name="如：马先生，最多10个字符").click()                 # [MISS] 联系人，上限 10 字符
    page3.get_by_role("button", name="下一步").click()                                  # [OK] 说明商户表单是分步的
    page3.get_by_role("button", name="提交审核").click()                                # [OK]

    # ================= PC 建站流程（已搁置）=================
    # 核心机制：不是拖拽、不是普通图片上传，而是「模块导入 psd 内容」逐个导入 PSD
    page3.get_by_text("我的站点").click()                                               # [OK]
    page3.get_by_role("button", name="新建站点").click()                                # [OK]
    page3.get_by_role("button", name="选择模板").click()                                # [NOISE] 模板路线被放弃
    page3.get_by_role("button", name="使用").nth(1).click()                             # [NOISE]
    page3.get_by_role("textbox", name="请输入站点名称,最多30个字符").click()              # [OK] 注意：半角逗号
    page3.get_by_role("textbox", name="请输入站点名称,最多30个字符").fill("21111111")
    page3.get_by_role("button", name="确定").click()                                    # [NOISE] 连续 5 次确定
    page3.get_by_role("button", name="确定").click()
    page3.get_by_role("dialog", name="新建站点").click()
    page3.locator(".one-button.one-dialog-close").click()                               # [OK] 关掉残留弹窗
    page3.get_by_role("button", name="新建站点").click()
    page3.get_by_role("button", name="新建空白站点").click()                             # [OK] 真正采用的路线
    page3.get_by_role("button", name="关闭").click()                                    # [OK] 引导层

    # ---- PSD 模块导入循环（重复 4 次）----
    page3.get_by_role("button", name="模块导入psd内容").click()                          # [OK] 关键入口
    page3.get_by_role("button", name="选择PSD文件").click()                              # [OK]
    page3.locator("input[name=\"file\"]").set_input_files("河北硕景园林古建筑工程有限公司_优势.psd")   # [OK] 注意是 name=file，非 type=file
    page3.get_by_text("新建站点 请遵循规则修改页面名称，修改后需提交送审，审核通过则修改成功 查看修改规则 全新主题色功能，一键设置全局换色，快来体验吧 知道了 页面全局 | 主题色").click()   # [FRAG] 整段文案匹配，极不稳定
    page3.get_by_role("tab", name="图层").click()                                        # [OK]
    page3.locator(".dls-icon.m-captain-icon.m-captain-icon-move-up").click()             # [FRAG] 图层上移，次数取决于 PSD
    page3.get_by_role("button", name="提交").click()                                     # [OK] 模块级提交
    page3.get_by_role("button", name="保存").click()                                     # [OK]
    page3.get_by_role("tab", name="属性").click()                                        # [OK]

    page3.get_by_role("button", name="模块导入psd内容").click()
    page3.get_by_role("button", name="选择PSD文件").click()
    page3.locator("input[name=\"file\"]").set_input_files("河北硕景园林古建筑工程有限公司_banner2.psd")
    page3.get_by_role("button", name="提交").click()
    page3.locator(".card-header > .el-icon-close").click()                               # [FRAG] element-ui 关闭图标
    page3.get_by_role("button", name="提交").click()
    page3.get_by_text("松手放到这里 松手放到这里 松手放到这里").nth(1).click()             # [NOISE] 拖拽提示文案，疑似误触
    page3.get_by_role("img", description="在下方插入空白模块", exact=True).click()         # [OK] 指定插入位置

    page3.get_by_role("button", name="模块导入psd内容").click()
    page3.get_by_role("button", name="选择PSD文件").click()
    page3.locator("input[name=\"file\"]").set_input_files("落地页_联系咨询.psd")
    page3.get_by_role("tab", name="图层").click()
    page3.locator(".layer-operate-move-up > .dls-icon").click()                          # [FRAG] 与上面 move-up 类名不同，说明有两套 UI
    page3.get_by_role("img", description="在下方插入空白模块", exact=True).click()
    page3.get_by_text("新建站点 请遵循规则修改页面名称……").click()                          # [FRAG] 同上
    page3.get_by_text("属性").click()                                                    # [NOISE] 与 tab 版重复

    page3.get_by_role("button", name="模块导入psd内容").click()
    page3.get_by_role("button", name="选择PSD文件").click()
    page3.locator("input[name=\"file\"]").set_input_files("落地页_企业核心优势.psd")
    page3.get_by_role("button", name="提交").click()

    # ---- 收尾：站点命名 ----
    page3.get_by_role("textbox", name="请输入站点名称，最多30个字符").click()              # [FRAG] 全角逗号，与前面半角版本是不同输入框
    page3.get_by_role("textbox", name="请输入站点名称，最多30个字符").fill("111111")
    page3.once("dialog", lambda dialog: dialog.dismiss())                                # [OK] 原生 beforeunload，必须 dismiss
    page3.get_by_role("button", name="确定").click()
    # [MISS] 录制到此结束，没有站点级「提交/发布」的成功判据

    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
