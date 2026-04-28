# Human MCP

**AI Agent 全自動圖片搜尋 + 社群發文工具。**

從 Google Trends 抓關鍵字 → Playwright 無頭下載圖片 → AI 生成 caption → 自動發布到 Facebook/Instagram。全流程無需人工干預。

## 解決問題

- Bing/Google Images 有 captcha，傳統 HTTP 爬蟲失效
- 圖片防盜鏈（403/簽名 URL）
- AI Agent 需要本地圖片路徑才能上傳社群平台
- 圖片上傳繞過 React input.files 限制

## 啟動

```bash
cd ~/human-mcp
uv run python server.py
# 後台運行：
nohup uv run python server.py > /tmp/human-mcp.log 2>&1 &
```

Server 運行在 `http://localhost:8080`

## 核心功能

### 1. 全自動圖片爬蟲（無需人工）

```
GET /scrape?query=關鍵字&engine=bing&max_images=6
```

使用 Playwright 無頭渲染 Bing/Google，自動下載圖片到 `~/Downloads/mcp_images/`，返回本地路徑陣列。

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

### 3. 列出已下載圖片

```
GET /list
```

### 4. CDP Port 追蹤（供其他工具使用）

```
GET /cdp-port?port=9333   # 寫入並返回 CDP port
GET /active-cdp-port       # 讀取當前 CDP port
```

`post_facebook.py` 等工具透過 `~/.cdp_port` 文件讀取當前 active CDP port，復用同一瀏覽器 session。

## 架構

```
Google Trends (關鍵字)
       ↓
human-mcp /scrape (自動下載圖片)
       ↓
AI 生成 caption (本地 LLM)
       ↓
post_facebook.py (CDP 自動發文)
       ↓
Facebook 發文成功 ✅
```

```
Python FastAPI (server.py)          Node.js Playwright (scraper.js)
       ↑                                    ↑
   HTTP API  ←── spawn subprocess ──→  headless Chromium
       ↑
AI Agent (Hermes)
       ↓
  /scrape → 本地路徑 → post_facebook.py → Facebook 發文
                           post_ig_human.py → Instagram 發文
```

- `server.py` — FastAPI HTTP API，含 CDP port 追蹤
- `scraper.js` — Node.js Playwright 無頭爬蟲
- `post_ig_human.py` — Instagram CDP 自動化發文
- `post_facebook.py` — Facebook 圖文發文（~300 行，純 Playwright CDP）

## 完整工作流：Google Trends → 圖片 → Caption → FB 發文

```bash
# Step 1: 抓 Google Trends 關鍵字（browser_navigate）
# Step 2: 用 /scrape 自動下載圖片
curl "http://localhost:8080/scrape?query=jimmy+kimmel+melania+trump&engine=bing&max_images=3"
# → 返回本地圖片路徑

# Step 3: AI 生成 caption（用本地 LLM）

# Step 4: post_facebook.py 發布
python3 post_facebook.py "Caption 文字..." "/path/to/image.jpg" 9333
# → ✅ Facebook 發文成功
```

## Facebook 發文腳本亮點

- **DataTransfer API** — 圖片 base64 → Blob → File → DataTransfer，繞過 React input.files 限制
- **execCommand("insertText")** — 打字進 contenteditable（React 生態兼容性）
- **CDP JS innerText 匹配** — 自動點擊「在想什麼」composer、「下一頁」、「發佈」
- **復用同一瀏覽器 session** — CDP connect_over_cdp，不啟動新瀏覽器
- **~300 行，無外部依賴** — 只用 playwright.async_api

## API 總覽

| 方法 | 路徑 | 說明 |
|------|------|------|
| `GET` | `/` | Server 狀態 |
| `GET` | `/scrape` | Playwright 全自動爬蟲（自動下載圖片到本地） |
| `GET` | `/download` | 下載單張圖片 |
| `GET` | `/batch-download` | 批量下載 |
| `GET` | `/list` | 列出已下載圖片 |
| `GET` | `/cdp-port` | 寫入並返回 CDP port |
| `GET` | `/active-cdp-port` | 讀取當前 CDP port |

## 為什麼用 Playwright（Node.js）而不是 Python？

Python requests/urllib 無法處理 JS 渲染的 Bing/Google 圖片頁面（響應是空的或 base64 嵌入）。Node.js Playwright 直接執行 JS，提取真實圖片 URL。

## 安裝依賴

```bash
cd ~/human-mcp
npm install
pip install fastapi uvicorn
```
