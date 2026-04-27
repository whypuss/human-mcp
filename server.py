"""
Human MCP — 打開瀏覽器搜圖，本地保存，人工選圖
"""

import subprocess
import logging
import time
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("human-mcp")

app = FastAPI()

SAVE_DIR = Path.home() / "Downloads" / "mcp_images"
SAVE_DIR.mkdir(parents=True, exist_ok=True)

# 追蹤用戶已取得的圖片列表（用於過濾新圖）
_fetched_files: set[str] = set()


# ─────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    engine: str = "bing"  # "bing" | "google"


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "status": "ok",
        "save_dir": str(SAVE_DIR),
        "workflow": "1. POST /search → Chrome opens browser | 2. Save image manually to ~/Downloads/mcp_images/ | 3. Use local path directly with post_instagram()"
    }


@app.post("/search")
async def search(req: SearchRequest):
    """打開瀏覽器搜圖"""
    if req.engine == "bing":
        url = f"https://www.bing.com/images/search?q={req.query.replace(' ', '+')}&first=1"
    else:
        url = f"https://www.google.com/search?q={req.query.replace(' ', '+')}&tbm=isch"

    subprocess.run(["open", "-a", "Google Chrome", url], check=True)
    log.info(f"[search] opened: {url}")

    return {
        "success": True,
        "url": url,
        "engine": req.engine,
        "save_dir": str(SAVE_DIR),
        "instruction": f"Right-click image → Save As → save to {SAVE_DIR}/"
    }


@app.get("/images")
async def list_images():
    """列出已保存的所有圖片（含路徑、修改時間）"""
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


@app.get("/watch")
async def watch_images():
    """
    返回自上次呼び以来新保存的圖片（poll until有新圖）。
    客戶端不斷輪詢，有新圖時立即返回。
    """
    global _fetched_files
    current = {f.name for f in SAVE_DIR.iterdir()
               if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}}

    new = current - _fetched_files
    if new:
        _fetched_files = current
        # 返回最新的一張
        latest = sorted(SAVE_DIR.iterdir(),
                       key=lambda p: p.stat().st_mtime, reverse=True)[0]
        return {
            "new_image": {
                "filename": latest.name,
                "path": str(latest),
                "size_kb": round(latest.stat().st_size / 1024, 1),
            }
        }
    return {"new_image": None}


@app.post("/reset")
async def reset_watch():
    """重置 watch 狀態，下次 /watch 從頭開始追新圖"""
    global _fetched_files
    _fetched_files = set()
    return {"status": "reset"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
