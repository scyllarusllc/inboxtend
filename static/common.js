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
let userSettings = {timezone: 'Pacific/Saipan', due_soon_hours: 48, default_priority: 'medium', inbox_view: 'threads', mark_read_on_open: true, gmail_write_read: false, ai_enabled: false, ai_provider: 'codex', translate_enabled: false, translate_language: '', flags_enabled: false, topics_enabled: false};
const AI_PROVIDERS = {codex: {name: 'Codex', vendor: 'OpenAI'}, claude: {name: 'Claude', vendor: 'Anthropic'}};
function aiProvider() { return AI_PROVIDERS[userSettings.ai_provider] || AI_PROVIDERS.codex; }
// Resolves after the user's saved settings are loaded (falls back to the defaults above on failure).
const settingsReady = sessionReady.then(() => request('/api/settings')).then(result => { userSettings = result.settings; })
  .catch(() => {});
// "5 min ago", "3 h ago", "2 d ago", "3 w ago", "4 mo ago", "2 y ago". The exact time goes in the tooltip.
function agoLabel(ms) {
  if (!ms) return '';
  const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + ' min ago';
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + ' h ago';
  const days = Math.floor(hours / 24);
  if (days < 7) return days + ' d ago';
  if (days < 30) return Math.floor(days / 7) + ' w ago';
  if (days < 365) return Math.floor(days / 30) + ' mo ago';
  return Math.floor(days / 365) + ' y ago';
}
function agoSpan(ms, fallback = '') {
  if (!ms) return el('span', fallback, 'ago');
  const span = el('span', agoLabel(ms), 'ago');
  span.dataset.ago = String(ms);
  span.title = formatWhen(ms);
  return span;
}
// Keep every "x ago" on the page honest while it stays open.
function refreshAgo() {
  if (typeof document.querySelectorAll !== 'function') return;
  for (const node of document.querySelectorAll('[data-ago]')) node.textContent = agoLabel(Number(node.dataset.ago));
  for (const node of document.querySelectorAll('[data-tz]')) paintTimeChip(node);
}
if (typeof setInterval === 'function') setInterval(refreshAgo, 30000);
// The time where a correspondent is, from the UTC offset of their messages. No network, no AI.
function thereNow(offsetMinutes) {
  const t = new Date(Date.now() + offsetMinutes * 60000);
  const hour = t.getUTCHours();
  return {text: String(hour).padStart(2, '0') + ':' + String(t.getUTCMinutes()).padStart(2, '0'), night: hour >= 19 || hour < 7, asleep: hour >= 23 || hour < 7};
}
function paintTimeChip(node) {
  const there = thereNow(Number(node.dataset.tz));
  node.replaceChildren(el('i', undefined, 'fa-solid fa-fw ' + (there.night ? 'fa-moon' : 'fa-sun')), document.createTextNode(there.text));
  node.title = 'Local time there: ' + there.text + (there.asleep ? ' (probably asleep)' : '');
  node.dataset.asleep = String(there.asleep);
}
function timeChip(offsetMinutes) {
  if (offsetMinutes === null || offsetMinutes === undefined) return null;
  const chip = el('span', undefined, 'time-chip');
  chip.dataset.tz = String(offsetMinutes);
  paintTimeChip(chip);
  return chip;
}
const RELATIONSHIPS = {
  client: ['Client', 'fa-handshake'], colleague: ['Colleague', 'fa-users'], friend_family: ['Friend or family', 'fa-heart'],
  recruiter: ['Recruiter', 'fa-user-tie'], vendor: ['Vendor', 'fa-store'], service: ['Service', 'fa-headset'],
  newsletter: ['Newsletter', 'fa-newspaper'], automated: ['Automated', 'fa-robot'], other: ['Other', 'fa-user']
};
function chip(text, iconClass, tone, title) {
  const node = el('span', undefined, 'chip' + (tone ? ' ' + tone : ''));
  if (iconClass) node.append(el('i', undefined, 'fa-solid fa-fw ' + iconClass));
  node.append(document.createTextNode(text));
  if (title) node.title = title;
  return node;
}
function roughDuration(seconds) {
  if (seconds < 3600) return 'under an hour';
  if (seconds < 86400) return 'about ' + Math.round(seconds / 3600) + ' h';
  const days = Math.round(seconds / 86400);
  return 'about ' + days + (days === 1 ? ' day' : ' days');
}
// The little "contact card" for the sender of the open message. AI guesses are marked; the history is counted from your mail.
function personCard(person, item) {
  if (!person) return null;
  const guessed = userSettings.flags_enabled && person.guessed;
  const history = person.history;
  const card = el('section', undefined, 'person-card');
  const head = el('div', undefined, 'card-row');
  const flag = guessed ? flagEl(Object.assign({}, item, {country: person.country, country_conf: person.confidence, country_reason: person.reason})) : null;
  const where = [person.city, person.country ? countryName(person.country) : ''].filter(Boolean).join(', ');
  if (guessed && (flag || where)) { if (flag) head.append(flag); head.append(el('strong', where || 'Location unknown')); }
  const work = [person.role, person.company].filter(Boolean).join(' · ');
  if (guessed && work) head.append(chip(work, 'fa-briefcase', 'ai', 'AI guess from their signature'));
  if (guessed) {
    const [label, iconClass] = RELATIONSHIPS[person.relationship] || [];
    if (label && person.relationship !== 'unknown') head.append(chip(label, iconClass, 'ai', 'AI guess from the content'));
    if (person.language) head.append(chip('Writes ' + person.language, 'fa-language', 'ai', 'AI guess'));
    const style = [person.formality !== 'unknown' ? person.formality : '', person.brevity === 'short' ? 'concise' : person.brevity === 'long' ? 'long messages' : person.brevity === 'medium' ? 'medium length' : ''].filter(Boolean).join(', ');
    if (style) head.append(chip(style, 'fa-pen-nib', 'ai', 'AI guess from how they write'));
  }
  // The clock only means something for a known place; an offset of +00:00 alone is usually a server.
  const timeNow = history && (person.country || history.offset_minutes) ? timeChip(history.offset_minutes) : null;
  if (timeNow) { const there = el('span', undefined, 'chip'); there.append(el('span', 'There now', 'mr-1.5'), timeNow); if (timeNow.dataset.asleep === 'true') there.append(el('span', 'probably asleep', 'ml-1.5 text-slate-500')); head.append(there); }
  if (head.children.length) card.append(head);
  if (history) {
    const bits = [];
    bits.push('Known since ' + new Intl.DateTimeFormat(undefined, {timeZone: userSettings.timezone, month: 'short', year: 'numeric'}).format(new Date(history.since)));
    bits.push(history.count + (history.count === 1 ? ' email' : ' emails') + (history.sent ? ' (' + history.received + ' from them, ' + history.sent + ' from you)' : ''));
    if (!history.received) bits.push('no message from them yet');
    if (history.reply_seconds !== null) bits.push('usually replies in ' + roughDuration(history.reply_seconds));
    if (history.usual_hour !== null) bits.push('usually writes around ' + String(history.usual_hour).padStart(2, '0') + ':00 their time');
    card.append(el('p', bits.join(' · '), 'meta'));
  }
  if (!card.children.length) return null;
  if (guessed) card.append(el('p', 'Highlighted details are AI guesses and can be wrong; the rest is counted from your mail.', 'meta text-slate-400'));
  return card;
}
// What a message is about (guessed once per message by the AI): code -> [label, Font Awesome icon].
const TOPICS = {
  money: ['Money', 'fa-sack-dollar'], job: ['Work and jobs', 'fa-briefcase'], love: ['Love', 'fa-heart'],
  friends_family: ['Friends and family', 'fa-user-group'], travel: ['Travel', 'fa-plane'], shopping: ['Shopping and orders', 'fa-bag-shopping'],
  meeting: ['Meetings and scheduling', 'fa-calendar-days'], security: ['Account security', 'fa-lock'], health: ['Health', 'fa-heart-pulse'],
  legal: ['Legal and official', 'fa-scale-balanced'], education: ['Education', 'fa-graduation-cap'], tech: ['Tech', 'fa-laptop-code'],
  social: ['Social networks', 'fa-thumbs-up'], news: ['News and newsletters', 'fa-newspaper'], events: ['Events', 'fa-ticket'],
  food: ['Food', 'fa-utensils'], promo: ['Promotions', 'fa-tag']
};
// Small round icons for a list row (display only), or null when there is nothing to show.
function topicIcons(codes) {
  if (!userSettings.topics_enabled || !codes || !codes.length) return null;
  const box = el('span', undefined, 'topics');
  for (const code of codes) {
    const known = TOPICS[code];
    if (!known) continue;
    const badge = el('span', undefined, 'topic');
    badge.dataset.topic = code;
    badge.title = known[0];
    badge.setAttribute('role', 'img');
    badge.setAttribute('aria-label', known[0]);
    badge.append(el('i', undefined, 'fa-solid ' + known[1]));
    box.append(badge);
  }
  return box.children.length ? box : null;
}
// Labelled buttons for the open message; pressing one calls onPick(code), which shows all mail on that topic.
function topicButtons(codes, onPick) {
  if (!userSettings.topics_enabled || !codes || !codes.length) return null;
  const box = el('div', undefined, 'topics-row');
  box.append(el('span', 'About', 'meta'));
  for (const code of codes) {
    const known = TOPICS[code];
    if (!known) continue;
    const button = el('button', undefined, 'topic-chip');
    button.type = 'button';
    button.dataset.topic = code;
    button.title = 'Show all mail about ' + known[0].toLowerCase() + ' (AI guess)';
    button.append(el('i', undefined, 'fa-solid fa-fw ' + known[1]), document.createTextNode(known[0]));
    button.addEventListener('click', () => onPick(code));
    box.append(button);
  }
  return box.children.length > 1 ? box : null;
}
// Country name for an ISO code, in English.
function countryName(code) {
  try { return new Intl.DisplayNames(['en'], {type: 'region'}).of(code) || code; } catch (_) { return code; }
}
// A flag for the other party of a message or group (item.country comes from the AI guess), or null.
function flagEl(item) {
  if (!userSettings.flags_enabled || !item || !item.country) return null;
  const flag = el('span', undefined, 'fi fi-' + item.country.toLowerCase() + ' flag' + (item.country_conf === 'low' ? ' low' : ''));
  flag.title = countryName(item.country) + ' · AI guess, ' + (item.country_conf || 'low') + ' confidence' + (item.country_reason ? ': ' + item.country_reason : '');
  flag.setAttribute('role', 'img');
  flag.setAttribute('aria-label', countryName(item.country));
  return flag;
}
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
