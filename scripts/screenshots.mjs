// Takes the product screenshots in static/screens/ from the demo data (fictional people only).
//
//   python3 scripts/demo_data.py /tmp/inboxtend-demo/tasks.sqlite3
//   INBOX_TEND_DB=/tmp/inboxtend-demo/tasks.sqlite3 INBOX_TEND_PORT=8781 python3 app.py &
//   npm i --no-save playwright && npx playwright install chromium     # not a dependency of the project
//   node scripts/screenshots.mjs
//
// Optional: BASE_URL (default http://127.0.0.1:8781), PLAYWRIGHT_MODULE (path to the playwright package entry).
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
import {mkdirSync} from 'node:fs';
const base = process.env.BASE_URL || 'http://127.0.0.1:8781', out = 'static/screens';
mkdirSync(out, {recursive: true});
const browser = await chromium.launch();
const shot = (page, name, clip) => page.screenshot({path: `${out}/${name}.jpg`, type: 'jpeg', quality: 88, ...(clip ? {clip} : {})});

async function open(width, height) {
  const context = await browser.newContext({viewport: {width, height}});
  const page = await context.newPage();
  await page.goto(base + '/login');
  await page.fill('#username', 'demo@example.org');
  await page.fill('#password', 'demo-password-123');
  await Promise.all([page.waitForURL('**/user/inbox'), page.click('button[type=submit]')]);
  const session = await (await context.request.get(base + '/api/session')).json();
  const set = body => context.request.patch(base + '/api/settings', {data: body, headers: {'X-CSRF-Token': session.csrf_token, Origin: base}});
  return {context, page, set};
}
const settle = page => page.waitForTimeout(1600);
// Bring the workspace (below the intro and the status card) to the top, just under the sticky header.
const toWorkspace = async page => { await page.evaluate(() => { const top = document.getElementById('inbox-grid').getBoundingClientRect().top + scrollY - 84; window.scrollTo({top, behavior: 'instant'}); }); await page.waitForTimeout(500); };

// 1. The Inbox with the translation column: a Spanish message, its English translation, the person card.
{
  const {page, set} = await open(1440, 1000);
  await set({translate_enabled: true, inbox_view: 'threads'});
  await page.goto(base + '/user/inbox'); await settle(page);
  await page.locator('#messages button', {hasText: 'Propuesta'}).first().click(); await settle(page);
  await toWorkspace(page); await shot(page, 'inbox');
}
// 2. People: flags, local time there, role and company, and the card.
{
  const {page, set} = await open(1440, 1000);
  await set({translate_enabled: false, inbox_view: 'people'});
  await page.goto(base + '/user/inbox'); await settle(page);
  await page.locator('#messages button', {hasText: 'Hiroshi'}).first().click(); await settle(page);
  await toWorkspace(page); await shot(page, 'people');
}
// 3. Topics: filtered to money, with an invoice and its attachments open.
{
  const {page, set} = await open(1440, 1000);
  await set({translate_enabled: false, inbox_view: 'messages'});
  await page.goto(base + '/user/inbox'); await settle(page);
  await page.fill('#mail-search', 'topic:money'); await page.waitForTimeout(900);
  await page.locator('#messages button', {hasText: 'Invoice #1042'}).first().click(); await settle(page);
  await toWorkspace(page); await shot(page, 'topics');
}
// 4. Tasks
{
  const {page} = await open(1440, 1000);
  await page.goto(base + '/user/tasks'); await settle(page);
  await shot(page, 'tasks');
}
// 5. Settings (the AI, sender details and topics sections)
{
  const {page} = await open(1440, 1000);
  await page.goto(base + '/user/settings'); await settle(page);
  await page.evaluate(() => document.getElementById('ai').scrollIntoView()); await page.waitForTimeout(400);
  await shot(page, 'settings');
}
// 5b. Gmail accounts (settings)
{
  const {page} = await open(1440, 1000);
  await page.goto(base + '/user/settings'); await settle(page);
  await page.evaluate(() => { const t = document.getElementById('gmail').getBoundingClientRect().top + scrollY - 90; window.scrollTo({top: t, behavior: 'instant'}); }); await page.waitForTimeout(400);
  await shot(page, 'accounts');
}
// 6. Phone
{
  const {page, set} = await open(390, 844);
  await set({translate_enabled: false, inbox_view: 'threads'});
  await page.goto(base + '/user/inbox'); await settle(page);
  await toWorkspace(page); await shot(page, 'phone');
}
await browser.close();
console.log('Screenshots written to ' + out);
