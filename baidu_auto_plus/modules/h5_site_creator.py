def run_h5_site_task(page, config, logger):
    logger.warning("H5站点创建流程尚未接入完整 Codegen 录制；不会猜测页面按钮或自动提交。")
    return {"status": "not_configured", "detail": "请提供 H5 站点录制代码后接入"}
