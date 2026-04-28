# Human MCP

**AI Agent 全自動圖片搜尋 + 社群發文工具。**

從 Google Trends / 微博熱搜抓關鍵字 → Playwright 無頭下載圖片 → Gemini 生成 caption → 自動發布到 Facebook + Threads + Instagram。

---

## 核心架構：語義點擊（Semantic Clicking）

### 解決什麼問題？

傳統 RPA 腳本用座標點擊 `click(500, 300)`，按鈕一換位置/顏色/樣式就掛。

Human MCP 的做法：**從「座標導向」轉向「語義視覺導向」**。

### 三層自癒機制

```
┌─────────────────────────────────────────────────────┐
│  Layer 1: DOM 定位（getByRole / getByText）        │
│  → UI 變了？只要 role + label 還在就能找到         │
│         ↓ 找不到                                   │
│  Layer 2: JS dispatchEvent（React 兼容性 fallback）│
│         ↓ 點了沒反應                                │
│  Layer 3: 結果驗證 + 重試（Self-Correction Loop）  │
│  → verify_after 關鍵字，錯了自動重試最多 3 次      │
└─────────────────────────────────────────────────────┘
```

每個點擊步驟都有驗證 + 重試，IG/Threads UI 改版後依然有效。

```python
# 核心：SemanticClicker
ok = await clicker.click(
    label="發佈",
    role="button",
    parent="dialog",
    verify_after="已發佈",  # 點完驗證關鍵字
    max_retries=3,          # 錯了自動重試
)
```

### 為何不用 CDP Vision？

CDP（Remote Debugging Protocol）靠 DOM 查座標，UI 變了就掛。語義點擊靠 `getByRole` + `getByText`，是 DOM 層的語義標籤，比座標穩定得多。Vision 僅作為最後 fallback 預留。

---

## 獨立 Chromium Profile

每個平台使用**獨立 Chromium Profile**，不影響用戶正常瀏覽器：

| 腳本 | Profile 目錄 | 用途 |
|------|-------------|------|
| `post_threads.py` | `/tmp/threads-chromium-profile/` | Threads 圖文發文 |
| `post_ig_human.py` | `/tmp/ig-chromium-profile/` | Instagram 圖文發文 |
| `post_facebook.py` | `/tmp/fb-chromium-profile/` | Facebook 圖文發文 |

第一次需手動登入一次，之後自動記住 session。

---

## 快速開始

```bash
# 第一次（每個平台只需一次）
python3 post_threads.py "測試發文" "/path/to/image.jpg"
python3 post_ig_human.py "測試發文" "/path/to/image.jpg"
python3 post_facebook.py "測試發文" "/path/to/image.jpg"

# 完整工作流
python3 social_workflow.py 1   # 微博熱搜 → FB + Threads + IG
python3 social_workflow.py 2   # Google Trends HK
python3 social_workflow.py 3   # Google Trends US
```

## API（圖片下載）

```bash
uv run python server.py   # 啟動 server

curl "http://localhost:8080/scrape?query=jimmy+kimmel&engine=bing&max_images=3"
curl "http://localhost:8080/download?url=https://..."
```

## 安裝

```bash
cd ~/human-mcp
npm install
pip install fastapi uvicorn
```
