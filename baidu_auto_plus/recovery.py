"""Layered failure recovery for the Baidu automation flows.

The happy path stays fully deterministic. This module only runs when something
unexpected happens (stray popup, layout drift, rejected upload) and it is
deliberately pessimistic:

  Tier 0  deterministic popup sweep, no model call
  Tier 1  grounded element manifest, the model picks an existing ref (no pixels)
  Tier 2  vision model returns coordinates (last resort, clamped and logged)

Safety rules enforced here:
  * a model decision can only target an element that really exists on the page
  * irreversible buttons (submit/publish/delete/pay) are refused
  * the escalation budget is bounded, so a confused model cannot loop
  * recovery is never attempted once a submit may have happened; the caller must
    record needs_review instead, because retrying risks duplicate records
"""

import base64
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent

# Containers that Baidu's UI (vi / dls component libraries) uses for overlays.
DIALOG_SELECTORS = [
    '[role="dialog"]',
    '[role="alertdialog"]',
    ".vi-dialog",
    ".vi-modal",
    ".vi-drawer",
    ".dls-dialog",
    ".dls-modal",
    '[class*="dialog"]',
    '[class*="modal"]',
    '[class*="popup"]',
    '[class*="guide"]',
]

# Labels that are safe to click purely to make an overlay go away.
DISMISS_LABELS = [
    "知道了",
    "我知道了",
    "好的",
    "稍后再说",
    "以后再说",
    "跳过",
    "取消",
    "关闭",
    "不再提示",
    "下次再说",
]

# Clicking these could create/publish/destroy real business data. Recovery must
# never pick them, even if the model insists.
FORBIDDEN_WORDS = [
    "提交审核",
    "提交",
    "发布",
    "立即发布",
    "保存并发布",
    "删除",
    "清空",
    "支付",
    "确认支付",
    "立即开通",
]

MANIFEST_JS = """
([dialogSelectors, dismissLabels, forbiddenWords, limit]) => {
  // Refs must be rebuilt from scratch every pass; stale attributes would make
  // a later collect_manifest() skip real elements and shift every index.
  document.querySelectorAll('[data-recovery-ref]')
    .forEach((el) => el.removeAttribute('data-recovery-ref'));
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || s.opacity === '0') return false;
    return true;
  };
  const zOf = (el) => {
    let node = el, z = 0;
    while (node && node !== document.body) {
      const v = parseInt(getComputedStyle(node).zIndex || '0', 10);
      if (!isNaN(v) && v > z) z = v;
      node = node.parentElement;
    }
    return z;
  };

  // A node can match several selectors ([role=dialog] and [class*=dialog]), and
  // scopes can nest. Dedupe first: feeding duplicate/equal nodes to sort() with
  // an inconsistent comparator corrupts the ordering in V8 and silently drops
  // the real dialog out of the slice below.
  const matched = dialogSelectors
    .flatMap((sel) => Array.from(document.querySelectorAll(sel)))
    .filter(visible);
  const uniq = [];
  const seenScope = new Set();
  for (const el of matched) {
    if (seenScope.has(el)) continue;
    seenScope.add(el);
    uniq.push(el);
  }
  const outer = uniq.filter((el) => !uniq.some((o) => o !== el && o.contains(el)));
  const scopes = (outer.length ? outer : uniq)
    .slice()
    .sort((a, b) => zOf(b) - zOf(a))
    .slice(0, 5);
  if (!scopes.length) scopes.push(document.body);

  const clickable = 'a[href], button, input, textarea, select, [role="button"], '
    + '[role="checkbox"], [role="radio"], [role="tab"], [role="menuitem"], '
    + '[class*="close"], [class*="Close"], [aria-label]';

  const seen = new Set();
  const out = [];
  for (const scope of scopes) {
    for (const el of scope.querySelectorAll(clickable)) {
      if (!visible(el) || seen.has(el)) continue;
      const label = (el.getAttribute('aria-label')
        || el.getAttribute('title')
        || el.getAttribute('placeholder')
        || el.innerText || el.value || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
      const r = el.getBoundingClientRect();
      seen.add(el);
      const idx = out.length;
      el.setAttribute('data-recovery-ref', String(idx));
      out.push({
        index: idx,
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute('type') || '',
        label: label,
        cls: (el.className && el.className.toString().slice(0, 60)) || '',
        x: Math.round(r.left + r.width / 2),
        y: Math.round(r.top + r.height / 2),
        w: Math.round(r.width),
        h: Math.round(r.height),
        looksLikeDismiss: dismissLabels.some((d) => label.includes(d))
          || /close/i.test(el.className ? el.className.toString() : ''),
        forbidden: forbiddenWords.some((f) => label.includes(f)),
      });
      if (out.length >= limit) return out;
    }
  }
  return out;
}
"""


