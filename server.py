"""
Human MCP — AI Agent 搜圖工具
流程：
  1. POST /search → Chrome 開啟視覺瀏覽
  2. GET /scrape  → 無頭解析頁面，提取圖片 URL 列表
  3. GET /download?url=... → 直接下載圖片到本地 → 返回路徑
  4. GET /list     → 列出已下載圖片
  5. GET /cdp-port → 寫入並返回當前 CDP port（供 post_facebook.py 使用）
"""

import subprocess
import logging
import time
import json
import re
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("human-mcp")

app = FastAPI()

SAVE_DIR = Path.home() / "Downloads" / "mcp_images"
SAVE_DIR.mkdir(parents=True, exist_ok=True)

CDP_PORT_FILE = Path.home() / ".cdp_port"


def get_active_cdp_port() -> int:
    """讀取目前 active CDP port，順序：文件 → 預設 9333。"""
    if CDP_PORT_FILE.exists():
        try:
            return int(CDP_PORT_FILE.read_text().strip())
        except Exception:
            pass
    return 9333


def set_active_cdp_port(port: int) -> None:
    """寫入 CDP port 到檔案，供其他進程讀取。"""
    CDP_PORT_FILE.write_text(str(port))
    log.info(f"[cdp] Active port set to {port}")


# ─────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    engine: str = "bing"  # "bing" | "google"


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _fetch_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }


def _download_image(img_url: str, filename: str) -> Optional[dict]:
    """下載單張圖片到本地，返回 metadata 或 None"""
    try:
        cdn_match = re.search(r'cdnurl=([^&]+)', img_url)
        if cdn_match:
            img_url = urllib.parse.unquote(cdn_match.group(1))

        filepath = SAVE_DIR / filename
        req = urllib.request.Request(img_url, headers=_fetch_headers())
        with urllib.request.urlopen(req, timeout=15) as response:
            data = response.read()

        with open(filepath, "wb") as f:
            f.write(data)

        size_kb = round(len(data) / 1024, 1)
        log.info(f"[download] {filename} ({size_kb}KB)")
        return {
            "filename": filename,
            "path": str(filepath),
            "size_kb": size_kb,
            "source_url": img_url[:100]
        }
    except Exception as e:
        log.warning(f"[download] failed: {img_url[:60]} → {e}")
        return None


def _scrape_bing_images(query: str, max_images: int = 6) -> list[dict]:
    """無頭抓取 Bing Images 搜尋結果"""
    url = f"https://www.bing.com/images/search?q={urllib.parse.quote(query)}&first=0"
    try:
        req = urllib.request.Request(url, headers=_fetch_headers())
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.error(f"[scrape] bing request failed: {e}")
        return []

    # Bing: 圖片 URL 在 m="..." JSON 屬性或 data-src 中
    urls = []
    # 匹配 m="{"..."}" 內的 murl
    for m_match in re.finditer(r'm="([^"]+)"', html):
        try:
            m_data = urllib.parse.unquote(m_match.group(1))
            m_json = json.loads(m_data)
            img_url = m_json.get("murl", "")
            if img_url and img_url.startswith("http") and "data:image" not in img_url:
                thumb = m_json.get("turl", "") or m_json.get("murl", "")
                title = m_json.get("tt", "")
                urls.append({"url": img_url, "thumb": thumb, "title": title})
        except (json.JSONDecodeError, KeyError):
            continue

    # 備用：找 data-src
    for src_match in re.finditer(r'data-src="(https?://[^"]+)"', html):
        img_url = src_match.group(1)
        if img_url not in [u["url"] for u in urls] and "data:image" not in img_url:
            urls.append({"url": img_url, "thumb": img_url, "title": ""})

    log.info(f"[scrape] bing: found {len(urls)} URLs")
    return urls[:max_images]


def _scrape_google_images(query: str, max_images: int = 6) -> list[dict]:
    """無頭抓取 Google Images 搜尋結果"""
    url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&tbm=isch"
    try:
        req = urllib.request.Request(url, headers=_fetch_headers())
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.error(f"[scrape] google request failed: {e}")
        return []

    # Google: 解析 AF_initDataCallback 的 JSON 數據
    urls = []
    for match in re.finditer(r'\["(https?://[^"]+)",\s*\d+,\s*\d+\]', html):
        img_url = match.group(1)
        if img_url and not img_url.startswith("data:") and "gstatic.com" not in img_url:
            if img_url not in [u["url"] for u in urls]:
                urls.append({"url": img_url, "thumb": img_url, "title": ""})

    # 備用：抓 OU（原始 URL）參數
    for ou_match in re.finditer(r'\?imgurl=([^&"]+)', html):
        img_url = urllib.parse.unquote(ou_match.group(1))
        if img_url.startswith("http") and img_url not in [u["url"] for u in urls]:
            urls.append({"url": img_url, "thumb": img_url, "title": ""})

    log.info(f"[scrape] google: found {len(urls)} URLs")
    return urls[:max_images]


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "status": "ok",
        "save_dir": str(SAVE_DIR),
        "workflow": (
            "1. POST /search → Chrome opens for visual browsing\n"
            "2. GET  /scrape?query=xxx → extracts image URLs (no browser needed)\n"
            "3. GET  /download?url=xxx → downloads to local, returns path\n"
            "4. GET  /list → shows all downloaded images\n"
            "5. GET  /cdp-port → returns current active CDP port"
        )
    }


