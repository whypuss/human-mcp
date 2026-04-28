"""
post_ig_human.py — Instagram 擬人發文流程（Playwright 獨立 Chromium Profile）

用 launch_persistent_context 啟動獨立 Chromium，不影響用戶正常 Chrome。
第一次需手動登入，之後 profile 自動記住。

流程：
1. 啟動 Chromium → 導航到 IG 首頁
2. 點新貼文（+）按鈕
3. 等「建立新帖子」dialog → 攔截 OS file chooser
4. 等 3s（IG 處理圖片）
5. 右上角「下一步」(裁切頁)
6. 右上角「下一步」(濾鏡頁)
7. Caption 頁輸入文字
8. 右上角「分享」
9. 等「已共享」→「完成」

按鈕全部用 [aria-label] 定位，隨機延遲模拟人類操作。
"""

import asyncio
import base64
import logging
import os
import random
from pathlib import Path
from playwright.async_api import async_playwright

log = logging.getLogger(__name__)

_random = lambda a, b: random.uniform(a, b)

IG_PROFILE = Path("/tmp/ig-chromium-profile")
IG_PROFILE.mkdir(parents=True, exist_ok=True)


# ── Dialog 按鈕點擊（用 aria-label）───────────────────────────────────────────

async def _click_btn_in_dialog(page, target: str, timeout: float = 10) -> bool:
    _target = target  # noqa: F841 used in eval

    for _ in range(int(timeout * 5)):
        # 策略0: Playwright locator + has-text
        try:
            locator = page.locator(f'[role="dialog"] :text-is("{target}")').first
            count = await locator.count()
            if count == 0:
                locator = page.locator(f'[role="dialog"] :text("{target}")').first
                count = await locator.count()
            if count > 0:
                await locator.click(timeout=5000)
                log.debug(f"[dialog] clicked '{target}' (Playwright .click())")
                return True
        except Exception as e:
            log.debug(f"[dialog] playwright '{target}': {e}")

        # 策略1: aria-label
        try:
            r = await page.evaluate(f"""
            () => {{
                var dialog = document.querySelector('[role="dialog"]');
                if (!dialog) return 'no_dialog';
                var targets = dialog.querySelectorAll('[aria-label="{target}"]');
                if (!targets.length) return 'not_aria';
                var btn = targets[0];
                var rect = btn.getBoundingClientRect();
                if (!rect || rect.width === 0 || rect.height === 0) return 'hidden';
                var cx = rect.left + rect.width / 2;
                var cy = rect.top + rect.height / 2;
                var opts = {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy,
                             isPrimary: true, pointerId: 1, view: window }};
                btn.dispatchEvent(new MouseEvent('mousedown', opts));
                btn.dispatchEvent(new MouseEvent('mouseup', opts));
                btn.dispatchEvent(new MouseEvent('click', opts));
                return 'aria_ok';
            }}
            """)
            if r == "aria_ok":
                log.debug(f"[dialog] clicked '{target}' (aria-label)")
                return True
        except Exception as e:
            log.debug(f"[dialog] aria try '{target}': {e}")

        # 策略2: textContent 包含 target
        try:
            r2 = await page.evaluate(f"""
            () => {{
                var dialog = document.querySelector('[role="dialog"]');
                if (!dialog) return 'no_dialog';
                var btns = dialog.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {{
                    var tc = (btns[i].textContent || '').trim();
                    if (tc === '{target}' || tc.includes('{target}')) {{
                        var rect = btns[i].getBoundingClientRect();
                        if (!rect || rect.width === 0 || rect.height === 0) continue;
                        var cx = rect.left + rect.width / 2;
                        var cy = rect.top + rect.height / 2;
                        var opts = {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy,
                                     isPrimary: true, pointerId: 1, view: window }};
                        btns[i].dispatchEvent(new MouseEvent('mousedown', opts));
                        btns[i].dispatchEvent(new MouseEvent('mouseup', opts));
                        btns[i].dispatchEvent(new MouseEvent('click', opts));
                        return 'tc_ok:' + tc.slice(0, 20);
                    }}
                }}
                return 'not_tc';
            }}
            """)
            if r2.startswith("tc_ok"):
                log.debug(f"[dialog] clicked '{target}' (textContent): {r2}")
                return True
        except Exception as e:
            log.debug(f"[dialog] tc try '{target}': {e}")

        # 策略3: Playwright 原生 click
        try:
            btn = page.locator(f'[role="dialog"] button:has-text("{target}")').first
            if await btn.count() > 0:
                await btn.click(timeout=3000, force=True)
                log.debug(f"[dialog] clicked '{target}' (Playwright)")
                return True
        except Exception as e:
            log.debug(f"[dialog] playwright try '{target}': {e}")

        await asyncio.sleep(0.3)
    log.warning(f"[dialog] could not click '{target}'")
    return False


