#!/usr/bin/env node
/**
 * human-mcp scraper — Playwright headless 圖片搜索
 * 用法: node scraper.js "關鍵字" [數量]
 * 返回: JSON { query, images: [{url, thumb, title}], paths: [local_path] }
 */

const { chromium } = require('playwright');
const https = require('https');
const http = require('http');
const { URL } = require('url');
const path = require('path');
const fs = require('fs');

const SAVE_DIR = path.join(process.env.HOME, 'Downloads', 'mcp_images');
const MAX_IMAGES = parseInt(process.argv[2] || '6');
const QUERY = process.argv[3] || 'cat';
const ENGINE = (process.argv[4] || 'bing').toLowerCase();

if (!fs.existsSync(SAVE_DIR)) {
  fs.mkdirSync(SAVE_DIR, { recursive: true });
}

async function downloadFile(url, filepath) {
  return new Promise((resolve, reject) => {
    const parsedUrl = new URL(url);
    const client = parsedUrl.protocol === 'https:' ? https : http;
    const ext = path.extname(parsedUrl.pathname).split('?')[0].toLowerCase();
    const finalPath = ext.match(/\.(jpg|jpeg|png|webp|gif|bmp)$/) 
      ? filepath 
      : filepath.replace(/\.[^.]+$/, '.jpg');

    const req = client.get(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
        'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
        'Referer': 'https://www.bing.com/',
      }
    }, (response) => {
      if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
        // Follow redirect
        downloadFile(response.headers.location, finalPath).then(resolve).catch(reject);
        return;
      }
      if (response.statusCode !== 200) {
        reject(new Error(`HTTP ${response.statusCode}`));
        return;
      }
      const stream = fs.createWriteStream(finalPath);
      response.pipe(stream);
      stream.on('finish', () => resolve(finalPath));
      stream.on('error', reject);
    });
    req.on('error', reject);
    req.setTimeout(15000, () => { req.destroy(); reject(new Error('Timeout')); });
  });
}

async function scrape(engine, query, maxImages) {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  // Set a realistic viewport
  await page.setViewportSize({ width: 1280, height: 800 });

  let searchUrl;
  if (engine === 'google') {
    searchUrl = `https://www.google.com/search?tbm=isch&q=${encodeURIComponent(query)}`;
  } else {
    searchUrl = `https://www.bing.com/images/search?q=${encodeURIComponent(query)}&first=0`;
  }

  console.error(`[scraper] Navigating to ${searchUrl}`);
  await page.goto(searchUrl, { waitUntil: 'networkidle', timeout: 20000 });

  // Wait a bit for lazy-loaded images
  await page.waitForTimeout(2000);

  // Extract image URLs using engine-specific selectors
  let imageUrls = [];

  if (engine === 'bing') {
    // Bing: extract from m="..." JSON attributes
    imageUrls = await page.evaluate((max) => {
      const results = [];
      const items = document.querySelectorAll('[m]');
      for (const el of items) {
        try {
          const m = el.getAttribute('m');
          if (!m || m === 'false') continue;
          const decoded = decodeURIComponent(m);
          const data = JSON.parse(decoded);
          const url = data.murl || data.turl || data.iurl;
          if (url && url.startsWith('http') && !url.includes('data:image') && !url.includes('blob:')) {
            results.push({
              url,
              thumb: data.turl || url,
              title: data.t || data.tt || '',
            });
          }
        } catch(e) {}
        if (results.length >= max) break;
      }
      // Fallback: look for image elements with data-src
      if (results.length === 0) {
        const imgs = document.querySelectorAll('img[data-src]');
        for (const img of imgs) {
          const url = img.getAttribute('data-src');
          if (url && url.startsWith('http') && !url.includes('data:image')) {
            results.push({ url, thumb: url, title: img.alt || '' });
          }
          if (results.length >= max) break;
        }
      }
      return results;
    }, maxImages);
  } else {
    // Google: extract from image elements
    imageUrls = await page.evaluate((max) => {
      const results = [];
      // Google tbm=isch uses lazy-loaded images
      // Look for AF_initDataCallback data or JSON-LD
      const scripts = document.querySelectorAll('script');
      for (const script of scripts) {
        const text = script.textContent || '';
        // Match array of image URLs
        const matches = text.matchAll(/\["(https?:\/\/[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"/gi);
        for (const m of matches) {
          const url = m[1];
          if (!results.find(r => r.url === url)) {
            results.push({ url, thumb: url, title: '' });
          }
          if (results.length >= max) break;
        }
        if (results.length >= max) break;
      }
      // Fallback: img with data-src or src
      if (results.length === 0) {
        const imgs = document.querySelectorAll('img[src^="http"]');
        for (const img of imgs) {
          const src = img.src;
          if (src && !src.includes('gstatic.com') && !src.includes('data:image') && !src.includes('google.com/images')) {
            results.push({ url: src, thumb: src, title: img.alt || '' });
          }
          if (results.length >= max) break;
        }
      }
      return results;
    }, maxImages);
  }

  console.error(`[scraper] Found ${imageUrls.length} image URLs`);
  await browser.close();

  // Download images
  const timestamp = Date.now();
  const downloaded = [];

  for (let i = 0; i < imageUrls.length; i++) {
    const img = imageUrls[i];
    const filename = `img_${timestamp}_${i}.jpg`;
    const filepath = path.join(SAVE_DIR, filename);
    try {
      const saved = await downloadFile(img.url, filepath);
      downloaded.push({
        index: i,
        url: img.url,
        local_path: saved,
        title: img.title,
      });
      console.error(`[scraper] Downloaded ${i + 1}/${imageUrls.length}: ${filename}`);
    } catch(e) {
      console.error(`[scraper] Failed ${img.url.slice(0, 60)}: ${e.message}`);
    }
  }

  return {
    query,
    engine,
    found: imageUrls.length,
    downloaded: downloaded.length,
    save_dir: SAVE_DIR,
    images: downloaded,
  };
}

scrape(ENGINE, QUERY, MAX_IMAGES)
  .then(result => {
    console.log(JSON.stringify(result));
  })
  .catch(err => {
    console.error('[scraper] Error:', err.message);
    process.exit(1);
  });
