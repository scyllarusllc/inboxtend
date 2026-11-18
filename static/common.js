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
// Lucide icon paths (ISC; Feather-derived icons also MIT). See THIRD_PARTY_LICENSES.md.
// Local SVGs; no CDN or runtime dependencies.
const paths = {
  search:[['circle',{cx:11,cy:11,r:8}],['path',{d:'m21 21-4.35-4.35'}]],
  refresh:[['path',{d:'M20 11a8 8 0 0 0-14.6-4.5L3 9'}],['path',{d:'M3 4v5h5'}],['path',{d:'M4 13a8 8 0 0 0 14.6 4.5L21 15'}],['path',{d:'M16 15h5v5'}]],
  pencil:[['path',{d:'M12 20h9'}],['path',{d:'M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z'}]],
  history:[['path',{d:'M3 12a9 9 0 1 0 3-6.7L3 8'}],['path',{d:'M3 3v5h5'}],['path',{d:'M12 7v5l4 2'}]],
  check:[['path',{d:'M20 6 9 17l-5-5'}]],
  plus:[['path',{d:'M12 5v14'}],['path',{d:'M5 12h14'}]],
  mail:[['rect',{x:2,y:4,width:20,height:16,rx:2}],['path',{d:'m22 7-10 6L2 7'}]],
  paperclip:[['path',{d:'M13.5 6.5 7 13a3 3 0 0 0 4.24 4.24l7.07-7.07a5 5 0 0 0-7.07-7.07l-7.78 7.78a7 7 0 0 0 9.9 9.9L20 14'}]]
};
function icon(name) {
  const ns='http://www.w3.org/2000/svg';
  const svg=document.createElementNS(ns,'svg');
  svg.setAttribute('viewBox','0 0 24 24');
  svg.setAttribute('class','ultra-icon');
  svg.setAttribute('aria-hidden','true');
  for (const [tag,attrs] of paths[name] || []) {
    const node=document.createElementNS(ns,tag);
    for (const [key,value] of Object.entries(attrs)) node.setAttribute(key,String(value));
    svg.append(node);
  }
  return svg;
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
let userSettings = {timezone: 'Pacific/Saipan', due_soon_hours: 48, default_priority: 'medium', ai_enabled: false};
// Resolves after the user's saved settings are loaded (falls back to the defaults above on failure).
const settingsReady = sessionReady.then(() => request('/api/settings')).then(result => { userSettings = result.settings; })
  .catch(() => {});
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
