# Human MCP

打開瀏覽器搜圖，本地保存，人工選圖後直接上傳。

## 解決問題

Google Images captcha 導致自動化搜圖失效。用人工取代 captcha。

## 啟動

```bash
cd ~/human-mcp
uv run python server.py
```

## 流程

1. `POST /search` → Chrome 自動打開 Bing/Google 圖片搜尋
2. 右鍵保存圖片到 `~/Downloads/mcp_images/`
3. 拿本地路徑直接上傳（`post_instagram(image_path=本地路徑)`）

## API

```
POST http://localhost:8080/search
Body: {"query": "關鍵詞", "engine": "bing"}

Response:
{
  "success": true,
  "url": "https://www.bing.com/images/search?q=...",
  "engine": "bing",
  "save_dir": "/Users/xxx/Downloads/mcp_images",
  "instruction": "Right-click image → Save As → save to ~/Downloads/mcp_images/"
}
```

## 為什麼比自動下載好

- 不用處理 captcha
- 不用處理防盜鏈（URL 失效）
- 用戶確認過圖片內容才上傳
- 流程簡單，沒有中間環節
