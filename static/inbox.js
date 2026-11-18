'use strict';
const list = document.getElementById('messages');
const detail = document.getElementById('detail');
const draft = document.getElementById('draft');
let messages = [], selectedId = null, folder = 'inbox', view = 'threads', groups = [], currentGroup = null, gmailWriteAccess = false;
// What the list shows: search words (Gmail-style operators work), two quick filters, and how many rows are drawn.
let flagsAttempted = false, topicsAttempted = false;
// Connected Gmail accounts (from the status) and the one the list is narrowed to ('' = all of them).
let accounts = [], accountFilter = sessionStorage.getItem('inboxtend-account') || '';
let searchText = '', onlyUnread = false, onlyAttachments = false, listLimit = 100, lastData = null, listRun = 0;
const PAGE = 100;
const filtersActive = () => searchText.trim() !== '' || onlyUnread || onlyAttachments;
function listUrl() {
  const q = new URLSearchParams({folder, view});
  if (searchText.trim()) q.set('q', searchText.trim());
  if (onlyUnread) q.set('unread', '1');
  if (onlyAttachments) q.set('attachments', '1');
  if (accountFilter) q.set('account', accountFilter);
  return '/api/connectors/gmail/messages?' + q;
}
// Read state reaches Gmail only if the user allowed it and the message's own account has the permission (`account` is optional).
const writesToGmail = account => gmailWriteAccess && userSettings.gmail_write_read && (!account || account.write);
const translationPanel = document.getElementById('translation-panel');
const translationBox = document.getElementById('translation');
const translationCaption = document.getElementById('translation-caption');
function clearTranslation() {
  translationBox.replaceChildren('Choose a message to see its translation.');
  translationCaption.textContent = '';
}
// The third column. Translations are saved on the server, so reopening a message never calls the AI again;
// only the Retranslate button forces a new one.
async function showTranslation(id, force = false) {
  const language = userSettings.translate_language;
  translationCaption.textContent = language;
  if (!language) { translationBox.replaceChildren(el('p', 'Choose the language to translate into on the Settings page.', 'meta')); return; }
  translationBox.replaceChildren(el('p', 'Translating into ' + language + '… the first time can take up to ' + userSettings.ai_timeout + ' seconds; it is saved for next time.', 'meta'));
  let result;
  try { result = (await request('/api/translate', 'POST', {id, force})).translation; }
  catch (error) {
    if (selectedId !== id) return;
    const retry = iconButton('refresh', 'Try again');
    retry.addEventListener('click', () => showTranslation(id));
    translationBox.replaceChildren(el('p', error.message, 'error'), retry);
    return;
  }
  if (selectedId !== id) return;
  const nodes = [];
  if (result.already_in_language) {
    nodes.push(el('p', 'This message is already in ' + result.language + '; no translation was needed.', 'meta'));
  } else {
    if (result.subject) nodes.push(el('h3', result.subject));
    nodes.push(el('pre', result.body || '(No text content)', 'message-body'));  // Text only, never HTML.
  }
  const when = new Date(result.created_at * 1000);
  const info = el('p', (result.source_language ? 'From ' + result.source_language + ' · ' : '') + 'translated by ' + result.provider +
    ' (' + result.model + ') · ' + (result.cached ? 'saved ' + formatWhen(when.getTime()) : 'just now'), 'meta');
  const again = iconButton('refresh', 'Retranslate');
  again.addEventListener('click', () => showTranslation(id, true));
  translationBox.replaceChildren(...nodes, info, again);
}
async function showProposal(messageId, prepared) {
  const m = messages.find(message => message.id === messageId);
  if (!m) return;
  const proposal = prepared || await request('/api/connectors/gmail/messages/' + encodeURIComponent(messageId) + '/proposal');
  if (selectedId !== messageId) return;
  for (const old of detail.querySelectorAll('.task-proposal')) old.remove();
  const form = el('form', undefined, 'task-proposal');
  form.append(el('h4', 'Review suggested task'), el('p', proposal.note, 'meta'));
  if (proposal.ai) {
    const box = el('div', undefined, 'ai-box');
    box.append(el('p', 'AI suggestion · confidence ' + proposal.ai.confidence + ' · ' + (proposal.ai.provider || 'AI') + ' · model: ' + proposal.ai.model, 'meta'));
    for (const quote of proposal.ai.evidence) box.append(el('blockquote', (quote.verified ? '✓ ' : '⚠ Not found word for word in the message: ') + quote.text, quote.verified ? '' : 'unverified'));
    if (!proposal.ai.evidence.length) box.append(el('p', 'The AI gave no supporting quote.', 'meta'));
    if (proposal.ai.notes) box.append(el('p', 'Notes: ' + proposal.ai.notes, 'meta'));
    form.append(box);
  }
  const f = proposal.inferred;
  field(form, 'Title', 'title', f.title).required = true;
  field(form, 'Description', 'description', f.description, 'textarea');
  field(form, 'Owner (optional)', 'owner', f.owner || '');
  const priority = field(form, 'Priority', 'priority', f.priority, 'select');
  for (const value of ['low', 'medium', 'high']) {
    const option = el('option', value); option.value = value; priority.append(option);
  }
  priority.value = f.priority;
  field(form, 'Due date (YYYY-MM-DD, optional)', 'due_date', f.due_date);
  field(form, 'Due time (HH:MM, required with date)', 'due_time', f.due_time);
  field(form, 'Project', 'project_label', f.project_label);
  field(form, 'Customer label', 'customer_label', f.customer_label);
  const submit = el('button', 'Confirm and create local task', 'action');
  submit.type = 'submit';
  const feedback = el('p', '', 'meta');
  form.append(submit, feedback);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    submit.disabled = true;
    const data = Object.fromEntries(new FormData(form).entries());
    data.message_id = messageId;
    if (proposal.ai) data.ai_assisted = true;
    data.due_date = data.due_date.trim() || null;
    data.due_time = data.due_time.trim() || null;
    data.due_timezone = f.due_timezone || userSettings.timezone;
    try {
      await request('/api/tasks', 'POST', data);
      feedback.textContent = 'Task saved locally. No email sent. ';
      const link = el('a', 'View in Tasks'); link.href = '/user/tasks'; feedback.append(link);
    } catch (error) {
      feedback.textContent = error.message;
      submit.disabled = false;
    }
  });
  detail.append(form);
}
function formatBytes(n) {
  return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
}
function renderAttachments(message) {
  if (!message.attachments.length) return null;
  const block = el('section', undefined, 'attachment-section');
  const heading = el('h5', undefined, 'attachment-heading');
  heading.append(icon('paperclip'), document.createTextNode(' Attachments (' + message.attachments.length + ')'));
  block.append(heading);
  for (const file of message.attachments) {
    const row = el('div', undefined, 'attachment-row');
    const meta = el('div', undefined, 'attachment-meta');
    meta.append(el('strong', file.name), el('span', file.mime_type + ' · ' + formatBytes(file.size_bytes) +
      (file.stored ? '' : ' · not stored (' + (file.skipped_reason || 'unavailable') + ')'), 'meta'));
    row.append(meta);
    if (file.stored) {
      const base = '/api/connectors/gmail/messages/' + encodeURIComponent(message.id) + '/attachments/' + file.id;
      const download = el('a', 'Download', 'icon-button'); download.href = base; download.setAttribute('download', file.name); download.prepend(icon('download'));
      row.append(download);
      if (file.mime_type === 'text/plain' || file.mime_type === 'text/csv' || /\.(txt|csv)$/i.test(file.name)) {
        const preview = el('pre', undefined, 'attachment-preview'); preview.hidden = true;
        const open = iconButton('paperclip', 'Preview');
        open.addEventListener('click', async () => {
          if (!preview.hidden) { preview.hidden = true; return; }
          open.disabled = true;
          try {
            const result = await request(base + '/preview');
            preview.textContent = result.content + (result.truncated ? '\n… (truncated)' : '');  // Untrusted: text only.
          } catch (error) { preview.textContent = 'Preview unavailable: ' + error.message; }
          preview.hidden = false; open.disabled = false;
        });
        row.append(open, preview);
      }
    }
    block.append(row);
  }
  block.append(el('p', 'Attachments are untrusted. They are only downloaded, never opened or run by this app.', 'meta'));
  return block;
}
// "Synced x of y" next to what Gmail itself reports, so it is clear how much of the mailbox is here.
function showTotals(state) {
  const line = document.getElementById('gmail-counts'), remote = state.remote, n = value => Number(value).toLocaleString();
  if (!remote) { line.textContent = ''; return; }
  const part = (name, here, there) => there == null ? name + ' ' + n(here) : name + ' ' + n(here) + ' of ' + n(there) + (there ? ' (' + Math.min(100, Math.round(here * 100 / there)) + '%)' : '');
  const behind = (remote.inbox != null && state.counts.inbox < remote.inbox) || (remote.sent != null && state.counts.sent < remote.sent);
  line.textContent = (remote.all != null ? 'Your Gmail holds ' + n(remote.all) + ' messages in total. ' : '') +
    'Synced here: ' + part('Inbox', state.counts.inbox, remote.inbox) + ' · ' + part('Sent', state.counts.sent, remote.sent) + '.' +
    (behind ? ' Each folder keeps its ' + n(state.sync_limit) + ' most recent messages; raise “Messages per folder” in Settings to sync more.' : ' Everything in these folders is synced.');
}
function showCounts(counts) {
  folderInbox.textContent = 'Inbox (' + counts.inbox + (counts.unread ? ' · ' + counts.unread + ' unread' : '') + ')';
  folderSent.textContent = 'Sent (' + counts.sent + ')';
}
// Marks a message read/unread in Inbox Tend (never in Gmail) and updates the lists without reloading them.
async function setRead(id, markRead) {
  const result = await request('/api/connectors/gmail/messages/' + encodeURIComponent(id) + '/read', 'POST', {read: markRead});
  if (result.gmail_error) notify('Saved here, but Gmail was not updated: ' + result.gmail_error, true);
  readState.set(id, result.unread);
  paintRead();
  for (const m of messages) if (m.id === id) m.unread = result.unread;
  for (const g of groups) g.unread_count = g.messages.filter(m => m.unread).length;
  request('/api/connectors/gmail/status').then(state => { const own = accountFilter ? (state.accounts || []).find(a => String(a.id) === accountFilter) : null; showCounts(own ? own.counts : state.counts); }).catch(() => {});
  renderList(groups.length ? {groups} : {messages});
  return result.unread;
}
async function selectMessage(id) {
  selectedId = id;
  detailRead = null;
  const m = messages.find(message => message.id === id) || {id, subject: '', from: '', date: ''};
  for (const button of list.querySelectorAll('button.message')) {
    button.setAttribute('aria-pressed', String(button.dataset.id === (currentGroup ? currentGroup.id : id)));
  }
  draft.replaceChildren();
  detail.replaceChildren(el('h3', m.subject || '(No subject)'),
    el('div', (m.from || 'Unknown sender') + ' · ' + formatWhen(m.internal_date, m.date || 'No date'), 'meta'), el('p', 'Loading message…'));
  let full;
  try { full = (await request('/api/connectors/gmail/messages/' + encodeURIComponent(id))).message; }
  catch (error) { if (selectedId === id) detail.append(el('p', error.message, 'error')); return; }
  if (selectedId !== id) return;
  if (userSettings.translate_enabled) showTranslation(id);
  readState.set(full.id, full.unread);
  for (const t of full.thread) readState.set(t.id, t.unread);
  detailRead = {id, badge: null, toggle: null, items: []};
  const head = [el('h3', full.subject || '(No subject)'),
    el('div', 'From ' + (full.from || 'unknown') + ' · ' + formatWhen(full.internal_date, full.date || 'No date'), 'meta')];
  if (full.to) head.push(el('div', 'To ' + full.to, 'meta'));
  const owner = accounts.length > 1 ? accounts.find(a => a.id === full.account_id) : null;
  if (owner) { const line = el('div', undefined, 'meta account-line'); line.append(accountDot(owner.id), document.createTextNode('Account ' + accountName(owner))); head.push(line); }
  if (full.cc) head.push(el('div', 'Cc ' + full.cc, 'meta'));
  head[1] = el('div', undefined, 'meta flex flex-wrap items-center gap-x-1.5 gap-y-1');
  head[1].append(el('span', (full.direction === 'sent' ? 'Sent' : 'Received') + ' · From ' + (full.from || 'unknown') + ' · ' + formatWhen(full.internal_date, full.date || 'No date')), agoSpan(full.internal_date));
  const detailFlag = flagEl(full);
  if (detailFlag) head[1].append(detailFlag, el('span', countryName(full.country)));
  if (full.direction !== 'sent') { detailRead.badge = el('span', '', 'read-badge'); head[1].append(detailRead.badge); }
  const body = el('pre', full.body_text || full.snippet || '(No text content)', 'message-body');  // Text only, never HTML.
  const person = personCard(full.person, full);
  const about = topicButtons(full.topics, code => { searchBox.value = 'topic:' + code; searchText = searchBox.value; reloadList(); window.scrollTo({top: 0, behavior: 'smooth'}); });
  detail.replaceChildren(...head, ...(about ? [about] : []), ...(person ? [person] : []), body);
  const attachments = renderAttachments(full);
  if (attachments) detail.append(attachments);
  if (currentGroup && currentGroup.kind === 'person' && currentGroup.messages.length > 1) {
    const all = el('section', undefined, 'conversation-thread');
    all.append(el('h4', 'All messages with ' + currentGroup.title + ' · ' + currentGroup.messages.length));
    for (const t of currentGroup.messages) {
      const item = el('button', undefined, 'message');
      item.type = 'button';
      item.setAttribute('aria-pressed', String(t.id === id));
      if (t.unread) item.className += ' unread';
      detailRead.items.push({id: t.id, node: item});
      item.append(el('div', (t.direction === 'sent' ? 'Sent · ' : 'Received · ') + (t.subject || '(No subject)')),
        el('div', formatWhen(t.internal_date, t.date || 'No date') + ' — ' + (t.snippet || ''), 'meta'));
      item.addEventListener('click', () => selectMessage(t.id));
      all.append(item);
    }
    detail.append(all);
  }
  if (full.thread.length > 1 && !(currentGroup && currentGroup.kind === 'person')) {
    const thread = el('section', undefined, 'conversation-thread');
    thread.append(el('h4', 'Conversation · ' + full.thread.length + ' message(s)'));
    for (const t of full.thread) {
      const item = el('button', undefined, 'message');
      item.type = 'button';
      item.setAttribute('aria-pressed', String(t.id === id));
      if (t.unread) item.className += ' unread';
      detailRead.items.push({id: t.id, node: item});
      item.append(el('div', (t.direction === 'sent' ? 'Sent · To ' + (t.to || 'unknown') : 'Received · From ' + (t.from || 'unknown'))),
        el('div', formatWhen(t.internal_date, t.date || 'No date') + ' — ' + (t.snippet || ''), 'meta'));
      item.addEventListener('click', () => selectMessage(t.id));
      thread.append(item);
    }
    detail.append(thread);
  }
  const reply = iconButton('reply', 'Prepare an unsent reply draft');
  reply.className += ' action';
  reply.addEventListener('click', async () => {
    reply.disabled = true;
    try {
      const response = await request('/api/draft', 'POST', {id});
      if (selectedId === id) draft.replaceChildren(el('h4', 'Unsent draft'), el('pre', response.draft));
    } catch (error) {
      if (selectedId === id) draft.replaceChildren(el('p', error.message, 'error'));
    } finally { reply.disabled = false; }
  });
  const propose = iconButton('plus', 'Suggest task');
  propose.addEventListener('click', async () => {
    propose.disabled = true;
    try { await showProposal(id); }
    catch (error) { if (selectedId === id) detail.append(el('p', error.message, 'error')); }
    finally { propose.disabled = false; }
  });
  const bar = el('div', undefined, 'action-bar');
  if (full.direction !== 'sent') bar.append(reply);
  bar.append(propose);
  let note = null;
  if (full.direction !== 'sent') {
    const toggle = iconButton('check', 'Mark as read');
    detailRead.toggle = toggle;
    toggle.addEventListener('click', async () => {
      toggle.disabled = true;
      try { await setRead(id, readState.get(id) !== false); }  // Unread (or unknown) -> mark read; read -> mark unread.
      catch (error) { notify('Could not change the read state: ' + error.message, true); }
      toggle.disabled = false;
    });
    bar.append(toggle);
    note = el('p', writesToGmail(accounts.find(a => a.id === full.account_id)) ? 'Read and unread changes are also applied in Gmail.' : 'Read and unread changes are kept in Inbox Tend only; Gmail is not changed.', 'meta mt-2');
  }
  detail.append(bar);
  if (note) detail.append(note);
  // Opening a message marks it read. Shown as read at once; put back if the request fails.
  if (full.unread && userSettings.mark_read_on_open && full.direction !== 'sent') {
    readState.set(id, false);
    setRead(id, true).catch(() => { readState.set(id, true); paintRead(); });
  }
  paintRead();
  if (userSettings.ai_enabled) detail.append(aiControls(id, full.direction === 'sent'));
}
function aiControls(id, isSent) {
  const box = el('div', undefined, 'ai-actions');
  box.append(el('h4', 'AI assistant (' + aiProvider().name + ')'),
    el('p', 'Sends this message’s text to ' + aiProvider().vendor + ' through the server’s ' + aiProvider().name + ' login only when you press a button.', 'meta'));
  const status = el('p', '', 'meta');
  const guidance = document.createElement('input');
  guidance.type = 'text'; guidance.maxLength = 500; guidance.className = 'ai-guidance';
  guidance.placeholder = 'Optional: what should the reply say?';
  const suggest = iconButton('sparkles', 'Suggest task with AI');
  const write = iconButton('sparkles', 'Draft reply with AI');
  const run = async (button, work) => {
    suggest.disabled = write.disabled = true;
    status.textContent = 'Asking ' + aiProvider().name + '… this can take up to ' + userSettings.ai_timeout + ' seconds.';
    try { await work(); status.textContent = ''; }
    catch (error) { status.textContent = error.message; }
    suggest.disabled = write.disabled = false;
  };
  suggest.addEventListener('click', () => run(suggest, async () => {
    const result = await request('/api/ai/task', 'POST', {id});
    if (selectedId === id) await showProposal(id, result);
  }));
  write.addEventListener('click', () => run(write, async () => {
    const result = await request('/api/ai/reply', 'POST', {id, guidance: guidance.value});
    if (selectedId !== id) return;
    const copy = iconButton('copy', 'Copy');
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(result.draft); copy.lastChild.textContent = 'Copied'; } catch (_) { copy.lastChild.textContent = 'Copy failed'; }
    });
    draft.replaceChildren(el('h4', 'AI draft (not sent) — review and edit before using'), el('pre', result.draft), copy);
  }));
  box.append(suggest, el('span', ' '));
  if (!isSent) box.append(guidance, el('span', ' '), write);
  box.append(status);
  return box;
}

