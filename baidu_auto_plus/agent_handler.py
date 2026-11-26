from common import screenshot
from recovery import RecoveryGuard

class AgentExceptionHandler:
    """
    Exception hook with layered recovery.

    Captures page state, then hands the blocker to RecoveryGuard (popup sweep ->
    grounded text decision -> vision coordinates). The guard is whitelist-only
    and can never pick a submit/publish/delete button.
    It must not autonomously click submit or repeat creation when outcome is uncertain.
    """
    def __init__(self, page, config, logger):
        self.page, self.config, self.logger = page, config, logger

    def handle(self, task_name, error, submit_started=False):
        shot = screenshot(self.page, self.config, f"{task_name}_error")
        try:
            page_text = self.page.locator("body").inner_text(timeout=3000)[:5000]
        except Exception:
            page_text = "(unable to read page text)"
        self.logger.exception("Task %s failed: %s; screenshot=%s", task_name, error, shot)
        self.logger.error("Visible page text snapshot: %s", page_text)

        recovery = None
        # Opt-in: recovery calls an LLM, so it stays off until the deterministic
        # flows are verified. Enable with {"recovery": {"enabled": true}}.
        if not submit_started and self.config.get("recovery", {}).get("enabled", False):
            try:
                guard = RecoveryGuard(self.page, self.config, self.logger)
                recovery = guard.recover(task_name, error, submit_started=False, shot=shot)
            except Exception as exc:
                self.logger.warning("恢复流程本身出错，按失败处理：%s", exc)

        if submit_started:
            status = "needs_review"
        elif recovery and recovery.get("last"):
            # Blocker was cleared by a whitelisted action; the caller may retry
            # the step once. Submission never happened, so no duplicate risk.
            status = "recovered"
        else:
            status = "failed"

        return {
            "status": status,
            "error": str(error),
            "screenshot": shot,
            "page_text": page_text,
            "recovery": recovery,
        }
