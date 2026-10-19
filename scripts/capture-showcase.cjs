'use strict';
// Real application screens, exclusively synthetic data and an intercepted API.
// All unrecognized requests are blocked; no login or email is sent.
const {chromium} = require('playwright');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'docs/screenshots');
const fixture = JSON.parse(execFileSync(process.env.RENOVACIONES_PYTHON || 'python',
  [path.join(__dirname, 'showcase-fixture.py')],
  {encoding: 'utf8', env: {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'}}));

(async () => {
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1600, height: 1120},
      locale: 'es-ES', timezoneId: 'Europe/Madrid', deviceScaleFactor: 1});
    const errors = [], blocked = [];
    let connected = false;
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'http://localhost') {blocked.push(url.href); return route.abort();}
      if (url.pathname === '/api/session') return route.fulfill({json: {csrf: 'showcase-only'}});
      if (url.pathname === '/api/state') return route.fulfill({json: {...fixture.state,
        connected, auth: {status: connected ? 'connected' : 'disconnected'}}});
      if (url.pathname === '/api/items/atlas/preview') {
        assert.deepEqual(route.request().postDataJSON(), {discount: '10', kind: 'percent', recipient: 'atlas@example.com'});
        return route.fulfill({json: fixture.preview});
      }
      if (url.pathname === '/showcase-email') return route.fulfill({contentType: 'text/html', body: fixture.preview.html});
      const asset = {'/': ['index.html', 'text/html'], '/app.js': ['app.js', 'text/javascript'], '/styles.css': ['styles.css', 'text/css']}[url.pathname];
      if (asset) return route.fulfill({contentType: asset[1], body: fs.readFileSync(path.join(root, 'panel', asset[0]))});
      blocked.push(url.pathname);
      return route.abort();
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://localhost/');
    await page.waitForFunction(() => !document.getElementById('login-connect').disabled);
    await page.screenshot({path: path.join(output, '01-login.png')});
    connected = true;
    await page.evaluate(() => refresh());
    await page.locator('#application').waitFor({state: 'visible'});
    await page.locator('[data-key="atlas"]').click();
    await page.locator('#discount-kind').selectOption('percent');
    await page.locator('#discount-value').fill('10');
    assert.equal(await page.locator('#summary-pending').innerText(), '4');
    assert.equal((await page.locator('#live-total').innerText()).replace(/\s/g, ' '), '1620,00 €');
    await page.screenshot({path: path.join(output, '02-dashboard.png')});
    await page.locator('#prepare').click();
    await page.frameLocator('#email-preview').getByText('1.620,00 €', {exact: true}).waitFor();
    assert.equal(await page.locator('#review-send').isEnabled(), true);
    await page.locator('#review-send').click();
    await page.locator('#confirm-dialog').screenshot({path: path.join(output, '04-confirmation.png')});
    await page.locator('#cancel-send').click();
    const emailPage = await context.newPage();
    await emailPage.setViewportSize({width: 820, height: 1000});
    await emailPage.goto('http://localhost/showcase-email');
    assert.ok(!(await emailPage.locator('body').innerText()).includes('1.380,00'));
    await emailPage.screenshot({path: path.join(output, '03-email-preview.png'), fullPage: true});
    assert.deepEqual(errors, []);
    assert.deepEqual(blocked, []);
    console.log('Showcase captured: login, dashboard, real email template and confirmation. Synthetic data only; no external requests or sending.');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
