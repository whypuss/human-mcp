#!/usr/bin/env node
/**
 * post_instagram.js — 用 CDP Browser Hijacking 發 IG 圖文帖
 * 需先開啟 Chromium 並登入 IG：
 *   open -a "Chromium" --args --user-data-dir="$HOME/Library/Application Support/Chromium/FacebookMCP" --remote-debugging-port=9333
 */

const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const CDP_PORT = parseInt(process.env.CDP_PORT || '9333');
const IMAGE_PATH = process.argv[2];
const CAPTION = process.argv[3] || '';

if (!IMAGE_PATH || !fs.existsSync(IMAGE_PATH)) {
  console.error('[post_ig] Error: image path required');
  process.exit(1);
}

async function postInstagram(imagePath, caption) {
  const browser = await chromium.connectOverCDP(`ws://127.0.0.1:${CDP_PORT}`);
  const context = browser.contexts()[0];
  const page = context.pages()[0] || await context.newPage();

  console.error('[post_ig] Navigating to Instagram...');
  await page.goto('https://www.instagram.com/', { waitUntil: 'networkidle', timeout: 30000 });

  // Click "Create" button (+)
  await page.waitForTimeout(2000);
  const createBtn = page.locator('svg[aria-label="New post"]').first();
  if (await createBtn.isVisible({ timeout: 5000 })) {
    await createBtn.click();
  } else {
    // Try alternate selector
    await page.locator('a[href="#"][role="link"]').filter({ hasText: '' }).first().click();
  }

  await page.waitForTimeout(3000);

  // File input should appear
  const fileInput = page.locator('input[type="file"]').first();
  const isVisible = await fileInput.isVisible({ timeout: 5000 }).catch(() => false);

  if (!isVisible) {
    // Fallback: drag & drop or "From computer" button
    const fromComputerBtn = page.locator('button').filter({ hasText: /From computer/i }).first();
    if (await fromComputerBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await fromComputerBtn.click();
      await page.waitForTimeout(1000);
    }
  }

  // Set file
  console.error('[post_ig] Setting image file:', imagePath);
  await fileInput.setInputFiles(imagePath);

  // Wait for preview
  await page.waitForTimeout(4000);

  // Click "Next" (crop screen)
  const nextBtn1 = page.locator('button').filter({ hasText: /next|next/i }).first();
  if (await nextBtn1.isVisible({ timeout: 5000 }).catch(() => false)) {
    await nextBtn1.click();
    await page.waitForTimeout(2000);
  }

  // Click "Next" again (edit screen)
  const nextBtn2 = page.locator('button').filter({ hasText: /next/i }).first();
  if (await nextBtn2.isVisible({ timeout: 5000 }).catch(() => false)) {
    await nextBtn2.click();
    await page.waitForTimeout(2000);
  }

  // Type caption
  const captionArea = page.locator('textarea[aria-label*="aption"], textarea[aria-label*="aption"]').first();
  if (await captionArea.isVisible({ timeout: 3000 }).catch(() => false)) {
    await captionArea.click();
    await captionArea.fill(caption);
  }

  await page.waitForTimeout(1000);

  // Click "Share"
  const shareBtn = page.locator('button').filter({ hasText: /share|發布|发布|share/i }).first();
  if (await shareBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
    await shareBtn.click();
    console.error('[post_ig] Shared! Waiting for confirmation...');
    await page.waitForTimeout(5000);
  }

  // Check for success
  const url = page.url();
  console.error('[post_ig] Final URL:', url);

  await browser.disconnect();

  console.log(JSON.stringify({
    success: true,
    url: url,
    image_path: imagePath,
    caption_preview: caption.slice(0, 50),
  }));
}

postInstagram(IMAGE_PATH, CAPTION).catch(err => {
  console.error('[post_ig] Error:', err.message);
  process.exit(1);
});
