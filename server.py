"""
Human MCP — 打開瀏覽器搜圖，本地保存，人工選圖
"""

import subprocess
import logging
from pathlib import Path
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("human-mcp")

app = FastAPI()

SAVE_DIR = Path.home() / "Downloads" / "mcp_images"
SAVE_DIR.mkdir(parents=True, exist_ok=True)


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
