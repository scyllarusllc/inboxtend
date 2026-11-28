'use strict';
// One email on its own page: /user/mail/<id>.
const mailBox = document.getElementById('mail-detail');
const mailId = decodeURIComponent(location.pathname.slice('/user/mail/'.length));
const mailLink = id => '/user/mail/' + encodeURIComponent(id);

function mailPanel(title, ...children) {
  const box = el('section', undefined, 'panel task-section');
  box.append(el('h2', title), ...children);
  return box;
}

function threadList(title, items) {
  const box = el('div', undefined, 'conversation-thread');
  for (const t of items) {
    const item = el('a', undefined, 'message');
    item.href = mailLink(t.id);
    if (t.id === mailId) item.setAttribute('aria-current', 'page');
    if (t.unread) item.className += ' unread';
    item.append(el('div', t.direction === 'sent' ? 'To ' + (t.to || 'unknown') : 'From ' + (t.from || 'unknown')),
      el('div', formatWhen(t.internal_date, t.date || 'No date') + ' — ' + (t.snippet || ''), 'meta'));
    box.append(item);
  }
  return mailPanel(title, box);
}

async function taskFor(message) {
  try {
    const result = await request('/api/tasks');
    return result.tasks.find(task => task.message_id === message.id) || null;
  } catch (_) { return null; }
}

async function renderMail(m) {
  document.title = (m.subject || '(No subject)') + ' · Inbox Tend';
  mailBox.replaceChildren();
  const card = el('article', undefined, 'panel mail-card');
  card.append(el('h1', m.subject || '(No subject)'));
  const who = el('div', undefined, 'meta');
  who.append(document.createTextNode('From ' + (m.from || 'unknown') + ' · ' + formatWhen(m.internal_date, m.date || 'No date')));
  card.append(who);
  if (m.to) card.append(el('div', 'To ' + m.to, 'meta'));
  if (m.cc) card.append(el('div', 'Cc ' + m.cc, 'meta'));
  card.append(mailBodyView(m));

  const bar = el('div', undefined, 'action-bar');
  if (m.direction !== 'sent') {
    const toggle = iconButton('check', m.unread ? 'Mark as read' : 'Mark as unread');
    toggle.addEventListener('click', async () => {
      toggle.disabled = true;
      try {
        const result = await request('/api/connectors/gmail/messages/' + encodeURIComponent(m.id) + '/read', 'POST', {read: m.unread});
        if (result.gmail_error) notify('Saved here, but the mail server was not updated: ' + result.gmail_error, true);
        m.unread = result.unread;
        toggle.replaceChildren(icon('check'), document.createTextNode(m.unread ? 'Mark as read' : 'Mark as unread'));
      } catch (error) { notify(error.message, true); }
      toggle.disabled = false;
    });
    bar.append(toggle);
  }
  if (m.direction !== 'sent' && m.sender_address) bar.append(archiveSenderButton(m, state => { m.sender_archived = state; }));
  const task = await taskFor(m);
  if (task) {
    const open = el('a', undefined, 'icon-button');
    open.href = '/user/task/' + task.id;
    open.append(icon('list-check'), document.createTextNode(' Open task'));
    bar.append(open);
  } else {
    const create = iconButton('plus', 'Create task from this email');
    create.addEventListener('click', async () => {
      create.disabled = true;
      try {
        const base = await request('/api/connectors/gmail/messages/' + encodeURIComponent(m.id) + '/proposal');
        const f = base.inferred;
        const created = await request('/api/tasks', 'POST', {message_id: m.id, title: f.title, description: f.description,
          priority: f.priority, due_date: null, due_time: null, due_timezone: f.due_timezone || userSettings.timezone});
        location.assign('/user/task/' + created.task.id);
      } catch (error) { notify(error.message, true); create.disabled = false; }
    });
    bar.append(create);
  }
  const inbox = el('a', undefined, 'icon-button');
  inbox.href = '/user/inbox?message=' + encodeURIComponent(m.id);
  inbox.append(icon('mail'), document.createTextNode(' Open in Inbox (translate, reply)'));
  bar.append(inbox);
  card.append(bar);
  mailBox.append(card);

  const files = renderAttachments(m);
  if (files) mailBox.append(mailPanel('Attachments', files));
  if (m.thread && m.thread.length > 1) mailBox.append(threadList('Conversation · ' + m.thread.length + ' messages', m.thread));
}

async function init() {
  try { await sessionReady; } catch (error) { notify('Could not load session: ' + error.message, true); return; }
  await settingsReady;
  try {
    const m = (await request('/api/connectors/gmail/messages/' + encodeURIComponent(mailId))).message;
    // Like the Inbox: opening a message marks it read (when that setting is on).
    if (m.unread && userSettings.mark_read_on_open && m.direction !== 'sent') {
      try { const r = await request('/api/connectors/gmail/messages/' + encodeURIComponent(m.id) + '/read', 'POST', {read: true}); m.unread = r.unread; }
      catch (error) { notify('Could not mark the message as read: ' + error.message, true); }
    }
    await renderMail(m);
  } catch (error) {
    mailBox.replaceChildren();
    const box = el('div', undefined, 'panel');
    box.append(el('h1', 'Could not open this email'), el('p', error.message, 'meta'));
    mailBox.append(box);
  }
}
init();
