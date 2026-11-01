/* Browser regression: verify real geometry, not just icon counts/overflow.
 * Run: node scripts/check_admin_layout.cjs [path-to-playwright-module]
 * Uses synthetic identities and local assets; never contacts production. */
const { chromium } = require(process.argv[2] || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const assets = path.join(__dirname, '../queue_bot/mini_app/static');

(async () => {
  const browser = await chromium.launch({ channel: process.env.PI231_BROWSER_CHANNEL || 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    let owner = false;
    page.on('pageerror', error => errors.push(error.message));
    await page.route('https://telegram.org/**', route => route.fulfill({
      contentType: 'text/javascript',
      body: 'window.Telegram={WebApp:{initData:"test",colorScheme:"dark",ready(){},expand(){},onEvent(){}}};',
    }));
    await page.route('https://layout.invalid/**', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/miniapp/bootstrap') return route.fulfill({ json: {
        today: '2026-11-01', swaps: [],
        queues: ['Машинне навчання', 'Людино-машинна взаємодія', 'Конструювання програмного забезпечення', 'Програмування в Інтернет', 'Курсова робота'].map((subject, i) => ({
          id: i + 1, subject, teacher: 'Викладач', type: 'lesson', status: 'open', count: 18,
          my_position: i + 1, my_labs: [1, 2], my_planned_minutes: 30,
        })),
        next_lesson: { date: '2026-11-02', startTime: '10:00', endTime: '11:20', lessons: [{ subject: 'Машинне навчання', room: '231' }] },
        me: { name: 'Тест Адміністратор', is_registered: true, is_admin: true, is_owner: owner },
      } });
      if (url.pathname.startsWith('/api/miniapp/avatar/')) return route.fulfill({ status: 204 });
      if (url.pathname === '/api/miniapp/admin/users') return route.fulfill({ json: { users: [] } });
      const name = url.pathname.endsWith('/') ? 'index.html' : path.basename(url.pathname);
      const file = path.join(assets, name);
      if (!fs.existsSync(file)) return route.fulfill({ status: 404, body: 'Unknown test route' });
      const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.png': 'image/png' };
      return route.fulfill({ body: fs.readFileSync(file), contentType: types[path.extname(name)] || 'text/plain' });
    });
    for (owner of [false, true]) {
      for (const width of [320, 390, 430, 768, 1024, 1280, 1440, 1920]) {
        await page.setViewportSize({ width, height: 844 });
        await page.goto('https://layout.invalid/app/');
        await page.locator('.home-queue-hero').waitFor();
        await page.waitForFunction(() => !document.querySelector('.progress'));
        await page.evaluate(() => document.fonts.ready);
        const home = await page.evaluate(() => {
          const rect = selector => { const r = document.querySelector(selector).getBoundingClientRect(); return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height}; };
          return {main:rect('main'), hero:rect('.home-queue-hero'), lesson:rect('.upcoming-card'), nav:rect('.bottom-nav')};
        });
        if (width >= 1024) {
          assert.ok(home.main.width >= Math.min(width - 300, 1120) && home.main.width <= 1121, `desktop content width: ${width}`);
          assert.ok(home.hero.right <= home.lesson.x, `desktop cards overlap: ${width}`);
          assert.ok(Math.abs(home.hero.y - home.lesson.y) < 1, `desktop cards not aligned: ${width} ${JSON.stringify(home)}`);
          assert.ok(home.nav.right < home.main.x && home.nav.height < 320, `desktop sidebar oversized: ${width}`);
        } else {
          assert.ok(home.lesson.bottom <= home.hero.y, `mobile cards no longer stacked: ${width}`);
        }
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        if (process.env.PI231_DESKTOP_SCREENSHOT && !owner && width === 1440)
          await page.screenshot({ path: process.env.PI231_DESKTOP_SCREENSHOT, fullPage: true });
        await page.getByRole('button', { name: 'До адміна', exact: true }).click();
        await page.getByRole('heading', { name: 'Керування', exact: true }).waitFor();
        await page.waitForFunction(() => !document.querySelector('.progress'));
        await page.evaluate(() => document.fonts.ready);
        const rows = await page.locator('.admin-categories > button').evaluateAll(nodes => nodes.map(node => {
          const icons = [...node.children].filter(child => child.tagName.toLowerCase() === 'svg');
          const copy = [...node.children].find(child => child.tagName.toLowerCase() === 'span');
          const rect = element => { const r = element.getBoundingClientRect(); return { x: r.x, right: r.right, y: r.y, width: r.width, height: r.height }; };
          return { title: copy?.textContent, row: rect(node), copy: copy && rect(copy), icons: icons.map(rect), grid:getComputedStyle(node).gridTemplateColumns, children:[...node.children].map(n=>({tag:n.tagName,col:getComputedStyle(n).gridColumn,row:getComputedStyle(n).gridRow})) };
        }));
        assert.equal(rows.length, owner ? 8 : 7);
        for (const row of rows) {
          const label = `${width}px owner=${owner} ${row.title}`;
          assert.equal(row.icons.length, 2, label);
          assert.ok(row.copy.width > row.row.width * 0.5, `text squeezed into narrow column: ${label} ${JSON.stringify(row)}`);
          assert.ok(row.icons[0].right <= row.copy.x, `leading icon overlaps text: ${label}`);
          assert.ok(row.copy.right <= row.icons[1].x + 1, `trailing icon overlaps text: ${label}`);
          assert.ok(row.row.height < 150, `row is abnormally tall: ${label}`);
          assert.ok(row.icons[0].right < row.icons[1].x, `icons overlap: ${label}`);
        }
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        if (process.env.PI231_LAYOUT_SCREENSHOT && owner && width === 390)
          await page.screenshot({ path: process.env.PI231_LAYOUT_SCREENSHOT, fullPage: true });
      }
    }
    // Theme changes must preserve the live page and survive a full reload.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('https://layout.invalid/app/');
    await page.locator('.home-queue-hero').waitFor();
    await page.waitForFunction(() => !document.querySelector('.progress'));
    await page.evaluate(() => { window.originalMain = document.querySelector('main'); });
    await page.getByRole('button', { name: 'Увімкнути світлу тему', exact: true }).click();
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'light');
    assert.equal(await page.evaluate(() => document.querySelector('main') === window.originalMain), true);
    assert.equal(await page.evaluate(() => localStorage.getItem('pi231-theme')), 'light');
    await page.reload();
    await page.locator('.home-queue-hero').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'light');
    if (process.env.PI231_LIGHT_SCREENSHOT) await page.screenshot({ path: process.env.PI231_LIGHT_SCREENSHOT, fullPage: true });
    await page.getByRole('button', { name: 'Увімкнути темну тему', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.bottom-nav .lens-ready'));
    await page.waitForFunction(() => !document.querySelector('.progress'));
    await page.evaluate(() => {
      window.originalNav = document.querySelector('.bottom-nav');
      window.originalLens = document.querySelector('.bottom-nav .liquid-lens');
      window.startLensX = window.originalLens.getBoundingClientRect().x;
    });
    const queueButton = page.getByRole('button', { name: 'Черги', exact: true });
    const target = await queueButton.boundingBox();
    await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2);
    await page.mouse.down();
    assert.equal(await page.evaluate(() => document.querySelector('.bottom-nav .liquid-lens').getBoundingClientRect().x), await page.evaluate(() => window.startLensX), 'tap-down must not teleport lens');
    await page.evaluate(() => {
      window.lensFrames = [];
      window.motionDone = new Promise(resolve => {
        const frame = () => {
          const lens = document.querySelector('.bottom-nav .liquid-lens');
          window.lensFrames.push({x:lens.getBoundingClientRect().x, same: lens === window.originalLens && document.querySelector('.bottom-nav') === window.originalNav});
          if (window.lensFrames.length < 25) requestAnimationFrame(frame); else resolve();
        };
        requestAnimationFrame(frame);
      });
    });
    await page.mouse.up();
    const frames = await page.evaluate(async () => { await window.motionDone; return window.lensFrames; });
    assert.ok(frames.every(frame => frame.same), 'navigation/lens recreated during tab switch');
    assert.ok(frames.at(-1).x > frames[0].x + 20, 'lens did not slide to new tab');
    for (let i = 1; i < frames.length; i++) assert.ok(frames[i].x >= frames[i-1].x - 1, 'lens jumped backwards');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    assert.equal(await page.locator('.liquid-lens').evaluate(node => getComputedStyle(node).transitionDuration), '0s');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    assert.deepEqual(errors, []);
    console.log('PASS: 320–1920px home/admin layout; persistent themes; stable nav identity and monotonic lens animation; reduced motion; no JS errors');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
