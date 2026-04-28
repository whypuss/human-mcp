"""
post_threads.py — Threads 圖文發文（Playwright 獨立 Chromium Profile）

用 launch_persistent_context 啟動獨立 Chromium，不影響用戶正常 Chrome。
第一次需手動登入，之後 profile 自動記住。

流程：
1. 啟動 Chromium（獨立 profile）
2. 導航到 Threads 首頁，等待登入
3. 點擊 composer 文字框
4. 上傳圖片（可選）
5. keyboard.type 輸入文字（擬人速度）
6. 點「新增到串文」→「發佈」
7. reload 驗證
"""

import asyncio
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright

log = logging.getLogger("post_threads")
_random_delay = lambda a, b: asyncio.sleep(random.uniform(a, b))

THREADS_PROFILE = Path("/tmp/threads-chromium-profile")
THREADS_PROFILE.mkdir(parents=True, exist_ok=True)


# ── Selector 工廠 ───────────────────────────────────────────────────────────

def _dialog():
    return '[role="dialog"]'

def _textbox():
    return '[role="dialog"] div[role="textbox"]'

def _svg_btn(aria_label: str):
    return f'[role="dialog"] svg[aria-label="{aria_label}"]'


# ── 登入等待 ────────────────────────────────────────────────────────────────

async def _ensure_threads_logged_in(page) -> bool:
    """等待 Threads 頁面加載完成（已登入或未登入）。"""
    for attempt in range(30):
        await asyncio.sleep(1)
        try:
            url = page.url.lower()
            if "threads.net" in url:
                # 檢查是否在登入頁
                if "/login" in url:
                    log.debug(f"[_ensure] 等待登入... {attempt+1}/30")
                    continue
                log.debug(f"[_ensure] 已就緒: {url[:60]}")
                return True
        except Exception:
            pass
    return False


# ── 主流程 ────────────────────────────────────────────────────────────────

