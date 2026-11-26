# 百度营销平台自动化 MVP

## 0. 全链路编排（资源包 → 本上传器）

本目录只负责「上传」这一段。从资源包到平台的一键全链路在 demo 根目录：

```bash
cd .. && cv/bin/python run_e2e.py --products 1     # 先跑通一个产品
cd .. && cv/bin/python run_e2e.py --all-products   # 再跑全部
```

编排细节（流水线阶段、幂等规则、换包流程）见根目录 `README.md`。


## 1. 安装

在项目目录中运行：

```bash
pip install playwright
playwright install chromium
```

## 2. 准备资料

**资料不需要手写**，跑一次适配脚本即可，它从上游 pipeline 的既有产物生成三份索引：

```bash
../cv/bin/python tools/build_data.py
```

脚本**只做格式适配，不生成素材、不编造文案**（`company.json` 里明确写了
"未提供的信息不允许模型自行编造"，脚本严格遵守：拼不出来的字段留空并写进 `_待确认`）。
图片一律用绝对路径引用 `output/`，不复制文件。

| 上游来源 | 产出 |
| --- | --- |
| `output/02_main_images/<产品>/` | `data/products/<产品>/product.json` |
| `marketing_demo/input/company.json` → `products[].advantages[].title` | 产品 `tags`（真实文案） |
| `output/04_designs/PC端/product_main_*/**.meta.json` | 产品文案兜底（company.json 未收录时） |
| `marketing_demo/input/company.json` → `company.company_profile` | 商户 `intro`（153 字真实简介） |
| `marketing_demo/input/联系我们.txt`（GBK） | 商户 `contact` / `address` |
| `output/04_plan.json`（region=落地页） | `data/h5_site.json` 的 PSD 导入顺序 |

重跑 pipeline 后重新执行一次即可，产品数量随 `02_main_images` 自动增减。

### 产品

`tags` 支持预设标签和自定义标签：脚本先尝试点击同名预设项，找不到就
输入到 `#sdk-for-product-tag-input-select` 并按 Enter（两种 UI 都兼容）。

`category_clicks` 目前是全局默认值（`config.json` 的 `default_category_clicks`），
**平台分类树与每个产品的真实分类需要逐个核对**，这是当前最大的未验证项。

图片文件缺失的产品目录会被跳过并告警，不会中断整批（例如旧的 `塑石假山` 占位目录）。

### 商户信息

图片放到 `data/images/`，文件名对应 `data/company.json` 的 `images` 键：

```json
"images": { "logo": "images/logo.png", "gallery": "images/gallery.jpg", "main": "images/main.jpg" }
```

- 键名对应 `modules/merchant_uploader.py` 里 `DEFAULT_SLOTS` 的三个槽位。
- `address` / `phone` / `contact` 已由脚本从 `联系我们.txt` 填入真实值；**留空即跳过**，不猜测。
- `region` 默认为 `{}`（不改动地区）。地址推导出的是「河北省保定市」，与录制里地区控件
  的当前值一致，所以无需切换；录制里那次「保定 → 沧州」是手工测试。
- 商家名称默认不改写；需要时把 `config.json` 的 `merchant_set_name` 设为 `true`。

### H5 落地页

`data/h5_site.json` 由脚本生成，6 个 PSD 按 `04_plan.json` 顺序导入：
`hero → product_service → core_advantages → product_details → service_process → contact`。

录制里的图层上移 / 插入空白模块步骤依赖具体 PSD 组合，默认不做；
需要时给对应模块填 `"layer_ops": {"insert_below": true, "move_up": 1}`。


## 3. 启动

```bash
python main.py --task product
```

程序打开浏览器后，手动登录并进入 基木鱼 -> 内容中心 -> 产品管理（页面应显示“新建产品”），回到终端按 Enter。

可选任务：

```bash
python main.py --task product
python main.py --task merchant
python main.py --task pc
python main.py --task h5
python main.py --task all
```

## 4. 当前模块接入状态

- **产品上传**：已接入。2026-11 录制修正了三处 —— 标签改为输入+Enter（兼容预设点击）、
  `手动录入` 改用 span filter、提交审核被遮挡层吃掉时先点 `关闭` 再重点一次。
  分类层级随账号变化，仍由 `product.json` 的 `category_clicks` 配置驱动。
