'use strict';
const list = document.getElementById('messages');
const detail = document.getElementById('detail');
const draft = document.getElementById('draft');
let messages = [], selectedId = null;
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
function selectMessage(id) {
  selectedId = id;
  const m = messages.find(message => message.id === id);
  if (!m) return;
  for (const button of list.querySelectorAll('button')) {
    button.setAttribute('aria-pressed', String(button.dataset.id === id));
  }
  detail.replaceChildren(
    el('h3', m.subject || '(No subject)'),
    el('div', (m.from || 'Unknown sender') + ' · ' + (m.date || 'No date'), 'meta'),
    el('h4', 'Snippet from Gmail (not AI-generated)'), el('p', m.snippet || 'No snippet')
  );
  const reply = iconButton('mail','Prepare an unsent reply draft');
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
  const propose = iconButton('plus','Suggest task');
  propose.addEventListener('click', async () => {
    propose.disabled = true;
    try { await showProposal(id); }
    catch (error) { if (selectedId === id) detail.append(el('p', error.message, 'error')); }
    finally { propose.disabled = false; }
  });
  detail.append(reply, el('span', ' '), propose);
  draft.replaceChildren();
}

const gmailStatus = document.getElementById('gmail-status');
const gmailConnect = document.getElementById('gmail-connect');
const gmailDisconnect = document.getElementById('gmail-disconnect');
const gmailRefresh = document.getElementById('gmail-refresh');
async function refreshGmail() {
  const state = await request('/api/connectors/gmail/status');
  gmailConnect.hidden = !state.configured;
  gmailConnect.textContent = state.connected ? 'Reconnect Gmail' : 'Connect Gmail';
  gmailDisconnect.hidden = !state.connected;
  gmailRefresh.hidden = !state.connected;
  gmailStatus.textContent = state.connected ? 'Gmail connected. Only inbox headers and snippets are shown.' :
    (state.configured ? 'Gmail is not connected. Connect it to review your inbox.' : 'Gmail connection is not configured. See docs/gmail-web.md.');
  list.replaceChildren();
  detail.replaceChildren('Choose a message to see its details.');
  draft.replaceChildren();
  messages = []; selectedId = null;
  if (!state.connected) { list.textContent = 'Connect Gmail to see your inbox.'; return; }
  const data = await request('/api/connectors/gmail/messages');
  messages = data.messages;
  if (!messages.length) { list.textContent = 'No inbox messages found.'; return; }
  for (const m of messages) {
    const button = el('button', undefined, 'message');
    button.dataset.id = m.id;
    button.setAttribute('aria-pressed', 'false');
    button.append(el('div', m.subject || '(No subject)'), el('div', (m.from || 'Unknown sender') + ' · ' + (m.date || 'No date'), 'meta'));
    button.addEventListener('click', () => selectMessage(m.id));
    list.append(button);
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
  try { await refreshGmail(); } catch (error) { gmailStatus.textContent = error.message; }
});

async function init() {
  try { await sessionReady; } catch (error) { notify('Could not load session: ' + error.message, true); return; }
  try { await refreshGmail(); } catch (error) { gmailStatus.textContent = error.message; list.textContent = ''; }
}
init();
