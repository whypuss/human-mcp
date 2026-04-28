# Human MCP

**AI Agent 全自動圖片搜尋 + 社群發文工具。**

從 Google Trends / 微博熱搜抓關鍵字 → Playwright 無頭下載圖片 → Gemini 生成 caption → 自動發布到 Facebook + Threads + Instagram。全流程無需人工干預。

## 核心變化（與舊版 CDP 模式的區別）

**舊版**：需要提前打開 Chrome + 開啟 remote debugging port，腳本透過 CDP (`localhost:9333`) 控制同一個瀏覽器。缺點：用戶正常瀏覽受影響、CDP port 衝突、tab 太多崩潰。

**新版**：每個腳本啟動**獨立 Chromium**，放在專用 profile 目錄，互不干擾。
- 不影響用戶正常 Chrome
- 不需要 CDP port、`.cdp_port` 文件
- 不需要提前打開瀏覽器
- 第一次手動登入後，profile 自動記住 session

| 腳本 | Chromium Profile 目錄 | 用途 |
|------|----------------------|------|
| `post_facebook.py` | `/tmp/fb-chromium-profile/` | Facebook 圖文發文 |
| `post_threads.py` | `/tmp/threads-chromium-profile/` | Threads 圖文發文 |
| `post_ig_human.py` | `/tmp/ig-chromium-profile/` | Instagram 圖文發文 |

## 快速開始

```bash
cd ~/human-mcp
uv run python server.py
```

**第一次使用（每個平台只需一次）**：直接運行發文腳本，會彈出 Chromium 視窗，平常手動登入對應平台，之後腳本會記住登入狀態。

```bash
# 測試 Threads 發文（需要預先有一張測試圖）
python3 post_threads.py "測試發文" "/path/to/image.jpg"

# 測試 Instagram 發文
python3 post_ig_human.py "測試發文" "/path/to/image.jpg"

# 測試 Facebook 發文
python3 post_facebook.py "測試發文" "/path/to/image.jpg"
```

## 完整工作流

```bash
# 來源 1：微博熱搜 → FB + Threads + IG
python3 social_workflow.py 1

# 來源 2：Google Trends HK → FB + Threads + IG
python3 social_workflow.py 2

# 來源 3：Google Trends US → FB + Threads + IG
python3 social_workflow.py 3
```

流程：Trends 抓 topic → Playwright 下載圖片 → Gemini 生成 caption → FB → Threads → IG

## API（圖片下載）

Server 運行在 `http://localhost:8080`

| 方法 | 路徑 | 說明 |
|------|------|------|
| `GET` | `/scrape?query=關鍵字&engine=bing&max_images=6` | Playwright 無頭爬蟲，自動下載圖片到本地 |
| `GET` | `/download?url=https://...` | 下載單張圖片 |
| `GET` | `/batch-download?urls=url1,url2` | 批量下載 |
| `GET` | `/list` | 列出已下載圖片 |

```bash
curl "http://localhost:8080/scrape?query=jimmy+kimmel+melania+trump&engine=bing&max_images=3"
```

## 架構

```
Google Trends (US/HK) / 微博熱搜
       ↓
human-mcp /scrape (Playwright 無頭下載圖片)
       ↓
Gemini 生成 caption
       ↓
post_facebook.py ──→ Facebook ✅
post_threads.py  ──→ Threads ✅
post_ig_human.py ──→ Instagram ✅
```

每個發文腳本各自有獨立 Chromium Profile，不共用、不衝突。

## 安裝依賴

```bash
cd ~/human-mcp
npm install
pip install fastapi uvicorn
```
