'use strict';
// The Archived page: the senders on the archived list, and the mail filed under them.
const sendersBox = document.getElementById('senders');
const mailBoxEl = document.getElementById('archived-mail');
const PAGE_SIZE = 100;
let archivedMessages = [], shown = PAGE_SIZE;

function senderRow(s) {
  const row = el('div', undefined, 'message');
  row.style.flexDirection = 'row';
  row.style.alignItems = 'center';
  row.style.justifyContent = 'space-between';
  row.style.gap = '0.75rem';
  const text = el('div', undefined, 'min-w-0');
  text.append(el('div', s.name || s.address), el('div', (s.name ? s.address + ' · ' : '') + s.messages + ' archived message' + (s.messages === 1 ? '' : 's'), 'meta'));
  const remove = iconButton('xmark', 'Remove');
  remove.title = 'Stop archiving this sender; their mail goes back to the Inbox';
  remove.addEventListener('click', async () => {
    if (!confirm('Stop archiving ' + (s.name || s.address) + '? Their ' + s.messages + ' message(s) go back to the Inbox.')) return;
    remove.disabled = true;
    try {
      const result = await request('/api/archived/senders/remove', 'POST', {address: s.address});
      notify(result.restored + ' message(s) are back in the Inbox.');
      await load();
    } catch (error) { notify(error.message, true); remove.disabled = false; }
  });
  row.append(text, remove);
  return row;
}

function mailRow(m) {
  const item = el('a', undefined, 'message' + (m.unread ? ' unread' : ''));
  item.href = '/user/mail/' + encodeURIComponent(m.id);
  item.append(el('div', m.from || 'Unknown sender'), el('div', m.subject || '(No subject)'),
    el('div', formatWhen(m.internal_date, m.date || 'No date') + (m.snippet ? ' — ' + m.snippet : ''), 'meta'));
  return item;
}

function renderMail() {
  mailBoxEl.replaceChildren();
  document.getElementById('mail-caption').textContent = archivedMessages.length ? archivedMessages.length + ' message(s)' : '';
  if (!archivedMessages.length) { mailBoxEl.textContent = 'No archived mail yet.'; return; }
  const list = el('div', undefined, 'grid gap-2');
  for (const m of archivedMessages.slice(0, shown)) list.append(mailRow(m));
  mailBoxEl.append(list);
  if (archivedMessages.length > shown) {
    const more = iconButton('plus', 'Show more (' + (archivedMessages.length - shown) + ' left)');
    more.className += ' mt-3';
    more.addEventListener('click', () => { shown += PAGE_SIZE; renderMail(); });
    mailBoxEl.append(more);
  }
}

async function load() {
  const [senders, listing] = await Promise.all([
    request('/api/archived/senders'),
    request('/api/connectors/gmail/messages?folder=archived&view=messages').catch(error => ({messages: [], error: error.message}))]);
  sendersBox.replaceChildren();
  document.getElementById('senders-caption').textContent = senders.senders.length ? senders.senders.length + ' sender(s)' : '';
  if (!senders.senders.length) sendersBox.textContent = 'No archived senders yet.';
  else { const list = el('div', undefined, 'grid gap-2'); for (const s of senders.senders) list.append(senderRow(s)); sendersBox.append(list); }
  archivedMessages = listing.messages || [];
  renderMail();
  if (listing.error) mailBoxEl.append(el('p', listing.error, 'error'));
}

(async () => {
  try { await sessionReady; await settingsReady; await load(); }
  catch (error) { notify(error.message, true); sendersBox.textContent = ''; mailBoxEl.textContent = ''; }
})();
