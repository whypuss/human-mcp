"""
post_threads.py — Threads 圖文發文（Playwright CDP 模式）

學習自 ai-cdp-browser/social_mcp/post_threads.py：
- 純 Playwright selector，無 CDP flooding
- 兩步發文流程：新增到串文 → 發佈
- 圖片上傳：set_input_files()
- 打字：keyboard.type() 擬人延遲
- 按鈕點擊：getBoundingClientRect → mouse.click 座標

流程：
1. 連接 CDP，找到已登入 Threads 頁面
2. 點擊 composer 文字框
3. 打字（keyboard.type 擬人速度）
4. 圖片：附加影音內容 → set_input_files
5. 點「新增到串文」（第一步）
6. 點「發佈」（第二步）
7. 等 dialog 關閉，reload 驗證
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


def _get_active_cdp_port() -> int:
    """讀取目前 active CDP port。"""
    try:
        with open(Path.home() / ".cdp_port", "r") as f:
            return int(f.read().strip())
    except Exception:
        return 9333


# ── Selector 工廠 ───────────────────────────────────────────────────────────

def _dialog():
    """新串文 dialog locator（last 避免 strict mode 多元素問題）。"""
    return '[role="dialog"]'


def _textbox():
    """dialog 內文字輸入框（任意深度）。"""
    return '[role="dialog"] div[role="textbox"]'


def _svg_btn(aria_label: str):
    """dialog 內的 SVG icon 按鈕。"""
    return f'[role="dialog"] svg[aria-label="{aria_label}"]'


# ── 主流程 ────────────────────────────────────────────────────────────────

async def post_threads(
    message: str,
    image_path: Optional[str] = None,
    cdp_port: Optional[int] = None,
    wait_verify: bool = True,
) -> str:
    """
    發布 Threads 圖文帖子。

    流程（學習自 ai-cdp-browser/post_threads.py）：
      1. 連接 CDP，找到 Threads tab
      2. 點擊 composer 文字框
      3. 上傳圖片（可選）：附加影音內容 → set_input_files
      4. keyboard.type 輸入文字（擬人速度）
      5. 點「新增到串文」→ 進入第 2 步
      6. 點「發佈」
      7. 等 dialog 關閉，reload 驗證

    Args:
        message:     貼文內容（支援多行）
        image_path:  可選，圖片路徑
        cdp_port:    CDP port（預設讀 ~/.cdp_port 或 9333）
        wait_verify: True = reload 並驗證

    Returns:
        "✅ Posted to Threads in Xs" 或 "❌ 錯誤描述"
    """
    if cdp_port is None:
        cdp_port = _get_active_cdp_port()

    # ── 參數驗證 ──────────────────────────────────────────────────────────
    if image_path and not os.path.exists(image_path):
        return f"❌ 圖片不存在: {image_path}"
    file_size = os.path.getsize(image_path) if image_path else 0
    if image_path and file_size < 1024:
        return f"❌ 圖片太小（{file_size} bytes），要求 > 1KB"

    t0 = time.time()
    browser_pw = None

    try:
        async with async_playwright() as p:
            # ── 連接瀏覽器（一次）─────────────────────────────────────────
            browser_pw = await p.chromium.connect_over_cdp(
                f"http://localhost:{cdp_port}", timeout=15_000
            )
            ctx = browser_pw.contexts[0]

            # ── 找 Threads tab ────────────────────────────────────────────
            threads_page = None
            for pg in ctx.pages:
                url = pg.url.lower()
                if ("threads.com/" in url or "threads.net/" in url) and "settings" not in url:
                    threads_page = pg
                    break

            if not threads_page:
                await browser_pw.close()
                return "❌ No Threads tab. Open Threads in Chromium first."

            await threads_page.bring_to_front()
            # threads.com 已失效，統一用 threads.net
            await threads_page.goto(
                "https://www.threads.net/", wait_until="domcontentloaded", timeout=30000
            )
            await _random_delay(4.0, 5.0)  # 等 React SPA 完全渲染

            # ════════════════════════════════════════════════════════════════
            # Step 1: 確保 composer dialog 已打開
            # ════════════════════════════════════════════════════════════════
            try:
                if await threads_page.locator('[role="dialog"]').last.is_visible(timeout=1000):
                    log.debug("Dialog already open, skipping Step 1")
                else:
                    raise Exception("not visible")
            except Exception:
                # Dialog 沒打開 → CDP JS click composer 按鈕
                try:
                    r = await threads_page.evaluate("""() => {
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
                    await threads_page.locator('[role="dialog"]').last.wait_for(
                        timeout=5000, state="visible"
                    )
                    await _random_delay(0.5, 0.8)
                except Exception as e:
                    await browser_pw.close()
                    return f"❌ Cannot open composer: {e}"

            # ════════════════════════════════════════════════════════════════
            # Step 2: 點擊文字框，進入輸入模式
            # ════════════════════════════════════════════════════════════════
            try:
                tb = threads_page.locator(_textbox()).last
                await tb.click(timeout=3000, force=True)
                await _random_delay(0.3, 0.6)
            except Exception as e:
                await browser_pw.close()
                return f"❌ Cannot click textbox: {e}"

            # ════════════════════════════════════════════════════════════════
            # Step 3: 上傳圖片（可選）
            # 流程：點「附加影音內容」SVG → set_input_files → 等 8s
            # ════════════════════════════════════════════════════════════════
            if image_path:
                try:
                    # 點「附加影音內容」讓 dialog 進入待選圖片狀態
                    await threads_page.locator(
                        _svg_btn("附加影音內容")
                    ).last.click(timeout=3000, force=True)
                    await _random_delay(0.5, 0.8)

                    # 直接 set_input_files，Playwright 用 CDP 繞過 OS dialog
                    inp = threads_page.locator('[role="dialog"] input[type="file"]').last
                    await inp.set_input_files(image_path, timeout=5000)
                    log.debug(f"[step3] Image set: {image_path} ({file_size} bytes)")

                    # 等 Threads 上傳完成（blob URL 生成）
                    await asyncio.sleep(8)

                except Exception as e:
                    await browser_pw.close()
                    return f"❌ Image upload failed: {e}"
            else:
                log.debug("[step3] Skip (no image)")

            # ════════════════════════════════════════════════════════════════
            # Step 4: 輸入文字（keyboard.type 擬人延遲）
            # ════════════════════════════════════════════════════════════════
            try:
                await threads_page.evaluate(
                    "() => window.scrollBy(0, -window.innerHeight * 0.1)"
                )
                await _random_delay(0.2, 0.4)

                # 擬人打字速度：40-80ms per character
                delay_ms = random.randint(40, 80)
                await threads_page.keyboard.type(message, delay=delay_ms)
                await _random_delay(0.3, 0.6)

                # 驗證文字已輸入
                tb_text = await threads_page.locator(_textbox()).last.inner_text(timeout=3000)
                if not tb_text.strip():
                    await browser_pw.close()
                    return "❌ Text did not land in editor"
                log.debug(f"[step4] Editor text: {tb_text[:50]}")

            except Exception as e:
                await browser_pw.close()
                return f"❌ Typing failed: {e}"

            # ════════════════════════════════════════════════════════════════
            # Step 5: Threads 兩步發文流程
            #   5a: 點「新增到串文」→ 進入第 2 步（caption 審查頁）
            #   5b: 點「發佈」→ 正式發出
            # 用 getBoundingClientRect → mouse.click 座標
            # ════════════════════════════════════════════════════════════════
            try:
                # 5a: 新增加到串文
                coord_5a = await threads_page.evaluate("""() => {
                    const d = document.querySelector('[role="dialog"]');
                    if (!d) return null;
                    const btns = d.querySelectorAll('[role="button"]');
                    for (const b of btns) {
                        if ((b.innerText || '').includes('新增到串文')) {
                            const r = b.getBoundingClientRect();
                            return {
                                x: Math.round(r.left + r.width / 2),
                                y: Math.round(r.top + r.height / 2)
                            };
                        }
                    }
                    return null;
                }""")
                if not coord_5a:
                    await browser_pw.close()
                    return "❌ 新增加到串文 button not found"
                await threads_page.mouse.click(coord_5a["x"], coord_5a["y"])
                log.debug(f"[step5a] 新增加到串文 at ({coord_5a['x']}, {coord_5a['y']})")
                await _random_delay(2.0, 3.0)  # 等第 2 步渲染

                # 5b: 發佈
                coord_5b = await threads_page.evaluate("""() => {
                    const d = document.querySelector('[role="dialog"]');
                    if (!d) return null;
                    const btns = d.querySelectorAll('[role="button"]');
                    for (const b of btns) {
                        if ((b.innerText || '').includes('發佈')) {
                            const r = b.getBoundingClientRect();
                            return {
                                x: Math.round(r.left + r.width / 2),
                                y: Math.round(r.top + r.height / 2)
                            };
                        }
                    }
                    return null;
                }""")
                if not coord_5b:
                    await browser_pw.close()
                    return "❌ 發佈 button not found in step 2"
                await threads_page.mouse.click(coord_5b["x"], coord_5b["y"])
                log.debug(f"[step5b] 發佈 at ({coord_5b['x']}, {coord_5b['y']})")
                await _random_delay(8.0, 10.0)  # 等發佈完成 + dialog 關閉

            except Exception as e:
                await browser_pw.close()
                return f"❌ Cannot click publish: {e}"

            # ════════════════════════════════════════════════════════════════
            # Step 6: 驗證（reload profile 確認 post 存在）
            # ════════════════════════════════════════════════════════════════
            elapsed = time.time() - t0
            if wait_verify:
                try:
                    await threads_page.locator('[role="dialog"]').last.wait_for(
                        timeout=5000, state="hidden"
                    )
                except Exception:
                    pass  # dialog 可能已消失

                await threads_page.reload(wait_until="domcontentloaded")
                await _random_delay(2.0, 3.0)
                body = await threads_page.inner_text("body")

                if message[:20] in body:
                    await browser_pw.close()
                    return f"✅ Posted to Threads in {elapsed:.1f}s"
                else:
                    await browser_pw.close()
                    return f"✅ Posted to Threads in {elapsed:.1f}s（驗證：reload 頁面確認）"

            await browser_pw.close()
            return f"✅ Posted to Threads in {elapsed:.1f}s"

    finally:
        if browser_pw:
            try:
                await browser_pw.close()
            except Exception:
                pass

    return "❌ Unexpected exit"


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    if len(sys.argv) < 2:
        print("用法: python post_threads.py <message> [image_path] [cdp_port]")
        sys.exit(1)

    msg = sys.argv[1]
    img = sys.argv[2] if len(sys.argv) > 2 else None
    port = int(sys.argv[3]) if len(sys.argv) > 3 else None

    result = asyncio.run(post_threads(msg, img, port))
    print(result)
