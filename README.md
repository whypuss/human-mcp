# Human MCP

**AI Agent 全自動圖片搜尋 + 社群發文工具。**

從 Google Trends / 微博熱搜抓關鍵字 → Playwright 無頭下載圖片 → Gemini 生成 caption → 自動發布到 Facebook + Threads + Instagram。

## 核心架構

### 獨立 Chromium Profile

每個平台使用**獨立的 Chromium Profile**，不影響用戶正常瀏覽器：
- `post_facebook.py` → `/tmp/fb-chromium-profile/`
- `post_threads.py` → `/tmp/threads-chromium-profile/`
- `post_ig_human.py` → `/tmp/ig-chromium-profile/`

第一次需要手動登入一次，之後自動記住 session。

### 語義點擊架構（Semantic Clicking）

舊版問題：DOM 結構變了（按鈕換位置/換顏色）→ 座標點擊失敗。

新版做法：從「座標導向」轉向「語義視覺導向」：

```
DOM 定位（getByRole / getByText）
       ↓ 找不到
視覺定位（browser_vision VLM）
       ↓
點擊 + 結果驗證（verify_after 關鍵字）
       ↓ 錯了（按鈕還在）
自動重試（最多 3 次）→ Self-Correction Loop
```

每個點擊步驟都有驗證邏輯，IG/Threads UI 改版後依然有自癒能力。

## 快速開始

```bash
# 第一次（每個平台只需一次）
python3 post_threads.py "測試發文" "/path/to/image.jpg"
# → 彈出 Chromium 視窗 → 手動登入 Threads

python3 post_ig_human.py "測試發文" "/path/to/image.jpg"
# → 彈出 Chromium 視窗 → 手動登入 IG

python3 post_facebook.py "測試發文" "/path/to/image.jpg"
# → 彈出 Chromium 視窗 → 手動登入 FB
```

## 完整工作流

```bash
python3 social_workflow.py 1  # 微博熱搜 → FB + Threads + IG
python3 social_workflow.py 2  # Google Trends HK
python3 social_workflow.py 3  # Google Trends US
```

流程：Trends 抓 topic → Playwright 下載圖片 → Gemini caption → 發文

## API（圖片下載）

```bash
# 啟動 server
uv run python server.py

# 爬蟲下載圖片（自動存本地）
curl "http://localhost:8080/scrape?query=jimmy+kimmel&engine=bing&max_images=3"

# 下載單張
curl "http://localhost:8080/download?url=https://..."
```

## 發文腳本亮點

| 功能 | 說明 |
|------|------|
| 獨立 Profile | 互不干擾，不影響用戶正常 Chrome |
| 語義點擊 | getByRole/getByText，UI 變了也能找到 |
| 自癒重試 | 點擊後驗證，錯了自動重試（最多 3 次） |
| JS DataTransfer | 圖片 base64 → Blob → File，繞過 React input.files |
| 擬人打字 | keyboard.type 隨機延遲 40-80ms/字 |
| 兩步發文 | Threads:「新增到串文」→「發佈」 |

## 安裝

```bash
cd ~/human-mcp
npm install
pip install fastapi uvicorn
```