async def _wait_dialog_contains(page, keyword: str, timeout: float = 20) -> bool:
    for _ in range(int(timeout * 5)):
        try:
            dt = await page.evaluate(
                "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                "return d ? d.innerText.slice(0, 300) : ''; }"
            )
            if keyword in dt:
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    return False


# ── 等 IG 首頁 ready ───────────────────────────────────────────────────────

async def _wait_ig_feed(page, timeout: float = 10) -> bool:
    for _ in range(int(timeout * 5)):
        try:
            count = await page.evaluate(
                "() => document.querySelectorAll('article').length"
            )
            if count >= 1:
                log.debug(f"[feed] ready, {count} articles found")
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    return False


# ── 登入等待 ────────────────────────────────────────────────────────────────

async def _ensure_ig_logged_in(page) -> bool:
    """等待 IG 頁面加載完成（檢查是否在登入頁）。"""
    for attempt in range(30):
        await asyncio.sleep(1)
        try:
            url = page.url.lower()
            if "instagram.com" in url:
                if "/accounts/login" in url or "/accounts/login/" in url:
                    log.debug(f"[_ensure] 等待登入... {attempt+1}/30")
                    continue
                log.debug(f"[_ensure] 已就緒: {url[:60]}")
                return True
        except Exception:
            pass
    return False


# ── 主流程 ───────────────────────────────────────────────────────────────────