@app.get("/cdp-port")
async def cdp_port(port: int = Query(9333, description="寫入並返回此 CDP port")):
    """
    寫入 CDP port 到 ~/.cdp_port，供 post_facebook.py 等工具讀取。
    Example: GET /cdp-port?port=9333
    """
    set_active_cdp_port(port)
    return {"active_cdp_port": port, "file": str(CDP_PORT_FILE)}


@app.get("/active-cdp-port")
async def active_cdp_port():
    """讀取目前 active CDP port。"""
    return {"active_cdp_port": get_active_cdp_port(), "file": str(CDP_PORT_FILE)}


@app.post("/search")
async def search(req: SearchRequest):
    """開啟 Chrome 視覺瀏覽（搜尋關鍵字）"""
    query = req.query.replace(" ", "+")
    if req.engine == "google":
        url = f"https://www.google.com/search?q={query}&tbm=isch"
    else:
        url = f"https://www.bing.com/images/search?q={query}&first=1"

    try:
        subprocess.run(["open", "-a", "Google Chrome", url], check=True)
        log.info(f"[search] opened: {url}")
    except Exception as e:
        log.error(f"[search] open Chrome failed: {e}")
        return {"error": str(e)}

    return {
        "success": True,
        "url": url,
        "engine": req.engine,
        "save_dir": str(SAVE_DIR),
        "instruction": "Use /scrape to extract URLs & /download to save images"
    }


@app.get("/scrape")
async def scrape(query: str = Query(..., description="搜尋關鍵字"),
                 engine: str = Query("bing", description="bing | google"),
                 max_images: int = Query(6, ge=1, le=20)):
    """
    Playwright 無頭渲染 Bing/Google Images，自動下載圖片到本地。
    返回下載後的本地檔案路徑列表，全自動無需人工干預。
    """
    log.info(f"[scrape] query={query} engine={engine} max={max_images}")

    scraper_path = Path(__file__).parent / "scraper.js"
    try:
        result = subprocess.run(
            ["node", str(scraper_path), str(max_images), query, engine],
            capture_output=True,
            text=True,
            timeout=90
        )
        if result.returncode != 0:
            log.error(f"[scrape] node failed: {result.stderr}")
            return {"error": f"Scraper failed: {result.stderr[:200]}"}

        data = json.loads(result.stdout)
        log.info(f"[scrape] done: found={data['found']} downloaded={data['downloaded']}")
        return data

    except subprocess.TimeoutExpired:
        return {"error": "Scraper timeout (>90s)"}
    except Exception as e:
        log.error(f"[scrape] exception: {e}")
        return {"error": str(e)}


@app.get("/download")
async def download(url: str = Query(..., description="圖片 URL"),
                  filename: Optional[str] = Query(None, description="自訂檔案名")):
    """
    直接下載圖片 URL 到本地 ~/Downloads/mcp_images/，
    返回本地檔案路徑，供後續上傳工具使用。
    """
    if not filename:
        # 從 URL 推斷副檔名
        parsed = urllib.parse.urlparse(url)
        ext = Path(parsed.path).suffix.split("?")[0].lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
            ext = ".jpg"
        timestamp = int(time.time())
        filename = f"img_{timestamp}{ext}"

    result = _download_image(url, filename)
    if not result:
        return {"error": f"Failed to download: {url[:80]}"}

    return {
        "success": True,
        **result,
        "save_dir": str(SAVE_DIR)
    }


@app.get("/batch-download")
async def batch_download(urls: str = Query(..., description="URL 列表，逗號分隔"),
                        prefix: Optional[str] = Query(None)):
    """
    批量下載多張圖片。
    Example: /batch-download?urls=https://a.jpg,https://b.jpg&prefix=taipei
    """
    url_list = [u.strip() for u in urls.split(",") if u.strip()]
    ts = int(time.time())
    results = []

    for i, img_url in enumerate(url_list):
        fname = f"{prefix}_{ts}_{i}.jpg" if prefix else f"img_{ts}_{i}.jpg"
        result = _download_image(img_url, fname)
        if result:
            results.append(result)

    return {
        "total": len(url_list),
        "downloaded": len(results),
        "images": results,
        "save_dir": str(SAVE_DIR),
        "tip": "Use 'images[*].path' as input for social-mcp upload tools"
    }


@app.get("/list")
async def list_images():
    """列出已下載的圖片（含路徑、原始 URL、尺寸）"""
    images = []
    for f in sorted(SAVE_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
            stat = f.stat()
            images.append({
                "filename": f.name,
                "path": str(f),
                "size_kb": round(stat.st_size / 1024, 1),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
    return {"count": len(images), "images": images, "save_dir": str(SAVE_DIR)}


# ─────────────────────────────────────────────
# Bootstrap
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