- **商户信息**：已接入。三个图片槽位（logo / gallery / main）、商家简介、地区两级联动、
  地址电话联系人、`下一步` → `提交审核`。所有选择器可在 `config.json` 的
  `merchant_selectors` / `merchant_slots` 覆盖，**不要改代码**。
- **H5 站点**：已接入。机制是「模块导入 psd 内容」逐个导入 PSD
  （`input[name="file"]`，注意是 `name` 不是 `type`，与图片上传控件不同）。
  模块清单与顺序来自 `data/h5_site.json`。
- **PC 站点**：已搁置，等待重录。PC 端走的是「选择模板」路线，与 H5 的
  「新建空白站点 + PSD 导入」不同；此前录制在模板路线卡住，详见
  `modules/pc_site_creator.py` 的文件头说明。
- **Agent 兜底**：`recovery.py` 实现三层恢复，**默认关闭**。

录制原始证据（含逐行 `[OK]/[FRAG]/[MISS]/[NOISE]` 稳定性标注）见
`recordings/rec_2026_11_full.py`。

## 4.1 上传结果校验

点完 `确定` **不代表图片传上去了**：平台可能因格式/尺寸/大小拒绝，
弹一个约 2 秒就消失的 toast，表单里没有图，脚本却继续往下走，
直到提交审核才暴露，甚至静默发布缺图产品。

`common.wait_upload_confirmed()` 在每次上传后轮询两个证据：

- 出现拒绝文案（`上传失败` / `大小不能超过` / `请重新上传` …）→ 立即抛错
- 缩略图数量（`<img>` 与 CSS `background-image` 都算）超过上传前基线 → 判定成功

超时未满足即抛错，交给异常处理器。**后续若能拿到上传接口的 HAR，
改用接口返回码判定会更可靠。**

## 4.2 三层异常兜底（默认关闭）

`config.json` 里设 `"recovery": {"enabled": true}` 开启，仅在流程已经报错时触发：

| 层级 | 手段 | 是否调用模型 |
| --- | --- | --- |
| Tier 0 | 按已知安全文案清扫弹层（`知道了` / `取消` / `关闭` …） | 否 |
| Tier 1 | 采集页面**真实存在**的可交互元素清单，模型只能从中选一个 index | 文本 |
| Tier 2 | 截图 → 视觉模型返回 CSS 像素坐标 | 视觉 |

硬性约束（已在 `recovery.py` 内实现并通过离线测试）：

- 模型只能点清单里真实存在的元素，清单外 index 直接拒绝
- `提交审核` / `发布` / `删除` / `支付` 类按钮**永久拒绝**，模型坚持也不点
- 已经点过提交（`submit_started=True`）时**完全不做自动恢复**，直接转 `needs_review`
- 升级次数有上限（`max_text_attempts` / `max_vision_attempts`），不会死循环

API key 从环境变量 `WANXIANG_API_KEY` 或 `demo/.env` 读取，**不在代码里硬编码**。

注意分工：happy path 的 `common.safe_close_known_popups()` 只点纯提示类
（`知道了` / `稍后再说` / `跳过`），**不会**点 `取消` / `关闭`，
以免误关正在进行的上传对话框；更激进的清扫只在失败路径的 Tier 0 里发生。

## 5. 安全与防重复

- `browser_profile` 是登录状态敏感目录，不要提交 Git。
- `state/tasks_state.json` 记录任务状态。
- 如果状态为 `needs_review`，请先人工核实平台是否已创建/提交，避免重复创建。
- 当前不自动重试提交，避免不确定结果造成重复产品。
- `category_clicks` 使用 nth 索引，页面变化时容易失效，正式使用前应重新录制并改为稳定定位。
- 商户流程的三个图片槽位 trigger 都是 SVG `path:nth-child(n)` 级定位，**极其脆弱**；
  页面改版后请更新 `config.json` 的 `merchant_slots.<slot>.trigger`，不要改代码。
- `data/company.json` 里 `gallery` 槽位的实际业务用途尚未确认，接入生产前请先核对。
- 账号密码不要写进任何 `.py`；登录靠持久化 profile 手工完成一次。

## 6. Git 忽略建议

```gitignore
browser_profile/
state/
logs/
*.log
```
