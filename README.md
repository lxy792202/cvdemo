# 营销全链路自动化：资源包 → 图片/设计 → 平台上传

一条命令把「拿到的资源包」变成「百度基木鱼平台上的产品 / 商户信息 / H5 落地页」：

```
marketing_demo/input/（资源包：企业资料、产品图、联系文档）
        │  run_pipeline.py  Stage 1 图片处理 → 2 主图 → 3 选图 → 4 设计图/PSD
        ▼
output/（01_processed / 02_main_images / 04_designs / 04_plan.json）
        │  baidu_auto_plus/tools/build_data.py（只适配格式，不编造文案）
        ▼
baidu_auto_plus/data/（products/*.json / company.json / h5_site.json）
        │  baidu_auto_plus/main.py --task all（一次浏览器会话）
        ▼
基木鱼平台：产品上传 → 商户信息 → H5 建站（PSD 逐个导入）
```

## 目录职责

| 目录 | 职责 |
| --- | --- |
| `marketing_demo/input/` | 资源包入口（company.json、产品中心图片、联系我们.txt 等） |
| `pipeline/`、`skills/`、`run_pipeline.py` | 图片处理与营销设计图/PSD 生成（Stage 1-4） |
| `output/` | 流水线产物（主图、设计图、PSD、计划 json） |
| `baidu_auto_plus/` | 浏览器上传自动化（产品/商户/H5；PC 建站搁置） |
| `run_e2e.py` | 顶层编排：流水线 → 数据桥 → 上传，任一步失败即中断 |
| `cv/` | Python 虚拟环境（所有命令用 `cv/bin/python`；勿入 git） |

## 首次准备

1. `config.py`（demo 根目录）配置流水线 `API_KEY`——已在 .gitignore，不要提交。
2. 登录态：首次跑上传时**不加 `--yes`** 手动登录一次百度账号，之后复用
   `baidu_auto_plus/browser_profile/`（含会话 cookie，已 gitignore）。
3. 上传开关在 `baidu_auto_plus/config.json`：`submit_review`、`product_limit`、
   `skip_success`、`merchant_skip_existing_images` 等。

## 日常用法（demo 目录下）

```bash
cv/bin/python run_e2e.py --products 1      # 全链路测试：只传 1 个产品
cv/bin/python run_e2e.py --all-products    # 全链路批量：传完所有待传产品
cv/bin/python run_e2e.py --skip-pipeline   # 复用 output/ 既有产物，只跑桥接+上传
cv/bin/python run_e2e.py --rerun-success   # 商户/建站即使已成功也重跑（整链路演示）
cv/bin/python run_e2e.py --force-pipeline  # 忽略已有产物重生成（慢、耗 API）
cv/bin/python run_e2e.py --from-stage 3    # 流水线从指定阶段开始
```

单独跑某一段：

```bash
cv/bin/python run_pipeline.py --from 2          # 只跑流水线
cd baidu_auto_plus && ../cv/bin/python main.py --task all   # 只跑上传（交互）
```

## 幂等与状态

- 流水线各阶段有 manifest（`output/_manifests/`），已有产物自动跳过。
- `baidu_auto_plus/state/tasks_state.json` 按任务记录 success；`skip_success=true`
  时默认跳过已成功任务；`--rerun-success` 临时关闭。
- 产品**永不重传已成功项**（防止列表堆重复条目）；商户图片槽位已有平台图时
  跳过重复上传（平台对同内容重传会报「1张图片处理失败」）。
- 上传段任一任务 failed/needs_review → `main.py` 退出码非 0 → e2e 中断后续步骤。

## 换新资源包

1. 把新包文件放入 `marketing_demo/input/`（企业资料、产品图、联系文档）。
2. `cv/bin/python run_e2e.py --products 1` 验证一个产品跑通。
3. `cv/bin/python run_e2e.py --all-products` 批量执行。
   新包会真实调用模型生成素材；缺图的产品 build_data 会明确列出并不上传。

## 已知约束

- 产品单图 ≤2MB（平台硬限制，超了平台直接拒绝）。
- H5 建站 = 空白站点 + PSD 模块逐个导入（模块间要点「+」定位插入点）；
  PC 建站搁置，等重录。
- 百度后台入口会变（www2 会重定向到 oneWeb 门户），脚本已做归位与直连兜底；
  再遇到改版看 `logs/screenshots/` 的错误截图修选择器。

## 安全

- `config.py`（API_KEY）、`upload_script.py`（账号密码）仅限本地，勿提交。
- `baidu_auto_plus/browser_profile/`、`logs/`、`state/` 均已在 .gitignore。
