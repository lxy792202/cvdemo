def run_pc_site_task(page, config, logger):
    logger.warning("PC站点创建流程尚未接入完整 Codegen 录制；不会猜测页面按钮或自动提交。")
    return {"status": "not_configured", "detail": "请提供完整 PC 站点录制代码后接入"}
