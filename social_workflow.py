"""
social_workflow.py — 3來源社群發文 Workflow（human-mcp 版本）

用途：全自動從 Google Trends / 微博熱搜 抓話題 → human-mcp /scrape 下載圖片
      → Gemini 生成 caption → 發布到 FB + Threads + IG

數據流：
  Trends (CDP) → Gemini (CDP) → /scrape (HTTP) → 圖片 → FB/Threads/IG (CDP)

用法：
  python3 social_workflow.py <source>
  source: 1=Google Trends HK, 2=微博熱搜, 3=Google Trends US

依賴：
  - Chromium on port 9333 (CDP，已登入 FB/IG/Threads/Gemini)
  - human-mcp server running on localhost:8080
"""

import asyncio
import json
import logging
import random
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("social_workflow")

# ── 路徑 ────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
POSTED_TOPICS_FILE = Path.home() / ".hermes" / "cron" / "output" / "posted_topics_3source.json"
POSTED_TOPICS_FILE.parent.mkdir(parents=True, exist_ok=True)

# ── CDP 瀏覽器連接 ─────────────────────────────────────────────────────────

def _get_cdp_browser(port=9333):
    """測試 CDP port 是否可用。"""
    for p in [port, 9222]:
        try:
            req = urllib.request.Request(
                f"http://localhost:{p}/json",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                tabs = json.loads(r.read())
                return p, tabs
        except Exception:
            pass
    return None, []


# ── 音視頻下載 ────────────────────────────────────────────────────────────────

def load_posted_topics() -> list:
    if not POSTED_TOPICS_FILE.exists():
        return []
    try:
        with open(POSTED_TOPICS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def add_posted_topic(topic: str):
    topics = load_posted_topics()
    if topic not in topics:
        topics.append(topic)
    POSTED_TOPICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(POSTED_TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(topics, f, ensure_ascii=False, indent=2)


# ── 話題來源 ───────────────────────────────────────────────────────────────

SOURCE_NAMES = {
    1: "Google Trends HK",
    2: "微博熱搜",
    3: "Google Trends US",
}


def _find_page_by_url(ctx, predicate) -> tuple:
    """找第一個 URL 符合 predicate 的 page。"""
    for pg in ctx.pages:
        if predicate(pg.url):
            return pg
    return None


async def fetch_gtrends_hk(ctx, skip_topics=None) -> list:
    """Google Trends HK，取有數字排序的話題（置頂/熱/薦跳過）。"""
    skip_topics = skip_topics or []
    tg = _find_page_by_url(ctx, lambda u: "trends.google" in u.lower() and "geo=HK" in u and "trending" in u)
    if not tg:
        return []
    await tg.bring_to_front()
    await tg.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(1.5)

    # 用 inner_text() 讀取 shadow DOM 內容
    body_text = await tg.locator("body").inner_text()
    lines = [l.strip() for l in body_text.split("\n") if l.strip()]

    # topics 在「搜尋量」和「已開始」之間的兩行區塊
    # 格式：[topic_name, search_volume, trend_arrow, percentage, timestamp, ...]
    topics = []
    for i, line in enumerate(lines):
        if any(c.isdigit() for c in line) and line not in skip_topics:
            # 過濾導航关键字
            if any(k in line for k in ["首頁", "探索", "熱搜", "Google", "Trends", "sort", "category", "匯出", "搜尋", "依名稱", "依搜尋", "依最近", "趨勢詳細", "更新時間"]):
                continue
            # 這是 topic name（通常下一行是 搜尋量）
            if i + 1 < len(lines) and any(c.isdigit() for c in lines[i + 1]):
                topics.append(line)

    # 去重 + 去跳過
    seen = set()
    result = []
    for t in topics:
        if t not in seen and t not in skip_topics and len(t) > 1:
            seen.add(t)
            result.append(t)

    log.info(f"[Trends HK] {len(result)} topics: {result[:5]}")
    return result


async def fetch_gtrends_us(ctx, skip_topics=None) -> list:
    """Google Trends US，取有數字排序的話題。"""
    skip_topics = skip_topics or []
    tg = _find_page_by_url(ctx, lambda u: "trends.google" in u.lower() and "geo=US" in u and "trending" in u)
    if not tg:
        return []
    await tg.bring_to_front()
    await tg.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(1.5)

    body_text = await tg.locator("body").inner_text()
    lines = [l.strip() for l in body_text.split("\n") if l.strip()]

    topics = []
    for i, line in enumerate(lines):
        if any(c.isdigit() for c in line) and line not in skip_topics:
            if any(k in line for k in ["首頁", "探索", "熱搜", "Google", "Trends", "sort", "category", "匯出", "搜尋", "依名稱", "依搜尋", "依最近", "趨勢詳細", "更新時間"]):
                continue
            if i + 1 < len(lines) and any(c.isdigit() for c in lines[i + 1]):
                topics.append(line)

    seen = set()
    result = []
    for t in topics:
        if t not in seen and t not in skip_topics and len(t) > 1:
            seen.add(t)
            result.append(t)

    log.info(f"[Trends US] {len(result)} topics: {result[:5]}")
    return result


async def fetch_weibo(ctx, skip_topics=None) -> list:
    """微博熱搜，取有數字排序話題（置頂/熱/薦跳過）。"""
    skip_topics = skip_topics or []
    wb = _find_page_by_url(ctx, lambda u: "weibo.com" in u or "s.weibo.com" in u)
    if not wb:
        return []
    await wb.bring_to_front()
    await wb.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(1.5)

    body_text = await wb.locator("body").inner_text()
    lines = [l.strip() for l in body_text.split("\n") if l.strip()]

    # 格式：「序号 關鍵詞 熱度數字 標籤」
    # 例如：1	张雪曾拒绝跟华为合作 1129830
    topics = []
    for line in lines:
        # 跳過導航
        if any(k in line for k in ["登录", "注册", "微博热搜", "我的", "热搜", "文娱", "生活", "社会", "序号", "关键词", "熱", "新", "辟谣", "官宣"]):
            continue
        # 要有阿拉伯數字，且不是純數字行
        if any(c.isdigit() for c in line) and not line.isdigit() and len(line) > 2:
            # 去掉末尾的數字熱度，保留話題名
            import re
            # e.g. "张雪曾拒绝跟华为合作 1129830" → "张雪曾拒绝跟华为合作"
            cleaned = re.sub(r"\s+\d+\s*$", "", line).strip()
            if cleaned and cleaned not in skip_topics and len(cleaned) > 1:
                topics.append(cleaned)

    log.info(f"[Weibo] {len(topics)} topics: {topics[:5]}")
    return topics


# ── 圖片下載（human-mcp /scrape HTTP API）──────────────────────────────────

async def scrape_image(topic: str) -> str:
    """
    調用 human-mcp /scrape API 下載圖片。
    失敗返回 None。
    """
    query = urllib.parse.quote(topic[:40])
    url = f"http://localhost:8080/scrape?query={query}&engine=bing&max_images=1"

    def _fetch():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        return urllib.request.urlopen(req, timeout=40).read()

    try:
        log.info(f"[Scrape] GET {url}")
        loop = asyncio.get_event_loop()
        raw = await asyncio.wait_for(loop.run_in_executor(None, _fetch), timeout=45)
        data = json.loads(raw)
        if data.get("downloaded", 0) > 0 and data["images"]:
            img = data["images"][0]
            local = img.get("local_path") or img.get("path")
            log.info(f"[Scrape] ✅ downloaded: {local}")
            return local
        log.warning(f"[Scrape] no images: {data}")
        return None
    except asyncio.TimeoutError:
        log.error(f"[Scrape] timeout after 45s")
        return None
    except Exception as e:
        log.error(f"[Scrape] failed: {e}")
        return None


# ── Gemini caption 生成 ─────────────────────────────────────────────────────

GEMINI_INPUT = 'div[contenteditable="true"][data-tab="0"], div[contenteditable="true"][aria-label*="輸入"]'


def _find_gemini_page(ctx):
    """找 Gemini tab，沒有則新開一個。"""
    for pg in ctx.pages:
        if "gemini.google.com" in pg.url:
            return pg
    g_page = ctx.pages[0]
    return g_page


async def call_gemini(page, prompt: str, timeout=90) -> str:
    """在 Gemini 頁面輸入 prompt，返回回應文字。"""
    inp = page.locator(GEMINI_INPUT)
    await inp.click()
    await inp.fill("")
    await asyncio.sleep(1.0)  # 等 fill 完全生效，DOM 穩定
    await inp.type(prompt, delay=60)  # 60ms/字，確保每個字都輸入到位
    await asyncio.sleep(1.5)  # 等 React state 更新，確保文字完全進入 input
    await page.keyboard.press("Enter")

    await asyncio.sleep(6)
    start = time.time()

    for _ in range(15):
        await asyncio.sleep(4)

        response = await page.evaluate("""() => {
            const all = Array.from(document.querySelectorAll('.model-response-text'));
            if (all.length === 0) return { status: 'no-response', text: '' };
            const last = all[all.length - 1];
            const text = (last.innerText || '').trim();
            const isProcessing = last.classList.contains('processing-state-visible');
            return { status: isProcessing ? 'processing' : 'done', text };
        }""")

        elapsed = int(time.time() - start)

        if len(response["text"]) > 80 and elapsed > 25:
            return response["text"][:1000]

        if response["status"] == "done" and len(response["text"]) > 5:
            return response["text"][:1000]

    response = await page.evaluate("""() => {
        const all = Array.from(document.querySelectorAll('.model-response-text'));
        if (all.length === 0) return '';
        return (all[all.length - 1].innerText || '').trim();
    }""")
    return response[:1000] if response else "[Gemini timeout]"


async def generate_caption(topic: str, source: int, ctx) -> dict:
    """
    Gemini 生成約 100 字正文 + 5 個關鍵詞（繁體中文）。
    回傳 {body, keywords, fb, ig, threads}
    """
    gemini_page = _find_gemini_page(ctx)
    await gemini_page.bring_to_front()

    source_label = SOURCE_NAMES.get(source, f"來源{source}")

    prompt = f"""你是一個香港社交媒體內容創作專家。

請為以下話題創作一篇 Facebook / Instagram / Threads 帖子。

話題：「{topic}」（來源：{source_label}）

請嚴格按照以下格式輸出，不要加任何前置說明：

【正文】（約 100 字，繁體中文，廣東話口語，客觀資訊類風格，例如「据悉」「有消息指」「近日」之類，不要用「我」「我們」「我睇到」「我去咗」等第一人稱，純資訊分享，不要加 emoji）

【關鍵詞】（5個，用 # 開頭，繁體中文，例如：#香港 #話題 #電影 #推薦 #熱門）

直接輸出，不要加「以下是」等文字。"""

    log.info(f"[Gemini] 生成 caption for '{topic}' ({source_label})...")
    response = await call_gemini(gemini_page, prompt)
    log.info(f"[Gemini] 回應 {len(response)} chars: {response[:80]}...")

    # 解析正文和關鍵詞
    clean = response.strip()

    # 判斷格式：先出現【正文】還是【關鍵詞】
    first_body = clean.find("【正文】")
    first_kw = clean.find("【關鍵詞】")

    if first_body != -1 and first_kw != -1:
        # 正常格式：正文在前，關鍵詞在後
        body_text = clean[first_body + 4:first_kw].strip()
        keywords_text = clean[first_kw + 5:].strip()
    elif first_kw != -1:
        # 只有【關鍵詞】：關鍵詞在前表示正文缺失，用關鍵詞充當 body
        body_text = f"針對「{topic}」的熱門討論引發關注。"
        keywords_text = clean[first_kw + 5:].strip()
    elif first_body != -1:
        # 只有【正文】
        body_text = clean[first_body + 4:].strip()
    else:
        # 完全沒有標記，整段當 body
        body_text = clean

    def make_caption(body, keywords, max_len):
        full = f"{body}\n\n{keywords}" if keywords else body
        return full[:max_len]

    return {
        "body": body_text,
        "keywords": keywords_text,
        "fb": make_caption(body_text, keywords_text, 280),
        "ig": make_caption(body_text, keywords_text, 500),
        "threads": make_caption(body_text, keywords_text, 500),
    }


# ── 主流程 ──────────────────────────────────────────────────────────────────

async def run_workflow(source: int):
    print("=" * 60)
    print(f"Social Workflow 3-Source — 來源 {source}: {SOURCE_NAMES.get(source, '未知')}")
    print("=" * 60)

    posted = load_posted_topics()
    print(f"\n[Init] 已發布 topic ({len(posted)}): {posted[-5:]}")

    async with async_playwright() as p:
        port, _ = _get_cdp_browser()
        if not port:
            print("❌ 無法連接 CDP Chromium（port 9333 或 9222）")
            return {"error": "CDP not available"}

        log.info(f"[CDP] Connected to port {port}")

        browser = await p.chromium.connect_over_cdp(
            f"http://localhost:{port}", timeout=20_000
        )
        ctx = browser.contexts[0]

        # ── Step 1: 抓來源 ──────────────────────────────────────
        source_name = SOURCE_NAMES.get(source, f"來源{source}")
        print(f"\n[Step 1] 抓 {source_name}...")

        if source == 1:
            topics = await fetch_gtrends_hk(ctx, skip_topics=posted)
        elif source == 2:
            topics = await fetch_weibo(ctx, skip_topics=posted)
        elif source == 3:
            topics = await fetch_gtrends_us(ctx, skip_topics=posted)
        else:
            print(f"❌ 未知來源：{source}")
            return {"error": f"Unknown source {source}"}

        if not topics:
            print(f"❌ 無法取得 {source_name} 話題（或全部已發布過）")
            await browser.close()
            return {"error": "No topics available"}

        print(f"  取得 {len(topics)} 個話題：{topics[:5]}...")

        # ── Step 2: /scrape 下載圖片 ────────────────────────────
        chosen_topic = None
        image_path = None

        for topic in topics:
            print(f"\n[Step 2] 嘗試話題: '{topic}'")
            image_path = await scrape_image(topic)
            if image_path:
                chosen_topic = topic
                print(f"  ✅ 圖片找到: {image_path}")
                break
            else:
                print(f"  ❌ 找不到圖片，跳過")

        if not chosen_topic or not image_path:
            print("❌ 所有話題都找不到圖片")
            await browser.close()
            return {"error": "No image found"}

        # ── Step 3: Gemini 生成 caption ──────────────────────
        print(f"\n[Step 3] Gemini 生成 caption...")
        caption_data = await generate_caption(chosen_topic, source, ctx)

        print(f"  內容: {caption_data['body'][:60]}...")
        print(f"  關鍵詞: {caption_data['keywords'][:60]}...")

        # ── Step 4: 發布 ──────────────────────────────────────
        print(f"\n[Step 4] 發布到 FB → Threads → IG...")
        from post_facebook import post_facebook
        from post_threads import post_threads
        from post_ig_human import post_ig_human

        # 確保 Threads tab 已打開
        threads_tab = None
        for pg in ctx.pages:
            if "threads.net" in pg.url and "settings" not in pg.url:
                threads_tab = pg
                break
        if not threads_tab:
            print("  [Threads] 開新標籤...")
            threads_tab = await ctx.new_page()
            await threads_tab.goto("https://www.threads.net/", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)

        results = {}

        platforms = [
            ("facebook", caption_data["fb"], post_facebook),
            ("threads", caption_data["threads"], post_threads),
            ("instagram", caption_data["ig"], post_ig_human),
        ]

        for platform_name, text, post_fn in platforms:
            try:
                result = await post_fn(text, image_path)
                print(f"  [{platform_name}] {result}")
                results[platform_name] = result
                await asyncio.sleep(random.uniform(2, 4))
            except Exception as e:
                msg = f"❌ {e}"
                print(f"  [{platform_name}] {msg}")
                results[platform_name] = msg

        # ── Step 5: 記錄已發布 ─────────────────────────────────
        print(f"\n[Step 5] 更新已發布記錄...")
        add_posted_topic(chosen_topic)
        print(f"  ✅ '{chosen_topic}' 已記錄")

        print(f"\n{'=' * 60}")
        print(f"來源 {source}（{source_name}）完成")
        print(f"{'=' * 60}")
        for plat, res in results.items():
            print(f"  {plat}: {res}")

        try:
            await browser.close()
        except Exception as e:
            log.info(f"[Browser] close: {e}")

        return results


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 social_workflow.py <source>")
        print("  source: 1=Google Trends HK, 2=微博熱搜, 3=Google Trends US")
        sys.exit(1)

    source = int(sys.argv[1])
    result = asyncio.run(run_workflow(source))
    sys.exit(0 if "error" not in result else 1)