// ---- search and filters
const searchBox = document.getElementById('mail-search');
let searchTimer = null;
async function reloadList() {
  const mine = ++listRun;
  listLimit = PAGE;
  try {
    const data = await request(listUrl());
    if (mine === listRun) renderList(data);
  } catch (error) { document.getElementById('search-info').textContent = error.message; }
}
searchBox.addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { searchText = searchBox.value; reloadList(); }, 250);
});
searchBox.addEventListener('keydown', event => {
  if (event.key === 'Escape') { searchBox.value = ''; searchText = ''; clearTimeout(searchTimer); reloadList(); searchBox.blur(); }
});
document.addEventListener('keydown', event => {   // "/" jumps to the search box, like Gmail.
  const typing = /^(input|textarea|select)$/i.test((event.target && event.target.tagName) || '');
  if (event.key === '/' && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); searchBox.focus(); }
});
for (const [id, set] of [['filter-unread', v => { onlyUnread = v; }], ['filter-attachments', v => { onlyAttachments = v; }]]) {
  const button = document.getElementById(id);
  button.addEventListener('click', () => {
    const on = button.getAttribute('aria-pressed') !== 'true';
    button.setAttribute('aria-pressed', String(on)); set(on); reloadList();
  });
}
const viewButtons = {threads: document.getElementById('view-threads'), messages: document.getElementById('view-messages'), people: document.getElementById('view-people')};
function showViewState() {
  for (const [name, button] of Object.entries(viewButtons)) button.setAttribute('aria-pressed', String(name === view));
  viewButtons.people.textContent = folder === 'sent' ? 'Recipients' : 'People';
}
function chooseView(next) {
  if (view === next) return;
  view = next; currentGroup = null; selectedId = null; listLimit = PAGE;
  showViewState();
  detail.replaceChildren('Choose a message to see its details.'); draft.replaceChildren(); clearTranslation();
  userSettings.inbox_view = next;
  request('/api/settings', 'PATCH', {inbox_view: next}).catch(() => {});  // Remembered for next time.
  refreshGmail().catch(error => { gmailStatus.textContent = error.message; });
}
for (const [name, button] of Object.entries(viewButtons)) button.addEventListener('click', () => chooseView(name));
const folderInbox = document.getElementById('folder-inbox');
const folderSent = document.getElementById('folder-sent');
function chooseFolder(next) {
  if (folder === next) return;
  folder = next;
  folderInbox.setAttribute('aria-pressed', String(next === 'inbox'));
  folderSent.setAttribute('aria-pressed', String(next === 'sent'));
  selectedId = null; currentGroup = null; listLimit = PAGE;
  showViewState();
  detail.replaceChildren('Choose a message to see its details.'); draft.replaceChildren(); clearTranslation();
  refreshGmail().catch(error => { gmailStatus.textContent = error.message; });
}
folderInbox.addEventListener('click', () => chooseFolder('inbox'));
folderSent.addEventListener('click', () => chooseFolder('sent'));
const gmailStatus = document.getElementById('gmail-status');
const gmailRefresh = document.getElementById('gmail-refresh');
let refreshRun = 0;
function syncLabel(state) {
  return state.last_sync ? ' Last synced ' + formatWhen(state.last_sync * 1000) + '.' : '';
}
// The one place the UI reads "unread" from. Whatever learns something new (the list reloading during a sync,
// the toggle button, opening a message) updates it and calls paintRead(), so the open message follows live.
const readState = new Map();
let detailRead = null;  // What the open message's panel shows: {id, badge, toggle, items: [{id, node}]}
function paintRead() {
  if (!detailRead) return;
  const unread = readState.get(detailRead.id);
  if (unread !== undefined) {
    if (detailRead.badge) { detailRead.badge.textContent = unread ? 'Unread' : 'Read'; detailRead.badge.className = 'read-badge ' + (unread ? 'is-unread' : 'is-read'); }
    if (detailRead.toggle) detailRead.toggle.lastChild.textContent = unread ? 'Mark as read' : 'Mark as unread';
  }
  for (const item of detailRead.items) if (readState.has(item.id)) item.node.className = readState.get(item.id) ? 'message unread' : 'message';
}
// A row's title: the subject, then the small topic icons.
function subjectLine(text, codes) {
  const line = el('div', undefined, 'subject-line');
  line.append(el('span', text || '(No subject)', 'subject-text'));
  const icons = topicIcons(codes);
  if (icons) line.append(icons);
  return line;
}
// With several accounts, a small coloured dot says which account a message belongs to (the colour follows the account's
// position in the list; the address is its tooltip and, in the open message, written out).
const accountName = account => account.email || 'Gmail account ' + account.id;
function accountDot(id) {
  const at = accounts.findIndex(a => a.id === id);
  if (at < 0) return null;
  const dot = el('span', undefined, 'account-dot');
  dot.dataset.slot = String(at % 6);
  dot.title = accountName(accounts[at]);
  return dot;
}
function accountDots(ids) {
  if (accounts.length < 2 || accountFilter) return [];
  return [...new Set(ids)].map(accountDot).filter(Boolean);
}
// "[flag] name · 2 h ago": the line under a row's title.
function rowMeta(item, who, ms, fallbackDate, offsetMinutes) {
  const line = el('div', undefined, 'row-meta');
  for (const dot of accountDots(item && item.messages ? item.messages.map(m => m.account_id) : [item && item.account_id])) line.append(dot);
  const flag = flagEl(item);
  if (flag) line.append(flag);
  line.append(el('span', who, 'who'), el('span', '·'), agoSpan(ms, fallbackDate || 'No date'));
  // People rows: their clock, next to how long ago. Only when we know where they are: +00:00 is mostly a server, not a place.
  const there = userSettings.flags_enabled && item && item.country ? timeChip(offsetMinutes) : null;
  if (there) line.append(el('span', '·'), there);
  return line;
}
function rowButton(key, pressed, unread = false) {
  const button = el('button', undefined, unread ? 'message unread' : 'message');
  button.dataset.id = key;
  button.setAttribute('aria-pressed', String(pressed));
  return button;
}
function attachmentLabel(count) {
  const label = el('div', undefined, 'meta');
  label.append(icon('paperclip'), document.createTextNode(' ' + count + ' attachment(s)'));
  return label;
}
function renderList(data) {
  const previous = selectedId;
  list.replaceChildren();
  groups = data.groups || [];
  messages = data.groups ? groups.flatMap(g => g.messages) : data.messages;
  for (const m of messages) readState.set(m.id, m.unread);
  paintRead();
  if (!messages.some(m => m.id === previous)) {
    selectedId = null; currentGroup = null;
    detail.replaceChildren('Choose a message to see its details.'); draft.replaceChildren(); clearTranslation();
  } else if (currentGroup) {
    currentGroup = groups.find(g => g.id === currentGroup.id) || null;  // Fresh data for the open group.
  }
  lastData = data;
  const items = data.groups ? groups : messages;
  const noun = view === 'threads' ? 'conversation' : view === 'people' ? 'person' : 'message';
  const info = document.getElementById('search-info');
  info.textContent = filtersActive() ? (items.length ? items.length.toLocaleString() + ' ' + (noun === 'person' ? (items.length === 1 ? 'person' : 'people') : noun + (items.length === 1 ? '' : 's')) + ' match.' : 'No matches.') : '';
  if (!items.length) {
    list.textContent = filtersActive() ? 'Nothing matches your search.' : (folder === 'sent' ? 'No sent messages found.' : 'No inbox messages found.');
    return;
  }
  const openKey = data.groups ? (currentGroup && currentGroup.id) : selectedId;
  const openAt = items.findIndex(x => x.id === openKey);
  if (openAt >= listLimit) listLimit = Math.ceil((openAt + 1) / PAGE) * PAGE;   // Never hide the row that is open.
  const shown = items.slice(0, listLimit);
  // Rows are newest first, so each age forms one block; a coloured heading opens it.
  const ageOfItem = x => ageOf(data.groups ? x.last.internal_date : x.internal_date);
  const perAge = {today: 0, yesterday: 0, earlier: 0};
  for (const x of items) perAge[ageOfItem(x)]++;
  const AGE_LABEL = {today: 'Today', yesterday: 'Yesterday', earlier: 'Earlier'};
  let blockAge = null;
  const openBlock = (age, button) => {
    if (age !== blockAge) {
      const heading = el('h4', undefined, 'date-heading');
      heading.dataset.age = age;
      heading.append(el('span', AGE_LABEL[age]), el('span', String(perAge[age])));
      list.append(heading);
      blockAge = age;
    }
    button.dataset.age = age;
  };
  if (!data.groups) {
    for (const m of shown) {
      const button = rowButton(m.id, m.id === selectedId, m.unread);
      openBlock(ageOfItem(m), button);
      button.append(subjectLine(m.subject, m.topics),
        rowMeta(m, folder === 'sent' ? 'To ' + (m.to || 'unknown') : (m.from || 'Unknown sender'), m.internal_date, m.date));
      if (m.attachment_count) button.append(attachmentLabel(m.attachment_count));
      button.addEventListener('click', () => { currentGroup = null; selectMessage(m.id); });
      list.append(button);
    }
  } else {
    for (const g of shown) {
      const button = rowButton(g.id, currentGroup && g.id === currentGroup.id, g.unread_count > 0);
      openBlock(ageOfItem(g), button);
      const last = g.last;
      const top = el('div', undefined, 'group-title');
      top.append(el('span', g.title || '(No subject)', 'group-name'));
      const icons = topicIcons(g.topics);
      if (icons) top.append(icons);
      if (g.count > 1) {
        const badge = el('span', g.unread_count ? g.unread_count + '/' + g.count : String(g.count), g.unread_count ? 'count-badge unread-badge' : 'count-badge');
        badge.title = g.unread_count ? g.unread_count + ' unread of ' + g.count + ' messages' : g.count + ' messages';
        top.append(badge);
      }
      const who = g.kind === 'person' ? (g.address || '') : (g.participants || []).join(', ');
      button.append(top, rowMeta(g, who || 'Unknown', last.internal_date, last.date, g.kind === 'person' && g.person ? g.person.offset_minutes : null),
        el('div', (last.direction === 'sent' ? 'You: ' : '') + (g.kind === 'person' ? (last.subject || '') + ' — ' : '') + (last.snippet || ''), 'meta group-snippet'));
      const person = g.kind === 'person' && userSettings.flags_enabled ? g.person : null;
      const card = person ? [person.role, person.company, person.city].filter(Boolean).join(' · ') : '';
      if (card) button.append(el('div', card, 'person-line'));
      if (filtersActive() && g.matches && g.count > 1) button.append(el('div', g.matches + ' of ' + g.count + ' messages match', 'meta'));
      if (g.attachment_count) button.append(attachmentLabel(g.attachment_count));
      button.addEventListener('click', () => { currentGroup = g; selectMessage(g.last.id); });
      list.append(button);
    }
  }
  if (items.length > shown.length) {
    const more = el('button', 'Show ' + Math.min(PAGE, items.length - shown.length) + ' more (' + (items.length - shown.length).toLocaleString() + ' left)', 'more-button');
    more.type = 'button';
    more.addEventListener('click', () => { listLimit += PAGE; renderList(lastData); });
    list.append(more);
  }
}
// The page normally shows the local SQLite copy. A sync runs on the server in the background
// (full messages and attachments take a while); this polls its progress and refreshes the list.
async function refreshGmail(startSync = false) {
  const run = ++refreshRun;  // A newer refresh supersedes this one, so rows are never appended twice.
  if (startSync) {
    gmailRefresh.disabled = true;
    try { await request('/api/connectors/gmail/sync', 'POST', {}); }
    catch (error) { gmailRefresh.disabled = false; throw error; }
  }
  let lastSignature = null;
  for (;;) {
    const state = await request('/api/connectors/gmail/status');
    if (run !== refreshRun) return;
    gmailRefresh.hidden = !state.connected || !state.configured;
    if (!state.connected) {
      const link = el('a', 'Settings → Gmail'); link.href = '/user/settings';
      if (state.configured) gmailStatus.replaceChildren('Gmail is not connected. Connect it in ', link, ' to see your mail.');
      else gmailStatus.replaceChildren('Gmail is not configured on this server (see docs/gmail-web.md). Details in ', link, '.');
      list.replaceChildren(el('p', 'No mail yet: connect Gmail in Settings.', 'meta'));
      detail.replaceChildren('Choose a message to see its details.'); draft.replaceChildren(); clearTranslation();
      messages = []; selectedId = null; gmailRefresh.disabled = false;
      return;
    }
    accounts = state.accounts || [];
    showAccountFilter();
    const scoped = accountFilter ? accounts.find(a => String(a.id) === accountFilter) : null;
    const shown = scoped ? Object.assign({}, state, {counts: scoped.counts, remote: scoped.remote, stored: scoped.stored, last_sync: scoped.last_sync}) : state;   // The numbers follow the account filter.
    const sync = state.sync;
    if (!sync.running && accounts.some(a => !a.last_sync) && !sync.error && !startSync) {  // First visit after connecting an account.
      try { await request('/api/connectors/gmail/sync', 'POST', {}); } catch (error) { gmailStatus.textContent = error.message; }
      startSync = true; continue;
    }
    const guess = state.flags || {};
    if (userSettings.flags_enabled && guess.pending > 0 && !guess.running && !flagsAttempted) {   // Once per page load: a failing AI must not loop.
      flagsAttempted = true;
      try { await request('/api/flags/guess', 'POST', {}); } catch (error) { notify(error.message, true); }
      continue;
    }
    const labels = state.topics || {};
    if (userSettings.topics_enabled && labels.pending > 0 && !labels.running && !topicsAttempted) {   // Once per page load, like the flags.
      topicsAttempted = true;
      try { await request('/api/topics/guess', 'POST', {}); } catch (error) { notify(error.message, true); }
      continue;
    }
    const busy = sync.running || guess.running || labels.running;
    gmailRefresh.disabled = sync.running;
    // While a sync runs, fetch the list again only when its progress moved: a big mailbox is a big response.
    const signature = busy ? [sync.done, sync.failed, sync.total, guess.done, labels.done].join(':') : null;
    if (!busy || signature !== lastSignature || !lastData) {
      const mine = ++listRun;
      const data = await request(listUrl());
      if (run !== refreshRun) return;
      if (mine === listRun) renderList(data);   // A newer search may already have drawn its own result.
      lastSignature = signature;
    }
    showCounts(shown.counts);
    showTotals(shown);
    gmailWriteAccess = state.write_access;
    gmailStatus.textContent = (accounts.length > 1 ? accounts.length + ' Gmail accounts connected. ' : 'Gmail connected. ') + (scoped ? accountName(scoped) + ': ' : '') + shown.stored + ' message(s) saved locally (inbox and sent).' + syncLabel(shown) +
      (sync.running ? ' Syncing… ' + sync.done + ' of ' + sync.total + ' new message(s).' + (sync.rate_limited_s > 0 ? ' Gmail is limiting requests, so the download is paused for about ' + sync.rate_limited_s + ' s and then continues.' : '') : '') +
      (!sync.running && sync.error ? ' ' + sync.error : '') +
      (guess.running ? ' Guessing countries… ' + guess.done + ' of ' + guess.total + ' senders.' : '') +
      (!guess.running && guess.error ? ' Sender details: ' + guess.error : '') +
      (labels.running ? ' Labelling topics… ' + labels.done + ' of ' + labels.total + ' messages.' : '') +
      (!labels.running && labels.error ? ' Topic icons: ' + labels.error : '');
    if (!busy) return;
    await new Promise(resolve => setTimeout(resolve, 2000));
    if (run !== refreshRun) return;
  }
}
// The account chooser appears only when there is more than one account to choose from.
const accountSelect = document.getElementById('account-filter');
function showAccountFilter() {
  if (accountFilter && !accounts.some(a => String(a.id) === accountFilter)) { accountFilter = ''; sessionStorage.removeItem('inboxtend-account'); }
  const wanted = ['', ...accounts.map(a => String(a.id))].join(',');
  if (accountSelect.dataset.options !== wanted) {
    accountSelect.dataset.options = wanted;
    accountSelect.replaceChildren(new Option('All accounts (' + accounts.length + ')', ''), ...accounts.map(a => new Option(accountName(a), String(a.id))));
  }
  accountSelect.value = accountFilter;
  document.getElementById('account-filter-label').hidden = accounts.length < 2;
}
accountSelect.addEventListener('change', () => {
  accountFilter = accountSelect.value;
  if (accountFilter) sessionStorage.setItem('inboxtend-account', accountFilter); else sessionStorage.removeItem('inboxtend-account');
  listLimit = PAGE; selectedId = null; currentGroup = null;
  detail.replaceChildren('Choose a message to see its details.'); draft.replaceChildren(); clearTranslation();
  refreshGmail().catch(error => { gmailStatus.textContent = error.message; });
});
gmailRefresh.addEventListener('click', async () => {
  try { await refreshGmail(true); } catch (error) { gmailStatus.textContent = error.message; }
});

async function init() {
  try { await sessionReady; } catch (error) { notify('Could not load session: ' + error.message, true); return; }
  await settingsReady;
  view = ['threads', 'messages', 'people'].includes(userSettings.inbox_view) ? userSettings.inbox_view : 'threads';
  showViewState();
  if (userSettings.translate_enabled) {
    translationPanel.hidden = false;
    document.getElementById('inbox-grid').classList.add('has-translation');
  }
  try { await refreshGmail(); } catch (error) { gmailStatus.textContent = error.message; list.textContent = ''; }
}
init();
