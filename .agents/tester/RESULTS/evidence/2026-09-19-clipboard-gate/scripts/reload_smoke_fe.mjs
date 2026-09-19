// RELOAD SMOKE #40 — FE reload-render legs (a), (b), (c) — final pass.
// Each leg: fresh context (reload semantics), bounded reload-retry to ride the
// known deep-link mount flake, then scan [data-testid="message-image"].
import { chromium } from '/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-clipboard-img/frontend/node_modules/playwright/index.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const ROOT = '/Users/nguyenminmhka_ignore';
const BASE = '/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-clipboard-img';
const EV = path.join(BASE, 'data-gate-main', 'scripts', 'evidence', 'reload-smoke');
await mkdir(EV, { recursive: true });

const LEGS = [
  { key: 'a', id: '7b011705-4ca0-4f3e-a84a-33d2c6a07e21' },
  { key: 'b', id: '0621b9f8-cab8-4945-80d6-23e4c7fcd85b' },
  { key: 'c', id: '10512653-5c45-4104-ad62-3f01f469ed89' },
];

const browser = await chromium.launch({ headless: true });
const report = {};

async function openChat(page) {
  const url = page.__url;
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  for (let s = 0; s < 12; s += 2) {
    await page.waitForTimeout(2000);
    if (await page.evaluate(() => !!document.querySelector('app-chat-interface'))) return { mounted: true, reloads: 0 };
  }
  for (let r = 1; r <= 3; r++) {
    await page.reload({ waitUntil: 'domcontentloaded' });
    for (let s = 0; s < 12; s += 2) {
      await page.waitForTimeout(2000);
      if (await page.evaluate(() => !!document.querySelector('app-chat-interface'))) return { mounted: true, reloads: r };
    }
  }
  return { mounted: false, reloads: 3 };
}

for (const leg of LEGS) {
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await context.newPage();
  page.__url = `http://127.0.0.1:4199/projects/all/instances/${leg.id}`;
  const entry = { url: page.__url };
  try {
    const mount = await openChat(page);
    entry.mount = mount;
    if (!mount.mounted) throw new Error('chat overlay never mounted after 3 reloads');
    await page.waitForTimeout(3000); // message fetch + render settle

    entry.scan = await page.evaluate(() => {
      const imgs = [...document.querySelectorAll('img[data-testid="message-image"]')];
      const out = {
        images: imgs.map(img => ({
          src: (img.getAttribute('src') || '').slice(0, 100),
          naturalWidth: img.naturalWidth,
          complete: img.complete,
          failedClass: img.classList.contains('message-image-failed'),
          dataImageIndex: img.getAttribute('data-image-index'),
        })),
        // does ANY rendered user bubble text contain the injected message text?
        hasInjectedText: document.body.textContent.includes('also consider this image'),
        hasStoryText: document.body.textContent.includes('Write a detailed 600-word story'),
        hasWhatDoYouSee: document.body.textContent.includes('what do you see?'),
        hasDescribeWake: document.body.textContent.includes('describe this when you wake'),
      };
      return out;
    });
    entry.has_tmp_images_thumb = entry.scan.images.some(i => i.src.startsWith('/api/tmp_images/'));
    entry.tmp_thumb_loaded = entry.scan.images.some(i => i.src.startsWith('/api/tmp_images/') && i.naturalWidth > 0);
    entry.has_datauri_img = entry.scan.images.some(i => i.src.startsWith('data:image/'));
    entry.datauri_loaded = entry.scan.images.some(i => i.src.startsWith('data:image/') && i.naturalWidth > 0);
    const shot = `leg-${leg.key}-${leg.id.slice(0, 8)}.png`;
    await page.screenshot({ path: path.join(EV, shot), fullPage: false });
    entry.screenshot = shot;
    entry.status = 'scanned';
  } catch (e) {
    entry.status = 'error';
    entry.error = e.message;
  }
  report[`leg_${leg.key}`] = entry;
  await context.close();
}

await browser.close();
await writeFile(path.join(EV, 'reload-smoke-report.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
