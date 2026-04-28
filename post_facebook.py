"""
post_facebook.py — Facebook 圖文發文（Playwright 獨立 Chromium Profile）

用 launch_persistent_context 啟動獨立 Chromium，不影響用戶正常 Chrome。
第一次需手動登入，之後 profile 自動記住。

流程：
1. 啟動 Chromium（獨立 profile）
2. 導航到 Facebook 主頁
3. 點「在想什麼」composer
4. 有圖片：DataTransfer API 注入
5. execCommand("insertText") 輸入文字
6. 點「下一頁」→「發佈」
7. 等 dialog 消失
"""

import asyncio
import base64
import logging
import os
import random
from pathlib import Path
from playwright.async_api import async_playwright

log = logging.getLogger("post_facebook")
_random_delay = lambda a, b: asyncio.sleep(random.uniform(a, b))

# 獨立 Chromium Profile 目錄（不改變用戶正常 Chrome）
FB_PROFILE = Path("/tmp/fb-chromium-profile")
FB_PROFILE.mkdir(parents=True, exist_ok=True)


async def _click_btn_by_text(page, text: str, timeout: float = 10):
    """JS click 按鈕（含 fallback 包含匹配）。"""
    script = f"""
    () => {{
        var btns = document.querySelectorAll('[role="button"], button');
        for (var i = 0; i < btns.length; i++) {{
            var t = (btns[i].innerText || '').trim();
            if (t === '{text}') {{ btns[i].click(); return 'clicked:' + t; }}
        }}
        for (var i = 0; i < btns.length; i++) {{
            var t = (btns[i].innerText || '').trim();
            if (t.indexOf('{text}') >= 0) {{ btns[i].click(); return 'clicked:' + t; }}
        }}
        return 'not_found';
    }}
    """
    for _ in range(int(timeout * 5)):
        try:
            r = await page.evaluate(script)
            if r != "not_found":
                log.debug(f"JS click [{text}]: {r}")
                return r
        except Exception as e:
            log.debug(f"click [{text}] evaluate err: {e}")
        await asyncio.sleep(0.2)
    return "not_found"


async def _wait_dialog_contains(page, keywords: list, timeout: float = 30) -> bool:
    for _ in range(int(timeout * 5)):
        try:
            dt = await page.evaluate(
                "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                "return d ? d.innerText.slice(0, 300) : ''; }"
            )
            if dt and any(k in dt for k in keywords):
                log.debug(f"Dialog ready: {[k for k in keywords if k in dt]}")
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    return False


async def _ensure_fb_logged_in(page) -> bool:
    """檢查是否在登入頁，若是則等待手動登入。"""
    for attempt in range(60):  # 最多等 60 秒
        await asyncio.sleep(1)
        try:
            url = page.url.lower()
            if "/login" not in url and "facebook.com" in url:
                log.debug(f"[_ensure] 已登入: {url[:60]}")
                return True
            log.debug(f"[_ensure] 等待登入... {attempt+1}/60")
        except Exception:
            pass
    return False


