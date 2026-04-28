# Human MCP

**AI Agent 全自動圖片搜尋 + 社群發文工具。**

從 Google Trends / 微博熱搜抓關鍵字 → Playwright 無頭下載圖片 → Gemini 生成 caption → 自動發布到 Facebook + Threads + Instagram。全流程無需人工干預。

## 解決問題

- Bing/Google Images 有 captcha，傳統 HTTP 爬蟲失效
- 圖片防盜鏈（403/簽名 URL）
- AI Agent 需要本地圖片路徑才能上傳社群平台
- Google Trends 熱搜榜用 shadow DOM，傳統 selector 抓不到
- Instagram OS file dialog 在 CDP mode 無法关闭，圖片上傳改用 JS DataTransfer

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

使用 Playwright 無頭渲染 Bing，自動下載圖片到 `~/Downloads/mcp_images/`，返回本地路徑陣列。

```bash
curl "http://localhost:8080/scrape?query=jimmy+kimmel+melania+trump&engine=bing&max_images=3"
```

```json
{
  "query": "jimmy kimmel melania trump",
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

## 完整工作流

### social_workflow.py（3 來源全自動發文）

```bash
# 來源 1：微博熱搜 → FB + Threads + IG
python3 social_workflow.py 1

# 來源 2：Google Trends HK → FB + Threads + IG
python3 social_workflow.py 2

# 來源 3：Google Trends US → FB + Threads + IG
python3 social_workflow.py 3
```

流程：Trends 抓 topic → /scrape 下載圖片 → Gemini 生成 caption → FB → Threads → Instagram

### 手動發文腳本

```bash
# Facebook
python3 post_facebook.py "Caption 文字..." "/path/to/image.jpg"

# Threads（需先開啟 Threads tab）
python3 post_threads.py "Caption 文字..." "/path/to/image.jpg"

# Instagram（需先開啟 IG 首頁）
python3 post_ig_human.py "Caption 文字..." "/path/to/image.jpg"
```

## 腳本亮點

### Facebook（post_facebook.py）

- **DataTransfer API** — 圖片 base64 → Blob → File → DataTransfer，繞過 React input.files 限制
- **execCommand("insertText")** — 打字進 contenteditable（React 生態兼容性）
- **CDP JS innerText 匹配** — 自動點擊「在想什麼」composer、「下一頁」、「發佈」
- **復用同一瀏覽器 session** — CDP connect_over_cdp，不啟動新瀏覽器
- ~300 行，只用 playwright.async_api

### Threads（post_threads.py）

- **兩步發文流程**：「新增到串文」→「發佈」，Threads API 特性
- **keyboard.type()** — 擬人打字速度（40-80ms/字），避免被判定機器人
- **reload 驗證** — 發佈後 reload 頁面確認內容存在
- ~380 行，只用 playwright.async_api

### Instagram（post_ig_human.py）

- **JS DataTransfer 注入** — CDP mode 無法拦截 OS file chooser，改用 JS DataTransfer 直接寫入 input.files
- **三步 Next** — 裁切頁 → 濾鏡頁 → Caption 頁，全部用 aria-label 定位
- **隨機 human delay** — 模拟真實點擊節奏，避免被判定機器人
- ~500 行，只用 playwright.async_api

### social_workflow.py（全自動工作流）

- **inner_text() 讀 shadow DOM** — Google Trends / 微博熱搜榜用 shadow DOM 封裝，改用 page.inner_text() 繞過
- **asyncio.wait_for** — Python 3.9 兼容性（不用 asyncio.timeout）
- **Caption fallback** — Gemini 只回關鍵詞時自動填入默認正文
- ~500 行

## 架構

```
Google Trends (US/HK) / 微博熱搜（關鍵字）
       ↓
human-mcp /scrape (自動下載圖片)
       ↓
Gemini 生成 caption（本地 browser）
       ↓
post_facebook.py ──→ Facebook 發文 ✅
post_threads.py ──→ Threads 發文 ✅
post_ig_human.py ──→ Instagram 發文 ✅
```

```
Python FastAPI (server.py)          Node.js Playwright (scraper.js)
       ↑                                    ↑
   HTTP API  ←── spawn subprocess ──→  headless Chromium
       ↑
AI Agent (Hermes) / social_workflow.py
```

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

## 文件清單

| 檔案 | 用途 |
|------|------|
| `server.py` | FastAPI HTTP API，含 CDP port 追蹤 |
| `scraper.js` | Node.js Playwright 無頭爬蟲 |
| `post_facebook.py` | Facebook 圖文發文（~300 行，純 Playwright CDP） |
| `post_threads.py` | Threads 圖文發文（~380 行，純 Playwright CDP） |
| `post_ig_human.py` | Instagram 圖文發文（~500 行，純 Playwright CDP） |
| `social_workflow.py` | 三來源全自動發文工作流（~500 行） |
