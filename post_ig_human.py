"""
post_ig_human.py — Instagram 擬人發文流程（語義點擊 + 自愈架構）

與舊版差異：
- 不再依賴固定座標或 CDP
- 改用 Playwright getByRole/getByText DOM 定位
- 找不到時降級到 JS dispatchEvent（React 兼容性）
- 點擊後自愈驗證：結果不符預期自動重試

流程：
1. 啟動 Chromium（獨立 profile）
2. 導航到 IG 首頁
3. 點新貼文（+）按鈕
4. 等「建立新帖子」dialog
5. 注入圖片
6. 等 3s（IG 處理圖片）
7. 裁切頁「下一步」
8. 濾鏡頁「下一步」
9. Caption 頁輸入文字
10. 「分享」
11. 等「已分享」→「完成」
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


# ─────────────────────────────────────────────────────────────────────────────
# 語義按鈕點擊（DOM + 自愈）
# ─────────────────────────────────────────────────────────────────────────────

class SemanticBtn:
    """
    語義按鈕點擊器：
    1. Playwright getByRole（DOM 定位，最靠譜）
    2. JS dispatchEvent（React compatibility）
    3. 視覺 fallback（预留接口）
    """

    def __init__(self, page, dialog: bool = True):
        self.page = page
        self.dialog = dialog

    def _container(self):
        return self.page.locator('[role="dialog"]') if self.dialog else self.page

    async def _find_btn(self, label: str):
        """DOM 定位按鈕：getByRole → locator has-text → JS 遍歷"""
        c = self._container()

        # 策略1: getByRole（最靠譜，UI 變了只要 role+name 還在就能找到）
        for exact in [True, False]:
            try:
                loc = c.get_by_role("button", name=label, exact=exact)
                if await loc.count() > 0 and await loc.first.is_visible(timeout=1000):
                    return loc.first
            except Exception:
                pass

        # 策略2: locator has-text
        try:
            loc = c.locator(f"button:has-text('{label}')").first
            if await loc.count() > 0 and await loc.is_visible(timeout=1000):
                return loc
        except Exception:
            pass

        # 策略3: JS 遍歷（React aria-label 或 textContent）
        try:
            r = await self.page.evaluate(f"""
            () => {{
                var container = document.querySelector('[role="dialog"]');
                if (!container) return 'no_dialog';
                var btns = container.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {{
                    var b = btns[i];
                    var tc = (b.textContent || '').trim();
                    var aria = b.getAttribute('aria-label') || '';
                    if (tc === '{label}' || tc.includes('{label}') ||
                        aria === '{label}' || aria.includes('{label}')) {{
                        var rect = b.getBoundingClientRect();
                        if (!rect || rect.width === 0 || rect.height === 0) continue;
                        return 'found:' + tc.slice(0, 20) + ':' + b.getAttribute('aria-label') || '';
                    }}
                }}
                return 'not_found';
            }}
            """)
            if r.startswith("found:"):
                log.debug(f"[_find_btn] JS found: {r}")
                return r
        except Exception as e:
            log.debug(f"[_find_btn] JS search failed: {e}")

        return None

    async def click(self, label: str, timeout: float = 10, max_retries: int = 3) -> bool:
        """
        語義點擊按鈕（帶自癒重試）。

        點擊後如果畫面沒變（按鈕仍在），自動重試。
        """
        for attempt in range(1, max_retries + 1):
            log.debug(f"[_btn] clicking '{label}' (attempt {attempt}/{max_retries})")

            btn = await self._find_btn(label)

            if btn is None:
                log.warning(f"[_btn] '{label}' not found in DOM")
                await asyncio.sleep(1.0)
                continue

            # 如果是 JS 返回的字符串（坐標 JS click）
            if isinstance(btn, str) and btn.startswith("found:"):
                try:
                    await self.page.evaluate(f"""
                    () => {{
                        var container = document.querySelector('[role="dialog"]');
                        if (!container) return;
                        var btns = container.querySelectorAll('button');
                        for (var i = 0; i < btns.length; i++) {{
                            var b = btns[i];
                            var tc = (b.textContent || '').trim();
                            var aria = b.getAttribute('aria-label') || '';
                            if (tc === '{label}' || tc.includes('{label}') ||
                                aria === '{label}' || aria.includes('{label}')) {{
                                var rect = b.getBoundingClientRect();
                                var cx = rect.left + rect.width / 2;
                                var cy = rect.top + rect.height / 2;
                                var opts = {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy,
                                             isPrimary: true, pointerId: 1, view: window }};
                                b.dispatchEvent(new MouseEvent('mousedown', opts));
                                b.dispatchEvent(new MouseEvent('mouseup', opts));
                                b.dispatchEvent(new MouseEvent('click', opts));
                                return;
                            }}
                        }}
                    }}
                    """)
                    await asyncio.sleep(1.5)
                    # 驗證：按鈕是否消失（dialog 進入下一頁）
                    still_there = await self._find_btn(label)
                    if still_there and attempt < max_retries:
                        log.warning(f"[_btn] '{label}' still visible after click, retrying...")
                        await asyncio.sleep(1.0)
                        continue
                    return True
                except Exception as e:
                    log.warning(f"[_btn] JS click failed: {e}")
                    continue

            # Playwright locator click
            try:
                await btn.click(timeout=5000)
                await asyncio.sleep(1.5)
                # 驗證
                still_there = await self._find_btn(label)
                if still_there and attempt < max_retries:
                    log.warning(f"[_btn] '{label}' still visible after click, retrying...")
                    await asyncio.sleep(1.0)
                    continue
                return True
            except Exception as e:
                log.warning(f"[_btn] Playwright click failed: {e}")
                await asyncio.sleep(1.0)
                continue

        log.error(f"[_btn] '{label}' failed after {max_retries} attempts")
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


async def _wait_dialog_hidden(page, timeout: float = 10) -> bool:
    try:
        await page.locator('[role="dialog"]').last.wait_for(state="hidden", timeout=timeout * 1000)
        return True
    except Exception:
        return False


async def _ensure_ig_logged_in(page) -> bool:
    for attempt in range(30):
        await asyncio.sleep(1)
        try:
            url = page.url.lower()
            if "instagram.com" in url:
                if "/accounts/login" in url:
                    log.debug(f"[_ensure] 等待登入... {attempt+1}/30")
                    continue
                log.debug(f"[_ensure] 已就緒: {url[:60]}")
                return True
        except Exception:
            pass
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

async def post_ig_human(caption: str, image_path: str) -> str:
    """擬人化 IG 發文（語義點擊架構）。"""
    if not os.path.exists(image_path):
        return f"❌ 圖片不存在: {image_path}"
    file_size = os.path.getsize(image_path)
    if file_size < 1024:
        return f"❌ 圖片太小（{file_size} bytes），IG 要求 > 1KB"

    async with async_playwright() as p:
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

        # 清理殘留 dialog
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

        # 初始化語義按鈕（IG dialog 模式）
        btn = SemanticBtn(page, dialog=True)

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

        # ── Step 2: 等「從電腦選擇」dialog ────────────────────────────────
        if not await _wait_dialog_contains(page, "從電腦選擇", timeout=15):
            await asyncio.sleep(2)
            dt = await page.evaluate(
                "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                "return d ? d.innerText.slice(0, 100) : ''; }"
            )
            if "從電腦選擇" not in dt:
                await ctx.close()
                return "❌ 建立新帖子 dialog 未出現"
        log.debug("[step2] Dialog appeared")

        # ── Step 3: 注入圖片 ───────────────────────────────────────────────
        await asyncio.sleep(_random(0.5, 1.0))

        file_injected = False
        try:
            fc = await ctx.wait_for_file_chooser(timeout=3000)
            await fc.set_files(image_path, timeout=20_000)
            log.debug(f"[step3] File via file_chooser: {image_path}")
            file_injected = True
        except Exception as fc_err:
            log.warning(f"[step3] file_chooser not intercepted ({fc_err}), using JS DataTransfer")
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
                        const dt2 = new DataTransfer();
                        dt2.items.add(file);
                        Object.defineProperty(inp, 'files', {
                            value: dt2.files,
                            writable: true,
                            configurable: true
                        });
                        const tracker = inp._valueTracker;
                        if (tracker) tracker.setValue('');
                        inp.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                        return { ok: true, files: dt2.files.length };
                    } catch(e) {
                        return { error: e.message };
                    }
                }""", b64)
                log.debug(f"[step3] JS DataTransfer result: {result}")
                if not result.get("ok"):
                    raise Exception(f"JS inject failed: {result}")
                file_injected = True
            except Exception as inject_err:
                log.error(f"[step3] JS DataTransfer failed: {inject_err}")
                await page.keyboard.press("Escape")
                raise Exception(f"Image upload failed: {inject_err}")

        if not file_injected:
            await ctx.close()
            return "❌ Image injection failed"

        await asyncio.sleep(_random(3.0, 3.5))
        log.debug("[step3] Image uploaded")

        # ── Step 4: 裁切頁「下一步」（語義點擊 + 自癒）───────────────────
        ok = await btn.click("下一步", timeout=8, max_retries=3)
        if not ok:
            await ctx.close()
            return "❌ 裁切頁「下一步」找不到"
        log.debug("[step4] Crop page → 下一步 ✅")
        await asyncio.sleep(_random(1.5, 2.0))

        # ── Step 5: 濾鏡頁「下一步」───────────────────────────────────────
        ok = await btn.click("下一步", timeout=8, max_retries=3)
        if not ok:
            await ctx.close()
            return "❌ 濾鏡頁「下一步」找不到"
        log.debug("[step5] Filter page → 下一步 ✅")
        await asyncio.sleep(_random(2.0, 2.5))

        # ── Step 6: Caption 頁 ────────────────────────────────────────────
        caption_found = False
        for _ in range(30):
            dt = await page.evaluate(
                "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                "return d ? d.innerText.slice(0, 300) : ''; }"
            )
            if "說明文字" in dt or ("分享" in dt and len(dt) > 10):
                caption_found = True
                log.debug(f"[step6] Caption page detected: {repr(dt[:80])}")
                break
            if "裁切" in dt and _ > 3:
                log.debug("[step6] Still on crop page, retrying 下一步...")
                await btn.click("下一步", timeout=5, max_retries=2)
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

        # 找 caption textbox（用 role=textbox）
        for _ in range(10):
            try:
                boxes = page.locator('[role="dialog"] [role="textbox"]')
                if await boxes.count() > 0:
                    await boxes.first.click(timeout=2000, force=True)
                    log.debug("[step6] Caption textbox clicked")
                    await asyncio.sleep(_random(0.3, 0.5))
                    break
            except Exception as e:
                log.debug(f"[step6] textbox attempt err: {e}")
            await asyncio.sleep(_random(0.3, 0.5))
        else:
            await ctx.close()
            return "❌ 找不到 caption textbox"

        # 輸入 caption
        textbox = page.locator('[role="dialog"] [role="textbox"]').first
        await textbox.fill(caption)
        log.debug(f"[step6] Caption filled: {len(caption)} chars")
        await asyncio.sleep(_random(1.0, 1.5))
        await page.keyboard.press("ArrowRight")

        # ── Step 7: 「分享」────────────────────────────────────────────────
        ok = await btn.click("分享", timeout=8, max_retries=3)
        if not ok:
            await ctx.close()
            return "❌ 找不到「分享」按鈕"
        log.debug("[step7] 分享 clicked ✅")
        await asyncio.sleep(_random(1.0, 1.5))

        # ── Step 8: 等「已分享」→「完成」────────────────────────────────────
        for i in range(50):
            await asyncio.sleep(1)
            try:
                dt = await page.evaluate(
                    "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                    "return d ? d.innerText.slice(0, 200) : ''; }"
                )
                if "已分享" in dt:
                    log.debug(f"[step8] ✅ 已分享（{i + 1}s）")
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
        found_done = await btn.click("完成", timeout=5, max_retries=2)
        if not found_done:
            log.debug("[step8] 完成 not found, pressing Escape")
            await page.keyboard.press("Escape")
        else:
            log.debug("[step8] 完成 clicked ✅")

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
