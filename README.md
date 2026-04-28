# Human MCP

AI Agent 全自動圖片搜尋工具 — 支援 Bing/Google 圖片爬蟲 + Instagram 自動發文。

## 解決問題

- Bing/Google Images 有 captcha，傳統 HTTP 爬蟲失效
- 圖片防盜鏈（403/簽名 URL）
- AI Agent 需要本地圖片路徑才能上傳社群平台

## 啟動

```bash
cd ~/human-mcp
uv run python server.py
# 後台運行：
nohup uv run python server.py > /tmp/human-mcp.log 2>&1 &
```

Server 運行在 `http://localhost:8080`

## 核心功能

### 1. 自動圖片爬蟲（全自動，無需人工）

```
GET /scrape?query=關鍵字&engine=bing&max_images=6
```

使用 Playwright 無頭渲染 Bing/Google，自動下載圖片到 `~/Downloads/mcp_images/`，返回本地路徑。

```bash
curl "http://localhost:8080/scrape?query=thunder+vs+suns+NBA&engine=bing&max_images=3"
```

```json
{
  "query": "thunder vs suns NBA",
  "engine": "bing",
  "found": 3,
  "downloaded": 3,
  "images": [
    {"index": 0, "url": "https://...", "local_path": "/Users/xxx/Downloads/mcp_images/img_xxx_0.jpg", "title": "..."},
    {"index": 1, "url": "https://...", "local_path": "/Users/xxx/Downloads/mcp_images/img_xxx_1.jpg", "title": "..."}
  ]
}
```

### 2. 直接下載圖片

```
GET /download?url=https://example.com/image.jpg
GET /batch-download?urls=url1,url2&prefix=taipei
```

### 3. 視覺化人工選圖

```
POST /search
Body: {"query": "關鍵詞", "engine": "bing"}
```

打開 Chrome 讓人類視覺選圖，右鍵保存到 `~/Downloads/mcp_images/`

### 4. 列出已下載圖片

```
GET /list
```

## 架構

```
Python FastAPI (server.py)          Node.js Playwright (scraper.js)
       ↑                                    ↑
   HTTP API  ←── spawn subprocess ──→  headless Chromium
       ↑
AI Agent (Hermes)
       ↓
  /scrape → 本地路徑 → post_ig_human.py → Instagram 發文
```

- `server.py` — FastAPI HTTP API（Python）
- `scraper.js` — Node.js Playwright 無頭爬蟲
- `post_ig.js` — Instagram CDP 自動化發文（browser hijack 模式）

## 完整工作流：Google Trends → 圖片 → Caption → IG 發文

```python
# 1. 抓 Google Trends 關鍵字
# 2. 用 /scrape 自動下載圖片
# 3. Gemini 生成 caption
# 4. post_ig_human.py 發布到 IG

import asyncio
from social_mcp.post_ig_human import post_ig_human

caption = """文章內容... #標籤1 #標籤2"""

result = asyncio.run(post_ig_human(caption, "/path/to/image.jpg"))
# → "✅ Instagram 發文成功"
```

詳見：[ai-cdp-browser](https://github.com/whypuss/ai-cdp-browser) — 包含 `post_ig_human.py` + `social_workflow_3source.py`

## API 總覽

| 方法 | 路徑 | 說明 |
|------|------|------|
| `GET` | `/` | Server 狀態 |
| `POST` | `/search` | 開 Chrome 視覺選圖 |
| `GET` | `/scrape` | Playwright 全自動爬蟲 |
| `GET` | `/download` | 下載單張圖片 |
| `GET` | `/batch-download` | 批量下載 |
| `GET` | `/list` | 列出已下載圖片 |

## 為什麼用 Playwright（Node.js）而不是 Python？

Python requests/urllib 無法處理 JS 渲染的 Bing/Google 圖片頁面（響應是空的或 base64 嵌入）。Node.js Playwright 直接執行 JS，提取真實圖片 URL。

## 安裝依賴

```bash
cd ~/human-mcp
npm install
pip install fastapi uvicorn
```
