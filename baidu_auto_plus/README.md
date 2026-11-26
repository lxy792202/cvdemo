# 百度营销平台自动化 MVP

## 1. 安装

在项目目录中运行：

```bash
pip install playwright
playwright install chromium
```

## 2. 准备产品资料

每个产品单独一个文件夹，按示例准备 `product.json` 和对应图片。
图片路径相对于该产品文件夹。请把样例图片替换成真实图片文件。

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

- 产品上传：已接入基于此前录制流程的 MVP；分类索引、图片上传确认和详情编辑器定位可能随页面变化，先只测试一个产品。
- 商户信息：读取 `data/company.json`，页面操作尚待商户信息 Codegen 录制代码。
- PC站点：页面操作待完整 Codegen。
- H5站点：页面操作待完整 Codegen。
- Agent：当前为异常采集（截图+页面文本），没有开放自主点击/提交。后续可接视觉/LLM，建议采用受限动作白名单。

## 5. 安全与防重复

- `browser_profile` 是登录状态敏感目录，不要提交 Git。
- `state/tasks_state.json` 记录任务状态。
- 如果状态为 `needs_review`，请先人工核实平台是否已创建/提交，避免重复创建。
- 当前不自动重试提交，避免不确定结果造成重复产品。
- `category_clicks` 使用 nth 索引，页面变化时容易失效，正式使用前应重新录制并改为稳定定位。

## 6. Git 忽略建议

```gitignore
browser_profile/
state/
logs/
*.log
```
