"""PC 站点创建 —— 已搁置，等待可用录制。

The 2026-11 Codegen recording revealed the real mechanism: 建站 is NOT
drag-and-drop and NOT plain image upload. It is **repeated PSD module import**,
which lines up with pipeline/psd_export.py upstream. Recorded flow:

    我的站点 -> 新建站点 -> 新建空白站点 -> 关闭（引导层）
    然后对每个 PSD 重复：
        模块导入psd内容 -> 选择PSD文件
        -> input[name="file"].set_input_files("xxx.psd")
        -> 提交
    期间穿插：
        tab「图层」 -> .layer-operate-move-up > .dls-icon（上移图层）
        get_by_role("img", description="在下方插入空白模块")（插入位置）
        tab「属性」
        .card-header > .el-icon-close（关掉导入进度卡片）
    收尾：
        站点名称 fill -> page.once("dialog", lambda d: d.dismiss()) -> 确定

Recorded PSD files: 优势.psd / banner2.psd / 落地页_联系咨询.psd /
落地页_企业核心优势.psd

Why this is still not automated:
  * The recording contains abandoned branches (选择模板 route, stray
    context.new_page()/close() pairs, five consecutive 确定 clicks).
  * Layer ordering (move-up) and insert position depend on the PSD set, so the
    loop needs a per-site manifest rather than a fixed sequence.
  * The user reports PC 端 still has issues; do not guess at the fix.

When resuming, drive it from a JSON manifest like:
    {"site_name": "...", "modules": [{"psd": "banner2.psd", "insert_after": 0}]}
and reuse common.upload_local_file / wait_upload_confirmed for each import.
"""


def run_pc_site_task(page, config, logger):
    logger.warning(
        "PC站点创建已搁置：录制显示流程是「模块导入psd内容」逐个导入 PSD，"
        "但录制含中断分支，不会猜测页面按钮或自动提交。"
    )
    return {
        "status": "not_configured",
        "detail": "PC 建站为 PSD 模块导入流程，待补充干净录制后接入",
    }