def _read_env_file(path):
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def load_llm_config(config):
    """Resolve LLM settings without ever hardcoding a key in source."""
    llm = dict(config.get("llm", {}))
    env_name = llm.get("api_key_env", "WANXIANG_API_KEY")
    dotenv = _read_env_file(PROJECT_ROOT / ".env")
    key = (
        os.environ.get(env_name)
        or os.environ.get("API_KEY")
        or dotenv.get(env_name)
        or dotenv.get("API_KEY")
    )
    llm["api_key"] = (key or "").strip()
    llm.setdefault("base_url", dotenv.get("BASE_URL", ""))
    llm.setdefault("model", "qwen3.8-max")
    llm.setdefault("vision_model", llm.get("model"))
    return llm


class RecoveryGuard:
    """Bounded, whitelist-only recovery driver."""

    def __init__(self, page, config, logger):
        self.page = page
        self.config = config
        self.logger = logger
        rec = config.get("recovery", {})
        self.enabled = rec.get("enabled", True)
        self.max_text_attempts = int(rec.get("max_text_attempts", 2))
        self.allow_vision = bool(rec.get("allow_vision", True))
        self.max_vision_attempts = int(rec.get("max_vision_attempts", 1))
        self._client = None
        self._llm = None

    # ------------------------------------------------------------------ tier 0

    def collect_manifest(self, limit=40):
        try:
            return self.page.evaluate(
                MANIFEST_JS,
                [DIALOG_SELECTORS, DISMISS_LABELS, FORBIDDEN_WORDS, limit],
            )
        except Exception as exc:
            self.logger.warning("采集页面元素清单失败：%s", exc)
            return []

    def sweep_popups(self, reason=""):
        """Tier 0: close overlays using only known-safe dismiss labels."""
        closed = []
        if not self.enabled:
            return closed
        for item in self.collect_manifest():
            if not item.get("looksLikeDismiss") or item.get("forbidden"):
                continue
            if not item.get("label") and "close" not in (item.get("cls") or "").lower():
                continue
            if self._click_ref(item["index"], why=f"tier0:{reason}"):
                closed.append(item.get("label") or item.get("cls"))
                self.page.wait_for_timeout(300)
        if closed:
            self.logger.info("Tier0 关闭弹窗 %s（%s）", closed, reason or "常规检查")
        return closed

    def _click_ref(self, index, why=""):
        try:
            loc = self.page.locator(f'[data-recovery-ref="{index}"]')
            if not loc.count() or not loc.first.is_visible():
                return False
            loc.first.click(timeout=3000)
            self.logger.info("恢复动作：点击 ref=%s（%s）", index, why)
            return True
        except Exception as exc:
            self.logger.warning("点击 ref=%s 失败：%s", index, exc)
            return False

    # ------------------------------------------------------------------ tiers 1/2

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError:
            self.logger.warning("未安装 openai，跳过 Tier1/Tier2 模型兜底")
            return None
        self._llm = load_llm_config(self.config)
        if not self._llm.get("api_key") or not self._llm.get("base_url"):
            self.logger.warning("缺少 API key 或 base_url，跳过 Tier1/Tier2 模型兜底")
            return None
        self._client = OpenAI(
            api_key=self._llm["api_key"],
            base_url=self._llm["base_url"],
            timeout=int(self._llm.get("timeout", 60)),
        )
        return self._client

    def _ask(self, prompt, image_path=None):
        client = self._get_client()
        if client is None:
            return None
        content = []
        if image_path:
            mime = "image/png"
            raw = base64.b64encode(Path(image_path).read_bytes()).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{raw}"}})
        content.append({"type": "text", "text": prompt})
        model = self._llm["vision_model"] if image_path else self._llm["model"]
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                response_format={"type": "json_object"},
                extra_body={"enable_thinking": False},
            )
            text = (resp.choices[0].message.content or "").strip()
            match = re.search(r"\{.*\}", text, re.S)
            return json.loads(match.group(0)) if match else None
        except Exception as exc:
            self.logger.warning("模型兜底调用失败：%s", exc)
            return None

    def _text_recovery(self, task_name, error, manifest):
        """Tier 1: model chooses an existing ref from the manifest."""
        listing = "\n".join(
            f'{i["index"]}. <{i["tag"]} type="{i["type"]}"> "{i["label"]}" '
            f'class="{i["cls"]}" box={i["w"]}x{i["h"]}'
            for i in manifest
        )
        prompt = f"""你在修复一个浏览器自动化流程的异常，任务：{task_name}
报错信息：{error}

下面是当前页面（优先弹窗容器内）真实存在的可交互元素清单，index 是稳定引用号：
{listing}

请判断页面上出现了什么意外情况（引导弹窗、公告、登录失效、上传被拒、表单校验报错等），
并选择一个**最保守**的动作让流程可以继续。

硬性规则：
- 只能从上面清单里选 index，禁止编造。
- 禁止选择提交/发布/删除/支付类按钮。
- 如果清单里没有安全的可点元素，或者你不确定，就选 abort。
- 优先选择关闭弹窗/取消/知道了这类动作。

只输出 JSON：
{{"situation": "一句话描述页面现状", "action": "click|abort", "index": 数字或null, "reason": "为什么安全"}}"""
        decision = self._ask(prompt)
        if not decision:
            return None
        self.logger.info("Tier1 判断：%s -> %s", decision.get("situation"), decision.get("reason"))
        return self._apply_decision(decision, manifest, tier="tier1")

    def _vision_recovery(self, task_name, error, shot):
        """Tier 2: vision model returns coordinates. Only for closing overlays."""
        if not shot or not self.allow_vision:
            return None
        size = self.page.viewport_size or {"width": 1440, "height": 1000}
        prompt = f"""任务：{task_name}
报错：{error}

这是一个网页截图，视口 CSS 尺寸为 {size['width']}x{size['height']}。
流程被一个意外弹层/遮罩挡住了，需要点掉它才能继续。

请找出**用于关闭这个弹层**的位置（右上角 X、知道了、取消、稍后再说、遮罩空白处）。
坐标必须是相对截图左上角的 CSS 像素，不要用设备像素。

硬性规则：
- 绝对不要点击提交、发布、保存、删除、支付类按钮。
- 如果画面里没有可安全关闭的弹层，或者你不确定，action 填 abort。

只输出 JSON：
{{"situation": "一句话描述", "action": "click|abort", "x": 数字或null, "y": 数字或null, "target": "你点的是什么", "reason": "为什么安全"}}"""
        decision = self._ask(prompt, image_path=shot)
        if not decision:
            return None
        self.logger.info(
            "Tier2 判断：%s target=%s reason=%s",
            decision.get("situation"), decision.get("target"), decision.get("reason"),
        )
        if decision.get("action") != "click":
            return None
        x, y = decision.get("x"), decision.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return None
        x = max(1, min(int(x), size["width"] - 1))
        y = max(1, min(int(y), size["height"] - 1))
        try:
            self.page.mouse.click(x, y)
            self.logger.info("Tier2 已点击 (%s, %s)", x, y)
            self.page.wait_for_timeout(600)
            return {"tier": "tier2", "action": "click", "x": x, "y": y,
                    "target": decision.get("target"), "reason": decision.get("reason")}
        except Exception as exc:
            self.logger.warning("Tier2 点击失败：%s", exc)
            return None

    def _apply_decision(self, decision, manifest, tier):
        if decision.get("action") != "click":
            return None
        index = decision.get("index")
        if not isinstance(index, int):
            return None
        item = next((i for i in manifest if i["index"] == index), None)
        if item is None:
            self.logger.warning("%s 返回了清单外的 index=%s，已拒绝", tier, index)
            return None
        label = item.get("label") or ""
        if item.get("forbidden") or any(w in label for w in FORBIDDEN_WORDS):
            self.logger.warning("%s 想点危险按钮「%s」，已拒绝", tier, label)
            return None
        if not self._click_ref(index, why=f"{tier}:{label}"):
            return None
        self.page.wait_for_timeout(600)
        return {"tier": tier, "action": "click", "index": index,
                "label": label, "reason": decision.get("reason")}

    # ------------------------------------------------------------------ entry

    def recover(self, task_name, error, submit_started=False, shot=None):
        """Escalate tier 0 -> 1 -> 2. Returns a decision dict or None."""
        if submit_started:
            self.logger.warning("提交可能已发生，禁止自动恢复，转人工确认：%s", task_name)
            return None
        if not self.enabled:
            return None

        self.sweep_popups(reason=f"{task_name}:异常前清扫")
        attempts = []
        for _ in range(self.max_text_attempts):
            manifest = self.collect_manifest()
            decision = self._text_recovery(task_name, error, manifest)
            if decision:
                attempts.append(decision)
                return {"attempts": attempts, "last": decision}
        if self.allow_vision:
            for _ in range(self.max_vision_attempts):
                decision = self._vision_recovery(task_name, error, shot)
                if decision:
                    attempts.append(decision)
                    return {"attempts": attempts, "last": decision}
        return {"attempts": attempts, "last": None} if attempts else None


def is_dangerous_label(label):
    return any(word in (label or "") for word in FORBIDDEN_WORDS)
