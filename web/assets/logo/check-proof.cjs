'use strict';
// Isolated review check; never starts Chuddy or edits the source character image.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { pathToFileURL } = require('node:url');
const { chromium } = require('playwright');

(async () => {
  const folder = __dirname;
  const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'manifest.json'), 'utf8'));
  const sheet = path.resolve(folder, manifest.proof.reference);
  assert.equal(manifest.production_ready, false);
  assert.equal(manifest.production.path, null);
  assert.equal(manifest.groups.join(''), 'chuddy');
  assert.equal(manifest.letter_sequence.join('').toUpperCase(), 'CHUDDY');
  assert.equal(crypto.createHash('sha256').update(fs.readFileSync(sheet)).digest('hex'), manifest.proof.reference_sha256);
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ deviceScaleFactor: 1 });
    const errors = [];
    const external = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (!request.url().startsWith('file:')) external.push(request.url()); });
    for (const width of [1100, 390]) {
      await page.setViewportSize({ width, height: 1100 });
      await page.goto(pathToFileURL(path.join(folder, 'layout-proof.html')).href);
      const result = await page.evaluate(() => {
        const image = document.querySelector('.reference-frame img');
        const frame = document.querySelector('.reference-frame').getBoundingClientRect();
        return {
          text: document.querySelector('.letters').textContent,
          loaded: image.complete && image.naturalWidth === 1254 && image.naturalHeight === 1254,
          ratio: frame.width / frame.height,
          overflow: document.documentElement.scrollWidth > innerWidth,
          scripts: document.scripts.length,
          notice: document.querySelector('.notice').textContent
        };
      });
      assert.equal(result.text, 'chuddy');
      assert.equal(result.loaded, true);
      assert.ok(Math.abs(result.ratio - 418 / 627) < .001);
      assert.equal(result.overflow, false);
      assert.equal(result.scripts, 0);
      assert.match(result.notice, /Requested illustration pending/);
      if (width === 1100) await page.screenshot({ path: path.join(folder, 'layout-proof-review.png'), fullPage: true });
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log('PASS: pending state, CHUDDY spelling, original raster digest, local image loading, desktop/mobile layout, no scripts/external requests.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
