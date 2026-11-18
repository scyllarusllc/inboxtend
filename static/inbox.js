'use strict';
const list = document.getElementById('messages');
const detail = document.getElementById('detail');
const draft = document.getElementById('draft');
let messages = [], selectedId = null, folder = 'inbox';
async function showProposal(messageId) {
  const m = messages.find(message => message.id === messageId);
  if (!m) return;
  const proposal = await request('/api/connectors/gmail/messages/' + encodeURIComponent(messageId) + '/proposal');
  if (selectedId !== messageId) return;
  const form = el('form', undefined, 'task-proposal');
  form.append(el('h4', 'Review suggested task'), el('p', proposal.note, 'meta'));
  const f = proposal.inferred;
  field(form, 'Title', 'title', f.title).required = true;
  field(form, 'Description', 'description', f.description, 'textarea');
  field(form, 'Owner (optional)', 'owner', '');
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
    data.due_date = data.due_date.trim() || null;
    data.due_time = data.due_time.trim() || null;
    data.due_timezone = 'Pacific/Saipan';
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
      const download = el('a', 'Download'); download.href = base; download.setAttribute('download', file.name);
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
async function selectMessage(id) {
  selectedId = id;
  const m = messages.find(message => message.id === id) || {id, subject: '', from: '', date: ''};
  for (const button of list.querySelectorAll('button.message')) {
    button.setAttribute('aria-pressed', String(button.dataset.id === id));
  }
  draft.replaceChildren();
  detail.replaceChildren(el('h3', m.subject || '(No subject)'),
    el('div', (m.from || 'Unknown sender') + ' · ' + (m.date || 'No date'), 'meta'), el('p', 'Loading message…'));
  let full;
  try { full = (await request('/api/connectors/gmail/messages/' + encodeURIComponent(id))).message; }
  catch (error) { if (selectedId === id) detail.append(el('p', error.message, 'error')); return; }
  if (selectedId !== id) return;
  const head = [el('h3', full.subject || '(No subject)'),
    el('div', 'From ' + (full.from || 'unknown') + ' · ' + (full.date || 'No date'), 'meta')];
  if (full.to) head.push(el('div', 'To ' + full.to, 'meta'));
  if (full.cc) head.push(el('div', 'Cc ' + full.cc, 'meta'));
  head[1] = el('div', (full.direction === 'sent' ? 'Sent' : 'Received') + ' · From ' + (full.from || 'unknown') + ' · ' + (full.date || 'No date'), 'meta');
  const body = el('pre', full.body_text || full.snippet || '(No text content)', 'message-body');  // Text only, never HTML.
  detail.replaceChildren(...head, body);
  const attachments = renderAttachments(full);
  if (attachments) detail.append(attachments);
  if (full.thread.length > 1) {
    const thread = el('section', undefined, 'conversation-thread');
    thread.append(el('h4', 'Conversation · ' + full.thread.length + ' message(s)'));
    for (const t of full.thread) {
      const item = el('button', undefined, 'message');
      item.type = 'button';
      item.setAttribute('aria-pressed', String(t.id === id));
      item.append(el('div', (t.direction === 'sent' ? 'Sent · To ' + (t.to || 'unknown') : 'Received · From ' + (t.from || 'unknown'))),
        el('div', (t.date || 'No date') + ' — ' + (t.snippet || ''), 'meta'));
      item.addEventListener('click', () => selectMessage(t.id));
      thread.append(item);
    }
    detail.append(thread);
  }
  const reply = iconButton('mail', 'Prepare an unsent reply draft');
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
  detail.append(full.direction === 'sent' ? propose : reply, ...(full.direction === 'sent' ? [] : [el('span', ' '), propose]));
}

const folderInbox = document.getElementById('folder-inbox');
const folderSent = document.getElementById('folder-sent');
function chooseFolder(next) {
  if (folder === next) return;
  folder = next;
  folderInbox.setAttribute('aria-pressed', String(next === 'inbox'));
  folderSent.setAttribute('aria-pressed', String(next === 'sent'));
  selectedId = null;
  detail.replaceChildren('Choose a message to see its details.'); draft.replaceChildren();
  refreshGmail().catch(error => { gmailStatus.textContent = error.message; });
}
folderInbox.addEventListener('click', () => chooseFolder('inbox'));
folderSent.addEventListener('click', () => chooseFolder('sent'));
const gmailStatus = document.getElementById('gmail-status');
const gmailConnect = document.getElementById('gmail-connect');
const gmailDisconnect = document.getElementById('gmail-disconnect');
const gmailRefresh = document.getElementById('gmail-refresh');
let refreshRun = 0;
function syncLabel(state) {
  return state.last_sync ? ' Last synced ' + new Date(state.last_sync * 1000).toLocaleString() + '.' : '';
}
function renderList(data) {
  const previous = selectedId;
  list.replaceChildren();
  messages = data.messages;
  if (!messages.some(m => m.id === previous)) { selectedId = null; detail.replaceChildren('Choose a message to see its details.'); draft.replaceChildren(); }
  if (!messages.length) { list.textContent = folder === 'sent' ? 'No sent messages found.' : 'No inbox messages found.'; return; }
  for (const m of messages) {
    const button = el('button', undefined, 'message');
    button.dataset.id = m.id;
    button.setAttribute('aria-pressed', String(m.id === selectedId));
    button.append(el('div', m.subject || '(No subject)'),
      el('div', (folder === 'sent' ? 'To ' + (m.to || 'unknown') : (m.from || 'Unknown sender')) + ' · ' + (m.date || 'No date'), 'meta'));
    if (m.attachment_count) {
      const label = el('div', undefined, 'meta');
      label.append(icon('paperclip'), document.createTextNode(' ' + m.attachment_count + ' attachment(s)'));
      button.append(label);
    }
    button.addEventListener('click', () => selectMessage(m.id));
    list.append(button);
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
  for (;;) {
    const state = await request('/api/connectors/gmail/status');
    if (run !== refreshRun) return;
    gmailConnect.hidden = !state.configured;
    gmailConnect.textContent = state.connected ? 'Reconnect Gmail' : 'Connect Gmail';
    gmailDisconnect.hidden = !state.connected;
    gmailRefresh.hidden = !state.connected;
    if (!state.connected) {
      gmailStatus.textContent = state.configured ? 'Gmail is not connected. Connect it to review your inbox.' : 'Gmail connection is not configured. See docs/gmail-web.md.';
      list.textContent = 'Connect Gmail to see your inbox.';
      detail.replaceChildren('Choose a message to see its details.'); draft.replaceChildren();
      messages = []; selectedId = null; gmailRefresh.disabled = false;
      return;
    }
    const sync = state.sync;
    if (!sync.running && !state.last_sync && !sync.error && !startSync) {  // First visit after connecting.
      try { await request('/api/connectors/gmail/sync', 'POST', {}); } catch (error) { gmailStatus.textContent = error.message; }
      startSync = true; continue;
    }
    gmailRefresh.disabled = sync.running;
    const data = await request('/api/connectors/gmail/messages?folder=' + folder);
    if (run !== refreshRun) return;
    renderList(data);
    folderInbox.textContent = 'Inbox (' + state.counts.inbox + ')';
    folderSent.textContent = 'Sent (' + state.counts.sent + ')';
    gmailStatus.textContent = 'Gmail connected. ' + state.stored + ' message(s) saved locally (inbox and sent).' + syncLabel(state) +
      (sync.running ? ' Syncing… ' + sync.done + ' of ' + sync.total + ' new message(s).' : '') +
      (!sync.running && sync.error ? ' ' + sync.error : '');
    if (!sync.running) return;
    await new Promise(resolve => setTimeout(resolve, 2000));
    if (run !== refreshRun) return;
  }
}
gmailConnect.addEventListener('click', async () => {
  gmailConnect.disabled = true;
  try {
    const result = await request('/api/connectors/gmail/connect','POST',{});
    location.assign(result.authorization_url);
  } catch (error) { gmailStatus.textContent = error.message; gmailConnect.disabled = false; }
});
gmailDisconnect.addEventListener('click', async () => {
  gmailDisconnect.disabled = true;
  try { await request('/api/connectors/gmail/disconnect','POST',{}); await refreshGmail(); }
  catch (error) { gmailStatus.textContent = error.message; }
  finally { gmailDisconnect.disabled = false; }
});
gmailRefresh.addEventListener('click', async () => {
  try { await refreshGmail(true); } catch (error) { gmailStatus.textContent = error.message; }
});

async function init() {
  try { await sessionReady; } catch (error) { notify('Could not load session: ' + error.message, true); return; }
  try { await refreshGmail(); } catch (error) { gmailStatus.textContent = error.message; list.textContent = ''; }
}
init();
