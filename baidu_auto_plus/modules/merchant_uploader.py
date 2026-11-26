from pathlib import Path
import json
from state_manager import load_state, set_task
from agent_handler import AgentExceptionHandler

ROOT = Path(__file__).resolve().parents[1]

def run_merchant_task(page, config, logger):
    """
    Merchant profile automation placeholder.
    Insert recorded locators/actions here after merchant profile Codegen is complete.
    Do not invent field selectors or submit behavior.
    """
    data_file = ROOT / "data" / "company.json"
    if not data_file.exists():
        logger.warning("缺少商户资料文件：%s", data_file)
        return {"status": "not_configured", "detail": "先准备 data/company.json 并补录商户页面操作"}
    data = json.loads(data_file.read_text(encoding="utf-8"))
    logger.info("已读取商户资料：%s", data.get("company_name", "(未填写名称)"))
    logger.warning("商户页面自动填写尚未接入：请提供商户信息 Codegen 录制代码后接入")
    return {"status": "not_configured", "detail": "资料已读取，页面自动化待接入"}
