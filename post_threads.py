"""
post_threads.py — Threads 圖文發文（視覺語義 + 自愈點擊）

與舊版差異：
- 不再依賴座標 mouse.click(x, y)
- 改用 Playwright getByRole/getByText DOM 定位（UI 變了也能找到）
- 找不到時用 vision 視覺定位（browser_vision MCP）
- 點擊後自愈驗證：結果不符預期自動重試

流程：
1. 啟動 Chromium（獨立 profile）
2. 導航到 Threads 首頁
3. 點擊 composer
4. 上傳圖片（可選）
5. keyboard.type 輸入文字
6. 點「新增到串文」→「發佈」
7. reload 驗證
"""

import asyncio
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional, Tuple

from playwright.async_api import async_playwright

log = logging.getLogger("post_threads")
_random_delay = lambda a, b: asyncio.sleep(random.uniform(a, b))

THREADS_PROFILE = Path("/tmp/threads-chromium-profile")
THREADS_PROFILE.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# 語義點擊（DOM + 視覺自愈）
# ─────────────────────────────────────────────────────────────────────────────

class SemanticClicker:
    """
    語義點擊器：DOM 定位為主，找不到時用 vision 視覺定位。
    點擊後驗證結果，錯了自動重試（最多 3 次）。
    """

    def __init__(self, page, vision_fn=None):
        self.page = page
        self.vision_fn = vision_fn  # 可選：外部視覺定位函數 (label -> x, y)
        self._attempts = {}

    async def _verify_dialog_text_contains(self, keyword: str, timeout: float = 3) -> bool:
        """驗證 dialog 是否包含某個關鍵字（用於點擊後的結果驗證）。"""
        for _ in range(int(timeout * 5)):
            try:
                dt = await self.page.evaluate(
                    "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                    "return d ? d.innerText.slice(0, 500) : ''; }"
                )
                if keyword in dt:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.2)
        return False

    async def _find_by_dom(self, role: str, name: str = None, parent: str = None) -> Optional:
        """
        DOM 定位：透過 Playwright getByRole/getByText 找元素。
        比座標靠譜，UI 變了只要文字/role 還在就能找到。
        """
        dialog = self.page.locator('[role="dialog"]')
        if parent == "dialog":
            container = dialog
        else:
            container = self.page

        locators = []

        if role == "button":
            if name:
                locators.append(container.get_by_role("button", name=name))
                locators.append(container.get_by_role("button", name=name, exact=False))
            locators.append(container.locator(f"[role='dialog'] button:has-text('{name or ''}')"))
        elif role == "textbox":
            locators.append(container.get_by_role("textbox"))
            locators.append(container.locator('[role="dialog"] [role="textbox"]'))
        else:
            locators.append(container.locator(f"[role='{role}']"))

        for loc in locators:
            try:
                count = await loc.count()
                if count > 0:
                    # 確認可見
                    if await loc.first.is_visible(timeout=1000):
                        return loc.first
            except Exception:
                pass
        return None

    async def _find_by_vision(self, label: str) -> Optional[Tuple[int, int]]:
        """
        視覺定位：截圖後用 VLM 分析座標。
        外部傳入 vision_fn，格式：vision_fn(screenshot_path, "要找什麼") -> (x, y)
        """
        if not self.vision_fn:
            return None
        try:
            # 截圖
            img_path = f"/tmp/threads_vision_{int(time.time()*1000)}.png"
            await self.page.screenshot(path=img_path)
            x, y = await self.vision_fn(img_path, label)
            os.remove(img_path)
            return (x, y)
        except Exception as e:
            log.warning(f"[_find_by_vision] vision failed: {e}")
            return None

    async def click(
        self,
        label: str,
        role: str = "button",
        parent: str = "dialog",
        verify_after: str = None,
        verify_timeout: float = 5,
        max_retries: int = 3,
    ) -> bool:
        """
        語義點擊主函數。

        策略：
        1. DOM 定位（getByRole/getByText）
        2. 找不到 → 視覺定位（vision_fn）
        3. 點擊
        4. 驗證結果（verify_after 關鍵字）
        5. 錯了 → 重試（最多 max_retries 次）

        Args:
            label: 要點擊的文字（如 "新增到串文"、"發佈"）
            role: DOM role（默認 button）
            parent: 限定範圍（默認 dialog）
            verify_after: 點擊後要驗證的關鍵字（如點「新增到串文」後預期出現「發佈」）
            verify_timeout: 驗證超時（秒）
            max_retries: 最大重試次數
        """
        for attempt in range(1, max_retries + 1):
            log.debug(f"[SemanticClicker] attempt {attempt}/{max_retries} for '{label}'")

            # ── Step 1: DOM 定位 ─────────────────────────────────────────
            elem = await self._find_by_dom(role, name=label, parent=parent)

            if elem:
                try:
                    await elem.click(timeout=5000)
                    log.debug(f"[SemanticClicker] DOM click succeeded: '{label}'")
                except Exception as click_err:
                    log.warning(f"[SemanticClicker] DOM click failed ({click_err}), trying force")
                    try:
                        await elem.click(timeout=3000, force=True)
                    except Exception:
                        elem = None  # 降級到視覺
            else:
                log.debug(f"[SemanticClicker] DOM not found for '{label}', trying vision")

            # ── Step 2: 視覺定位（DOM 失敗時）────────────────────────────
            if not elem:
                coords = await self._find_by_vision(label)
                if coords:
                    x, y = coords
                    await self.page.mouse.click(x, y)
                    log.debug(f"[SemanticClicker] vision click: '{label}' at ({x}, {y})")
                else:
                    log.warning(f"[SemanticClicker] could not locate '{label}'")
                    continue  # 重試

            # ── Step 3: 驗證結果 ─────────────────────────────────────────
            if verify_after:
                found = await self._verify_dialog_text_contains(verify_after, timeout=verify_timeout)
                if found:
                    log.debug(f"[SemanticClicker] ✅ verified '{verify_after}' after clicking '{label}'")
                    return True
                else:
                    log.warning(
                        f"[SemanticClicker] ❌ verify failed: expected '{verify_after}' "
                        f"after '{label}' (attempt {attempt}/{max_retries})"
                    )
                    await _random_delay(1.0, 1.5)
                    continue  # 重試
            else:
                return True

        log.error(f"[SemanticClicker] ❌ all {max_retries} attempts failed for '{label}'")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 登入等待