async def post_facebook(message: str, image_path: str = None) -> str:
    """
    發布 Facebook 圖文帖子。

    Args:
        message: 帖子文字內容
        image_path: 本地圖片路徑（可選）

    Returns:
        "✅ Facebook 發文成功" 或 "❌ 錯誤描述"
    """
    # ── 參數驗證 ──────────────────────────────────────────────────────────
    if image_path and not os.path.exists(image_path):
        return f"❌ 圖片不存在: {image_path}"
    file_size = os.path.getsize(image_path) if image_path else 0
    if image_path and file_size < 1024:
        return f"❌ 圖片太小（{file_size} bytes），要求 > 1KB"

    async with async_playwright() as p:
        # 啟動獨立 Chromium（用戶正常 Chrome 不受影響）
        ctx = await p.chromium.launch_persistent_context(
            str(FB_PROFILE),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # ── Step 0: 確保已登入 ──────────────────────────────────────────────
        await page.goto("https://www.facebook.com/", timeout=30_000)
        if not await _ensure_fb_logged_in(page):
            await ctx.close()
            return "❌ 登入超時，請手動在 Chromium 視窗中完成登入後重試"
        log.debug(f"[fb] 已就緒: {page.url[:60]}")

        # ── Step 1: 點擊「在想什麼」composer ────────────────────────────────
        existing = await page.evaluate(
            "() => { var d = document.querySelector('[role=\"dialog\"]'); "
            "return d ? d.innerText.slice(0, 100) : ''; }"
        )

        if not existing:
            for attempt in range(3):
                try:
                    r = await page.evaluate("""() => {
                        var btn = document.querySelector('[role="button"]');
                        if (btn && btn.innerText.includes('想')) { btn.click(); return 'clicked'; }
                        return 'not_found';
                    }""")
                    log.debug(f"[step1] composer clicked: {r}")
                    await asyncio.sleep(2)
                    if await _wait_dialog_contains(
                        page, ["粉絲專頁", "限時動態", "發佈", "相片", "影片"], timeout=5
                    ):
                        break
                    log.debug(f"[step1] composer dialog 未出現，重試 {attempt+1}/3")
                except Exception as e:
                    log.debug(f"[step1] 点 composer attempt {attempt+1}: {e}")
                    if attempt == 2:
                        await ctx.close()
                        return f"❌ 點 composer 失敗: {e}"
                await asyncio.sleep(2)

        # ── Step 2: 相片/影片（DataTransfer API 注入）───────────────────────
        if image_path:
            with open(image_path, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode()

            inject_r = await page.evaluate("""(b64) => {
                try {
                    const binaryString = atob(b64);
                    const bytes = new Uint8Array(binaryString.length);
                    for (let i = 0; i < binaryString.length; i++) {
                        bytes[i] = binaryString.charCodeAt(i);
                    }
                    const blob = new Blob([bytes], { type: 'image/jpeg' });
                    const file = new File([blob], 'upload.jpg', { type: 'image/jpeg', lastModified: Date.now() });

                    const inputs = document.querySelectorAll('input[type=file]');
                    for (let i = 0; i < inputs.length; i++) {
                        const inp = inputs[i];
                        const dt = new DataTransfer();
                        dt.items.add(file);
                        Object.defineProperty(inp, 'files', {
                            value: dt.files,
                            writable: true,
                            configurable: true
                        });
                        const tracker = inp._valueTracker;
                        if (tracker) { tracker.setValue(''); }
                        inp.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                    }
                    return { ok: true, inputsUpdated: inputs.length };
                } catch(e) {
                    return { error: e.message };
                }
            }""", b64_data)

            if not inject_r or not inject_r.get("ok"):
                await ctx.close()
                return f"❌ 圖片注入失敗: {inject_r}"

            log.debug(f"[step2] Image injected: {image_path} ({file_size} bytes)")

            # 等 FB 處理圖片
            for _ in range(10):
                await asyncio.sleep(1)
                preview = await page.evaluate("""() => {
                    var d = document.querySelector('[role=dialog]');
                    if (!d) return null;
                    var imgs = d.querySelectorAll('img[src]');
                    for (var img of imgs) {
                        var src = img.src || '';
                        if (src.startsWith('blob:')) return src.slice(0, 80);
                    }
                    return null;
                }""")
                if preview:
                    log.debug(f"[step2] 圖片預覽出現: {preview}")
                    break
            else:
                log.debug("⚠️ 圖片預覽未出現（可能上傳失敗）")
        else:
            log.debug("[step2] Skip (no image)")

        # ── Step 3: 打字 ───────────────────────────────────────────────────
        for _ in range(5):
            try:
                r = await page.evaluate(f"""
                () => {{
                    var d = document.querySelector('[role=\"dialog\"]');
                    if (!d) return "no_dialog";
                    var e = d.querySelector('[contenteditable=\"true\"]');
                    if (!e) return "no_editor";
                    e.focus();
                    document.execCommand("insertText", false, {repr(message)});
                    return "done";
                }}
                """)
                if r == "done":
                    log.debug(f"[step3] Text inserted ({len(message)} chars)")
                    break
                log.debug(f"[step3] 打字重試 {_}/5: {r}")
            except Exception as e:
                log.debug(f"[step3] 打字 err: {e}")
            await asyncio.sleep(1)
        else:
            await ctx.close()
            return "❌ 無法輸入文字"

        await _random_delay(0.5, 1.0)

        # ── Step 4: 下一頁 ─────────────────────────────────────────────────
        r = await _click_btn_by_text(page, "下一頁")
        if r == "not_found":
            try:
                await page.locator('[aria-label="下一頁"]').click(timeout=5000)
                log.debug("[step4] 下一頁 clicked (aria-label)")
            except Exception:
                await ctx.close()
                return "❌ 找不到「下一頁」按鈕"
        log.debug("[step4] 下一頁 clicked")
        await asyncio.sleep(3)

        # ── Step 5: 發佈 ───────────────────────────────────────────────────
        r = await _click_btn_by_text(page, "發佈")
        if r == "not_found":
            try:
                await page.locator('[aria-label="發佈"]').click(timeout=5000)
                log.debug("[step5] 發佈 clicked (aria-label)")
            except Exception:
                await ctx.close()
                return "❌ 找不到「發佈」按鈕"
        log.debug("[step5] 發佈 clicked")

        # 等發佈完成（dialog 消失）
        for i in range(20):
            await asyncio.sleep(1)
            try:
                still_open = await page.evaluate(
                    "() => !!document.querySelector('[role=\"dialog\"]')"
                )
                if not still_open:
                    log.debug(f"[step6] ✅ Dialog closed（{i+1}s）")
                    break
            except Exception:
                pass
        else:
            await ctx.close()
            return "❌ 發佈超時"

        await ctx.close()
        return "✅ Facebook 發文成功"


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    if len(sys.argv) < 2:
        print("用法: python post_facebook.py <message> [image_path]")
        sys.exit(1)

    msg = sys.argv[1]
    img = sys.argv[2] if len(sys.argv) > 2 else None

    result = asyncio.run(post_facebook(msg, img))
    print(result)