async def post_threads(
    message: str,
    image_path: Optional[str] = None,
    wait_verify: bool = True,
) -> str:
    """
    發布 Threads 圖文帖子。

    流程：
      1. 啟動 Chromium（獨立 profile）
      2. 導航到 Threads 首頁
      3. 點擊 composer
      4. 上傳圖片（可選）
      5. keyboard.type 輸入文字
      6. 點「新增到串文」→「發佈」
      7. reload 驗證
    """
    # ── 參數驗證 ──────────────────────────────────────────────────────────
    if image_path and not os.path.exists(image_path):
        return f"❌ 圖片不存在: {image_path}"
    file_size = os.path.getsize(image_path) if image_path else 0
    if image_path and file_size < 1024:
        return f"❌ 圖片太小（{file_size} bytes），要求 > 1KB"

    t0 = time.time()

    async with async_playwright() as p:
        # 啟動獨立 Chromium
        ctx = await p.chromium.launch_persistent_context(
            str(THREADS_PROFILE),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # ── Step 0: 確保已登入 ─────────────────────────────────────────────
        await page.goto("https://www.threads.net/", wait_until="domcontentloaded", timeout=30_000)
        if not await _ensure_threads_logged_in(page):
            await ctx.close()
            return "❌ Threads 加載超時，請在 Chromium 視窗中完成登入後重試"
        await _random_delay(4.0, 5.0)

        # ════════════════════════════════════════════════════════════════
        # Step 1: 確保 composer dialog 已打開
        # ════════════════════════════════════════════════════════════════
        try:
            if await page.locator('[role="dialog"]').last.is_visible(timeout=1000):
                log.debug("Dialog already open, skipping Step 1")
            else:
                raise Exception("not visible")
        except Exception:
            try:
                r = await page.evaluate("""() => {
                    var btns = document.querySelectorAll('[role="button"], button');
                    for (const b of btns) {
                        const label = b.getAttribute('aria-label') || '';
                        const text = b.innerText || '';
                        if (label.includes('輸入內容') || label.includes('新鮮事') ||
                            label.includes('請輸入') || label === '建立') {
                            b.click(); return 'clicked:' + label;
                        }
                        if (text.includes('在想什麼') || text.includes('有什麼新鮮事')) {
                            b.click(); return 'clicked:' + text.slice(0, 30);
                        }
                    }
                    return 'not_found';
                }""")
                log.debug(f"[step1] Composer clicked: {r}")
                if r == "not_found":
                    raise Exception("composer button not found")
                await _random_delay(1.0, 1.5)
                await page.locator('[role="dialog"]').last.wait_for(timeout=5000, state="visible")
                await _random_delay(0.5, 0.8)
            except Exception as e:
                await ctx.close()
                return f"❌ Cannot open composer: {e}"

        # ════════════════════════════════════════════════════════════════
        # Step 2: 點擊文字框，進入輸入模式
        # ════════════════════════════════════════════════════════════════
        try:
            tb = page.locator(_textbox()).last
            await tb.click(timeout=3000, force=True)
            await _random_delay(0.3, 0.6)
        except Exception as e:
            await ctx.close()
            return f"❌ Cannot click textbox: {e}"

        # ════════════════════════════════════════════════════════════════
        # Step 3: 上傳圖片（可選）
        # ════════════════════════════════════════════════════════════════
        if image_path:
            try:
                await page.locator(_svg_btn("附加影音內容")).last.click(timeout=3000, force=True)
                await _random_delay(0.5, 0.8)

                try:
                    fc = await ctx.wait_for_file_chooser(timeout=3000)
                    await fc.set_files(image_path, timeout=20_000)
                    log.debug(f"[step3] File via file_chooser: {image_path}")
                except Exception:
                    # OS file chooser 超時，直接 set_input_files
                    inp = page.locator('[role="dialog"] input[type="file"]').last
                    await inp.set_input_files(image_path, timeout=20_000)
                    log.debug(f"[step3] set_input_files succeeded: {image_path}")

                await asyncio.sleep(8)

            except Exception as e:
                await ctx.close()
                return f"❌ Image upload failed: {e}"
        else:
            log.debug("[step3] Skip (no image)")

        # ════════════════════════════════════════════════════════════════
        # Step 4: 輸入文字（keyboard.type 擬人延遲）
        # ════════════════════════════════════════════════════════════════
        try:
            await page.evaluate("() => window.scrollBy(0, -window.innerHeight * 0.1)")
            await _random_delay(0.2, 0.4)

            delay_ms = random.randint(40, 80)
            await page.keyboard.type(message, delay=delay_ms)
            await _random_delay(0.3, 0.6)

            tb_text = await page.locator(_textbox()).last.inner_text(timeout=3000)
            if not tb_text.strip():
                await ctx.close()
                return "❌ Text did not land in editor"
            log.debug(f"[step4] Editor text: {tb_text[:50]}")

        except Exception as e:
            await ctx.close()
            return f"❌ Typing failed: {e}"

        # ════════════════════════════════════════════════════════════════
        # Step 5: 兩步發文：「新增到串文」→「發佈」
        # ════════════════════════════════════════════════════════════════
        try:
            # 5a: 新增加到串文
            coord_5a = await page.evaluate("""() => {
                const d = document.querySelector('[role="dialog"]');
                if (!d) return null;
                const btns = d.querySelectorAll('[role="button"]');
                for (const b of btns) {
                    if ((b.innerText || '').includes('新增到串文')) {
                        const r = b.getBoundingClientRect();
                        return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
                    }
                }
                return null;
            }""")
            if not coord_5a:
                await ctx.close()
                return "❌ 新增加到串文 button not found"
            await page.mouse.click(coord_5a["x"], coord_5a["y"])
            log.debug(f"[step5a] 新增加到串文 at ({coord_5a['x']}, {coord_5a['y']})")
            await _random_delay(2.0, 3.0)

            # 5b: 發佈
            coord_5b = await page.evaluate("""() => {
                const d = document.querySelector('[role="dialog"]');
                if (!d) return null;
                const btns = d.querySelectorAll('[role="button"]');
                for (const b of btns) {
                    if ((b.innerText || '').includes('發佈')) {
                        const r = b.getBoundingClientRect();
                        return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
                    }
                }
                return null;
            }""")
            if not coord_5b:
                await ctx.close()
                return "❌ 發佈 button not found in step 2"
            await page.mouse.click(coord_5b["x"], coord_5b["y"])
            log.debug(f"[step5b] 發佈 at ({coord_5b['x']}, {coord_5b['y']})")
            await _random_delay(8.0, 10.0)

        except Exception as e:
            await ctx.close()
            return f"❌ Cannot click publish: {e}"

        # ════════════════════════════════════════════════════════════════
        # Step 6: 驗證
        # ════════════════════════════════════════════════════════════════
        elapsed = time.time() - t0
        if wait_verify:
            try:
                await page.locator('[role="dialog"]').last.wait_for(timeout=5000, state="hidden")
            except Exception:
                pass

            await page.reload(wait_until="domcontentloaded")
            await _random_delay(2.0, 3.0)
            body = await page.inner_text("body")

            if message[:20] in body:
                await ctx.close()
                return f"✅ Posted to Threads in {elapsed:.1f}s"
            else:
                await ctx.close()
                return f"✅ Posted to Threads in {elapsed:.1f}s（驗證：reload 頁面確認）"

        await ctx.close()
        return f"✅ Posted to Threads in {elapsed:.1f}s"


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    if len(sys.argv) < 2:
        print("用法: python post_threads.py <message> [image_path]")
        sys.exit(1)

    msg = sys.argv[1]
    img = sys.argv[2] if len(sys.argv) > 2 else None

    result = asyncio.run(post_threads(msg, img))
    print(result)