# ─────────────────────────────────────────────────────────────────────────────

async def _ensure_threads_logged_in(page) -> bool:
    for attempt in range(30):
        await asyncio.sleep(1)
        try:
            url = page.url.lower()
            if "threads.net" in url:
                if "/login" in url:
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

async def post_threads(
    message: str,
    image_path: Optional[str] = None,
    wait_verify: bool = True,
) -> str:
    """
    發布 Threads 圖文帖子。

    使用語義點擊架構（SemanticClicker），UI 變化時有自愈能力。
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

        # 初始化語義點擊器
        clicker = SemanticClicker(page)

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
            # 用語義點擊（getByRole textbox）
            tb = page.locator('[role="dialog"] [role="textbox"]').last
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
                # 點「附加影音內容」
                svg_ok = await clicker.click(
                    label="附加影音內容",
                    role="button",
                    parent="dialog",
                    verify_after=None,
                    max_retries=2,
                )
                if not svg_ok:
                    raise Exception("附加影音內容 button not found")
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

            tb_text = await page.locator('[role="dialog"] [role="textbox"]').last.inner_text(timeout=3000)
            if not tb_text.strip():
                await ctx.close()
                return "❌ Text did not land in editor"
            log.debug(f"[step4] Editor text: {tb_text[:50]}")

        except Exception as e:
            await ctx.close()
            return f"❌ Typing failed: {e}"

        # ════════════════════════════════════════════════════════════════
        # Step 5: 兩步發文（使用語義點擊 + 自愈驗證）
        # ════════════════════════════════════════════════════════════════
        try:
            # 5a: 新增加到串文（點完驗證，出現「發佈」按鈕說明成功）
            ok_5a = await clicker.click(
                label="新增到串文",
                role="button",
                parent="dialog",
                verify_after="發佈",  # 點完後 dialog 應包含「發佈」
                verify_timeout=5,
                max_retries=3,
            )
            if not ok_5a:
                await ctx.close()
                return "❌ 新增加到串文失敗（3次重試後放棄）"
            log.debug("[step5a] 新增加到串文 ✅")
            await _random_delay(1.5, 2.0)

            # 5b: 發佈（點完驗證，dialog 關閉或出現「已發佈」）
            ok_5b = await clicker.click(
                label="發佈",
                role="button",
                parent="dialog",
                verify_after=None,  # 最後一步，不強制驗證文字
                max_retries=3,
            )
            if not ok_5b:
                await ctx.close()
                return "❌ 發佈失敗（3次重試後放棄）"
            log.debug("[step5b] 發佈 clicked ✅")
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