async def post_ig_human(caption: str, image_path: str) -> str:
    """擬人化 IG 發文。返回 '✅ ...' 或 '❌ ...'。"""
    if not os.path.exists(image_path):
        return f"❌ 圖片不存在: {image_path}"
    file_size = os.path.getsize(image_path)
    if file_size < 1024:
        return f"❌ 圖片太小（{file_size} bytes），IG 要求 > 1KB"

    async with async_playwright() as p:
        # 啟動獨立 Chromium
        ctx = await p.chromium.launch_persistent_context(
            str(IG_PROFILE),
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
        await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30_000)
        if not await _ensure_ig_logged_in(page):
            await ctx.close()
            return "❌ IG 登入超時，請在 Chromium 視窗中完成登入後重試"
        await asyncio.sleep(_random(1.5, 2.5))

        # 如果有殘留 dialog，關掉
        try:
            dt = await page.evaluate(
                "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                "return d ? d.innerText.slice(0, 100) : ''; }"
            )
            if dt:
                log.debug(f"Closing residual dialog: {repr(dt[:50])}")
                await page.keyboard.press("Escape")
                await asyncio.sleep(1.2)
        except Exception:
            pass

        # ── Step 1: 點「新貼文」按鈕 ─────────────────────────────────────────
        await asyncio.sleep(_random(0.5, 1.0))

        for attempt in range(3):
            try:
                await page.evaluate("""
                () => {
                    var s = document.querySelector('svg[aria-label="新貼文"]');
                    if (s && s.parentElement && s.parentElement.tagName === 'A') {
                        s.parentElement.click();
                    } else if (s && s.parentElement) {
                        s.parentElement.click();
                    }
                }
                """)
                log.debug(f"[step1] 新貼文 clicked (attempt {attempt + 1})")
                await asyncio.sleep(_random(2.0, 2.5))
                break
            except Exception as e:
                log.warning(f"[step1] attempt {attempt + 1} failed: {e}")
                if attempt == 2:
                    await ctx.close()
                    return f"❌ 點新貼文失敗: {e}"
                await asyncio.sleep(2)

        # ── Step 2: 等「建立新帖子」dialog，注入圖片 ──────────────────────────
        if not await _wait_dialog_contains(page, "從電腦選擇", timeout=15):
            await asyncio.sleep(2)
            dt = await page.evaluate(
                "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                "return d ? d.innerText.slice(0, 100) : ''; }"
            )
            if "從電腦選擇" not in dt:
                await ctx.close()
                return "❌ 建立新帖子 dialog 未出現"

        log.debug("[step2] Waiting for file chooser...")
        await asyncio.sleep(_random(0.5, 1.0))

        # 方案：優先攔截 OS file_chooser，失敗則用 JS DataTransfer
        file_injected = False
        try:
            fc = await ctx.wait_for_file_chooser(timeout=3000)
            await fc.set_files(image_path, timeout=20_000)
            log.debug(f"[step2] File set via file_chooser: {image_path}")
            file_injected = True
        except Exception as fc_err:
            # OS file chooser 沒出現，用 JS DataTransfer 直接注入
            log.warning(f"[step2] file_chooser not intercepted ({fc_err}), using JS DataTransfer")
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            try:
                result = await page.evaluate("""(b64) => {
                    try {
                        const binaryString = atob(b64);
                        const bytes = new Uint8Array(binaryString.length);
                        for (let i = 0; i < binaryString.length; i++) {
                            bytes[i] = binaryString.charCodeAt(i);
                        }
                        const blob = new Blob([bytes], { type: 'image/jpeg' });
                        const file = new File([blob], 'upload.jpg', { type: 'image/jpeg', lastModified: Date.now() });

                        const d = document.querySelector('[role="dialog"]');
                        if (!d) return 'no_dialog';
                        const inputs = d.querySelectorAll('input[type=file]');
                        if (!inputs.length) return 'no_file_input';

                        const inp = inputs[0];
                        const dt = new DataTransfer();
                        dt.items.add(file);
                        Object.defineProperty(inp, 'files', {
                            value: dt.files,
                            writable: true,
                            configurable: true
                        });
                        const tracker = inp._valueTracker;
                        if (tracker) tracker.setValue('');
                        inp.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                        return { ok: true, files: dt.files.length };
                    } catch(e) {
                        return { error: e.message };
                    }
                }""", b64)
                log.debug(f"[step2] JS DataTransfer result: {result}")
                if not result.get("ok"):
                    raise Exception(f"JS inject failed: {result}")
                file_injected = True
            except Exception as inject_err:
                log.error(f"[step2] JS DataTransfer failed: {inject_err}")
                await page.keyboard.press("Escape")
                raise Exception(f"Image upload failed: {inject_err}")

        if not file_injected:
            raise Exception("Image injection failed (no method available)")

        # IG 處理圖片
        await asyncio.sleep(_random(3.0, 3.5))
        log.debug("[step2] Image processing...")

        # ── Step 3: 右上角「下一步」(裁切/調整) ──────────────────────────────
        if not await _click_btn_in_dialog(page, "下一步", timeout=8):
            await ctx.close()
            return "❌ 裁切頁「下一步」找不到"
        log.debug("[step3] Crop page → 下一步 clicked")
        await asyncio.sleep(_random(1.5, 2.0))

        # ── Step 4: 右上角「下一步」(濾鏡) ─────────────────────────────────
        if not await _click_btn_in_dialog(page, "下一步", timeout=8):
            await ctx.close()
            return "❌ 濾鏡頁「下一步」找不到"
        log.debug("[step4] Filter page → 下一步 clicked")
        await asyncio.sleep(_random(2.0, 2.5))

        # ── Step 5: Caption 頁 ───────────────────────────────────────────────
        caption_found = False
        for _ in range(30):
            dt = await page.evaluate(
                "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                "return d ? d.innerText.slice(0, 300) : ''; }"
            )
            if "說明文字" in dt or ("分享" in dt and len(dt) > 10):
                caption_found = True
                log.debug(f"[step5] Caption page detected: {repr(dt[:80])}")
                break
            if "裁切" in dt and _ > 3:
                log.debug(f"[step5] Still on crop page, retrying 下一步...")
                await _click_btn_in_dialog(page, "下一步", timeout=5)
                await asyncio.sleep(1.5)
            await asyncio.sleep(0.5)

        if not caption_found:
            await ctx.close()
            try:
                dt = await page.evaluate(
                    "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                    "return d ? d.innerText.slice(0, 200) : ''; }"
                )
            except Exception:
                dt = ""
            return f"❌ 輸入說明文字頁未出現: {repr(dt[:80])}"

        # 找 caption textbox
        for _ in range(10):
            try:
                boxes = page.locator('[role="dialog"] [role="textbox"]')
                if await boxes.count() > 0:
                    await boxes.first.click(timeout=2000, force=True)
                    log.debug("[step5] Caption textbox clicked")
                    await asyncio.sleep(_random(0.3, 0.5))
                    break
            except Exception as e:
                log.debug(f"[step5] textbox attempt err: {e}")
            await asyncio.sleep(_random(0.3, 0.5))
        else:
            await ctx.close()
            return "❌ 找不到 caption textbox"

        # 輸入 caption
        textbox = page.locator('[role="dialog"] [role="textbox"]').first
        await textbox.fill(caption)
        log.debug(f"[step5] Caption filled: {len(caption)} chars")
        await asyncio.sleep(_random(1.0, 1.5))

        # 模擬人類打字後的光標操作
        await page.keyboard.press("ArrowRight")
        await asyncio.sleep(_random(0.3, 0.5))

        # ── Step 6: 右上角「分享」────────────────────────────────────────────
        if not await _click_btn_in_dialog(page, "分享", timeout=8):
            await ctx.close()
            return "❌ 找不到「分享」按鈕"
        log.debug("[step6] 分享 clicked")
        await asyncio.sleep(_random(1.0, 1.5))

        # ── Step 7: 等「已分享」→「完成」────────────────────────────────────
        for i in range(50):
            await asyncio.sleep(1)
            try:
                dt = await page.evaluate(
                    "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                    "return d ? d.innerText.slice(0, 200) : ''; }"
                )
                if "已分享" in dt:
                    log.debug(f"[step7] ✅ 已分享（{i + 1}s）")
                    break
                if "發生錯誤" in dt or "錯誤" in dt:
                    await ctx.close()
                    return f"❌ IG 發文錯誤: {repr(dt[:80])}"
            except Exception:
                pass
        else:
            await ctx.close()
            try:
                dt = await page.evaluate(
                    "() => (document.querySelector('[role=\"dialog\"]')||{}).innerText||''"
                )
                return f"❌ 分享超時，dialog: {repr(dt[:80])}"
            except Exception:
                return "❌ 分享超時"

        await asyncio.sleep(_random(0.5, 1.0))

        # 點「完成」或按 Escape
        found_done = await _click_btn_in_dialog(page, "完成", timeout=5)
        if not found_done:
            log.debug("[step7] 完成 not found, pressing Escape")
            await page.keyboard.press("Escape")
        else:
            log.debug("[step7] 完成 clicked")

        await asyncio.sleep(1)
        await ctx.close()
        return "✅ Instagram 發文成功"


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    if len(sys.argv) < 3:
        print("用法: python post_ig_human.py <caption> <image_path>")
        sys.exit(1)

    result = asyncio.run(post_ig_human(sys.argv[1], sys.argv[2]))
    print(result)
