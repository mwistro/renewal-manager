'use strict';
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      const names = {'/': ['index.html', 'text/html'], '/styles.css': ['styles.css', 'text/css'], '/app.js': ['app.js', 'text/javascript'], '/logic.js': ['logic.js', 'text/javascript']};
      const file = url.origin === 'http://localhost' && names[url.pathname];
      if (!file) return route.abort();
      return route.fulfill({contentType: file[1], body: fs.readFileSync(path.join(root, 'dist', file[0]))});
    });
    await page.goto('http://localhost/');
    await page.locator('#sample').click();
    assert.equal((await page.locator('#total').innerText()).replace(/\s/g, ' '), '592,92 €');
    await page.locator('#basis').selectOption('total');
    assert.equal((await page.locator('#total').innerText()).replace(/\s/g, ' '), '197,64 €');
    await page.frameLocator('#preview').getByText('197,64 €', {exact: true}).waitFor();
    const preview = await page.frameLocator('#preview').locator('body').innerText();
    assert.ok(!preview.includes('178,59'));
    fs.mkdirSync(path.join(root, 'docs'), {recursive: true});
    await page.screenshot({path: path.join(root, 'docs/demo.png')});
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#download').click();
    const download = await downloadPromise;
    const eml = fs.readFileSync(await download.path(), 'utf8');
    assert.ok(eml.includes('X-Unsent: 1'));
    assert.ok(eml.includes('To: cliente@example.com'));
    assert.equal(download.suggestedFilename(), 'propuesta-renovacion.eml');
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    console.log('Demo verified: synthetic sample, pricing, private cost exclusion, EML export and mobile layout.');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
