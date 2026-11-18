'use strict';
// Shared helpers for the signed-in pages. Loaded before the page script.
let csrfToken = '';
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function field(form, caption, name, value, kind = 'input') {
  const label = el('label', caption);
  const control = document.createElement(kind);
  control.name = name;
  control.value = value ?? '';
  if (kind === 'input') control.type = 'text';
  label.append(control);
  form.append(label);
  return control;
}
async function request(path, method = 'GET', body) {
  const options = {method, headers: {}};
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.headers['X-CSRF-Token'] = csrfToken;
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); }
  catch (_) {  // A proxy or CDN error page, not our JSON API.
    if (response.status === 401) { location.assign('/login'); throw Error('Session expired'); }
    throw Error('The server returned an unexpected response (HTTP ' + response.status + '). Please try again.');
  }
  if (response.status === 401) { location.assign('/login'); throw Error('Session expired'); }
  if (!response.ok) throw Error(data.error || 'Request failed (' + response.status + ')');
  return data;
}
// Icons are Font Awesome Free (self-hosted, see THIRD_PARTY_LICENSES.md): a name here maps to its class.
const ICONS = {
  search: 'fa-solid fa-magnifying-glass', refresh: 'fa-solid fa-rotate', pencil: 'fa-regular fa-pen-to-square',
  history: 'fa-solid fa-clock-rotate-left', check: 'fa-solid fa-check', plus: 'fa-solid fa-plus',
  mail: 'fa-regular fa-envelope', paperclip: 'fa-solid fa-paperclip', download: 'fa-solid fa-download',
  eye: 'fa-regular fa-eye', language: 'fa-solid fa-language', sparkles: 'fa-solid fa-wand-magic-sparkles',
  reply: 'fa-solid fa-reply', copy: 'fa-regular fa-copy', trash: 'fa-regular fa-trash-can', xmark: 'fa-solid fa-xmark'
};
function icon(name) {
  const node = document.createElement('i');
  node.className = (ICONS[name] || 'fa-solid fa-circle') + ' fa-fw';
  node.setAttribute('aria-hidden', 'true');
  return node;
}
function iconButton(name,label) {
  const button=el('button',undefined,'icon-button');
  button.type='button';
  button.append(icon(name),document.createTextNode(label));
  return button;
}
function notify(message,error=false) {
  const banner=document.getElementById('notice');
  if(!banner)return;
  banner.textContent=message;
  banner.className=error?'notice-banner error':'notice-banner';
}


// Resolves once the session (and CSRF token) is known; page scripts await it.
const sessionReady = request('/api/session').then(session => { csrfToken = session.csrf_token; });
let userSettings = {timezone: 'Pacific/Saipan', due_soon_hours: 48, default_priority: 'medium', inbox_view: 'threads', mark_read_on_open: true, gmail_write_read: false, ai_enabled: false, ai_provider: 'codex', translate_enabled: false, translate_language: ''};
const AI_PROVIDERS = {codex: {name: 'Codex', vendor: 'OpenAI'}, claude: {name: 'Claude', vendor: 'Anthropic'}};
function aiProvider() { return AI_PROVIDERS[userSettings.ai_provider] || AI_PROVIDERS.codex; }
// Resolves after the user's saved settings are loaded (falls back to the defaults above on failure).
const settingsReady = sessionReady.then(() => request('/api/settings')).then(result => { userSettings = result.settings; })
  .catch(() => {});
// 'today' | 'yesterday' | 'earlier' for a timestamp (ms since epoch), by calendar day in the Settings timezone.
function ageOf(ms) {
  if (!ms) return 'earlier';
  const day = value => {
    try { return new Intl.DateTimeFormat('en-CA', {timeZone: userSettings.timezone, year: 'numeric', month: '2-digit', day: '2-digit'}).format(value); }
    catch (_) { return new Date(value).toISOString().slice(0, 10); }
  };
  const today = day(new Date());
  const [y, m, d] = today.split('-').map(Number);
  const yesterday = new Date(Date.UTC(y, m - 1, d - 1)).toISOString().slice(0, 10);   // Calendar arithmetic, so daylight saving cannot skip a day.
  const when = day(new Date(ms));
  return when >= today ? 'today' : when === yesterday ? 'yesterday' : 'earlier';
}
// A Gmail timestamp (ms since epoch) shown in the timezone chosen in Settings.
function formatWhen(ms, fallback = '') {
  if (!ms) return fallback;
  try {
    return new Intl.DateTimeFormat(undefined, {timeZone: userSettings.timezone, dateStyle: 'medium', timeStyle: 'short'}).format(new Date(ms));
  } catch (_) { return new Date(ms).toLocaleString(); }
}
document.addEventListener('DOMContentLoaded', () => {
  const logout = document.getElementById('logout');
  if (logout) logout.addEventListener('click', async () => {
    try { await sessionReady; await request('/api/auth/logout','POST',{}); location.assign('/login'); }
    catch (error) { notify('Sign out failed: ' + error.message, true); }
  });
});
