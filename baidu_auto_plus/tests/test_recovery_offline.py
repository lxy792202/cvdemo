import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright
from common import count_thumbnails, find_upload_error, wait_upload_confirmed, THUMBNAIL_JS
from recovery import RecoveryGuard
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("t")

FIXTURE = """
<html><body>
  <div class="page-body">
    <button id="newprod">新建产品</button>
    <div class="picture-container">
      <div class="common-image-uploader"><img src="https://x/a.jpg" width="80" height="80"></div>
    </div>
  </div>
  <div class="vi-dialog" role="dialog" style="z-index:1000">
    <button class="vi-dialog-close" aria-label="关闭"></button>
    <button>本地上传</button>
    <div class="preview"><div style="width:100px;height:100px;background-image:url('blob:https://x/1')"></div></div>
    <button>取消</button>
    <button>确定</button>
    <button>提交审核</button>
  </div>
  <div class="vi-guide" role="dialog" style="z-index:900"><button>知道了</button></div>
</body></html>
"""

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1440, "height": 1000})
    page.set_content(FIXTURE)

    guard = RecoveryGuard(page, {"recovery": {"enabled": True}}, log)

    n0 = count_thumbnails(page)
    print("thumbnails initial (expect 2: 1 img + 1 bg):", n0)
    assert n0 == 2, n0

    print("upload error (expect empty):", repr(find_upload_error(page)))
    assert find_upload_error(page) == ""

    man = guard.collect_manifest()
    labels = [(m["index"], m["label"], m["forbidden"], m["looksLikeDismiss"]) for m in man]
    print("manifest:")
    for row in labels:
        print("   ", row)
    forb = [m["label"] for m in man if m["forbidden"]]
    assert "提交审核" in forb, forb
    assert not any(m["forbidden"] for m in man if m["label"] in ("知道了", "取消", "确定")), "safe buttons misflagged"

    dismiss = [m["label"] or m["cls"] for m in man if m["looksLikeDismiss"] and not m["forbidden"]]
    print("dismiss candidates:", dismiss)
    assert any("知道了" in (d or "") for d in dismiss)

    # apply_decision must refuse a forbidden index
    submit_idx = next(m["index"] for m in man if m["label"] == "提交审核")
    res = guard._apply_decision({"action": "click", "index": submit_idx, "reason": "t"}, man, "test")
    print("forbidden click result (expect None):", res)
    assert res is None

    # and refuse an index outside the manifest
    res2 = guard._apply_decision({"action": "click", "index": 9999, "reason": "t"}, man, "test")
    print("out-of-manifest result (expect None):", res2)
    assert res2 is None

    # error toast detection + wait_upload_confirmed failure path
    page.evaluate("""() => {
      const t = document.createElement('div');
      t.className = 'vi-toast'; t.textContent = '图片大小不能超过 5M，请重新上传';
      document.body.appendChild(t);
    }""")
    err = find_upload_error(page)
    print("upload error (expect matched):", err)
    assert err

    try:
        wait_upload_confirmed(page, before=0, timeout=1500)
        raise SystemExit("FAIL: should have raised on rejected upload")
    except RuntimeError as e:
        print("correctly raised:", str(e)[:60])

    # success path: thumbnail count grows
    page.evaluate("() => document.querySelector('.vi-toast').remove()")
    page.evaluate("""() => {
      const i = document.createElement('img');
      i.src = 'https://x/new.jpg'; i.width = 90; i.height = 90;
      document.querySelector('.preview').appendChild(i);
    }""")
    got = wait_upload_confirmed(page, before=n0, timeout=2000)
    print("upload confirmed, count now:", got)
    assert got > n0

    # indices must stay stable across repeated collections (ref attribute reset)
    man2 = guard.collect_manifest()
    assert len(man2) == len(man), f"manifest shrank on re-collect: {len(man)} -> {len(man2)}"
    assert [m["label"] for m in man2] == [m["label"] for m in man], "index drift on re-collect"
    print("re-collect stable, same", len(man2), "entries")

    # happy-path sweeper: only purely informational labels, never 取消/提交审核
    from common import safe_close_known_popups
    page2 = b.new_page(viewport={"width": 1440, "height": 1000})
    page2.set_content(FIXTURE)
    safe_close_known_popups(page2, log)
    assert page2.locator('.vi-dialog button:has-text("取消")').count() == 1, "happy path clicked 取消!"
    assert page2.locator('button:has-text("提交审核")').count() == 1, "happy path clicked 提交审核!"
    print("happy-path sweep left 取消/提交审核 untouched")
    page2.close()

    # failure-path sweeper may dismiss overlays but must never touch 提交审核
    closed = guard.sweep_popups("test")
    print("recovery sweep closed:", closed)
    assert page.locator('button:has-text("提交审核")').count() == 1, "recovery clicked 提交审核!"
    print("recovery sweep never touched 提交审核")
    print("\nALL OFFLINE CHECKS PASSED")
    b.close()
