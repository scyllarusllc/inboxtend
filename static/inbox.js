'use strict';
const list = document.getElementById('messages');
const detail = document.getElementById('detail');
const relatedMessages = document.getElementById('related-messages');
const draft = document.getElementById('draft');
let messages = [], selectedId = null, folder = 'inbox', view = 'people', groups = [], currentGroup = null, gmailWriteAccess = false;
// What the list shows: search words (Gmail-style operators work), two quick filters, and how many rows are drawn.
let flagsAttempted = false, topicsAttempted = false, websitesAttempted = false;
const websiteLink = document.getElementById('message-website-link');
function clearWebsiteLink() { websiteLink.hidden = true; websiteLink.removeAttribute('href'); }
function showWebsiteLink(url) {
  clearWebsiteLink();
  if (!url) return;
  websiteLink.href = url;
  websiteLink.title = 'AI guess from links or sender domain in this email; website ownership was not verified.';
  websiteLink.hidden = false;
}
function directionMark(direction) {
  const sent = direction === 'sent';
  const label = sent ? 'Sent' : 'Received';
  const mark = el('span', undefined, 'mail-direction');
  mark.title = label;
  mark.append(icon(sent ? 'sent' : 'received'), el('span', label, 'sr-only'));
  return mark;
}
// Connected Gmail accounts (from the status) and the one the list is narrowed to ('' = all of them).
let accounts = [], accountFilter = sessionStorage.getItem('inboxtend-account') || '';
let searchText = '', onlyUnread = false, onlyAttachments = false, listLimit = 100, lastData = null, listRun = 0;
const unreadPreferenceKey = 'inboxtend-inbox-unread';
try { onlyUnread = localStorage.getItem(unreadPreferenceKey) === 'true'; } catch (_) { /* Storage may be unavailable. */ }
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
// The third column uses offline language packs; saved translations are reused on reopening.
async function showTranslation(id, force = false) {
  const language = userSettings.translate_language;
  translationCaption.textContent = language;
  if (!language) { translationBox.replaceChildren(el('p', 'Choose the language to translate into on the Settings page.', 'meta')); return; }
  translationBox.replaceChildren(el('p', 'Translating locally… the result will be saved for next time.', 'meta'));
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
    nodes.push(linkedText(result.body || '(No text content)', 'message-body'));  // Text only, never HTML; web addresses become links.
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
    if (!proposal.ai.evidence.length) box.append(el('p', proposal.ai.image_review ? 'Image contents cannot be quote-verified here; check the previews above.' : 'The AI gave no supporting quote.', 'meta'));
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
  (detail.querySelector('.task-slot') || detail).append(form);
  form.scrollIntoView({behavior:'smooth',block:'nearest'});
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
    const kind = file.mime_type.startsWith('image/') ? 'Image' : file.mime_type === 'application/pdf' ? 'PDF document' :
      (file.mime_type.startsWith('text/') || /\.(txt|csv)$/i.test(file.name)) ? 'Text document' : 'File';
    meta.append(el('strong', file.name || 'Unnamed attachment'), el('span', kind + ' · ' + formatBytes(file.size_bytes) +
      (file.stored ? '' : ' · not stored (' + (file.skipped_reason || 'unavailable') + ')'), 'meta'));
    row.append(meta);
    if (file.stored) {
      const base = '/api/connectors/gmail/messages/' + encodeURIComponent(message.id) + '/attachments/' + file.id;
      const download = el('a', 'Download', 'icon-button'); download.href = base; download.setAttribute('download', file.name); download.prepend(icon('download'));
      row.append(download);
      if (file.mime_type === 'text/plain' || file.mime_type === 'text/csv' || /\.(txt|csv)$/i.test(file.name)) {
        const preview = el('pre', 'Loading text preview…', 'attachment-preview');
        row.append(preview);
        request(base + '/preview').then(result => {
          const content = result.content || '';
          const short = content.length > 800;
          preview.textContent = short ? content.slice(0,800) + '\n…' : content || '(Empty text file)';
          if (short) {
            const expand = iconButton('eye', 'Show more');
            expand.addEventListener('click', () => {
              const open = expand.getAttribute('aria-expanded') === 'true';
              expand.setAttribute('aria-expanded',String(!open));
              expand.lastChild.textContent = open ? 'Show more' : 'Show less';
              preview.textContent = open ? content.slice(0,800) + '\n…' : content + (result.truncated ? '\n… (preview truncated)' : '');
            });
            row.append(expand);
          }
        }).catch(error => { preview.textContent = 'Preview unavailable: ' + error.message; });
      } else if (['image/png','image/jpeg','image/gif','image/webp'].includes(file.mime_type) && file.size_bytes <= 2 * 1024 * 1024) {
        const image = document.createElement('img');
        image.className = 'attachment-image'; image.alt = 'Preview of ' + (file.name || 'image attachment');
        image.loading = 'lazy'; image.src = base + '/thumbnail';
        image.addEventListener('error', () => image.replaceWith(el('p', 'Image preview unavailable.', 'meta')));
        row.append(image);
      }
    } else {
      row.append(el('p', 'Preview unavailable because this file was not saved on the server.', 'meta'));
    }
    block.append(row);
  }
  block.append(el('p', 'Only plain text and verified raster images are previewed. Other files are downloads.', 'meta'));
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
let shownCounts = null;
function showCounts(counts) {
  shownCounts = {...counts};
  folderInbox.textContent = 'Inbox (' + counts.inbox + (counts.unread ? ' · ' + counts.unread + ' unread' : '') + ')';
  folderSent.textContent = 'Sent (' + counts.sent + ')';
}
const pendingRead = new Map();
const readVersion = new Map();
function updateReadLocally(id, unread) {
  const previous = readState.get(id);
  readState.set(id, unread);
  for (const m of messages) if (m.id === id) m.unread = unread;
  for (const g of groups) g.unread_count = g.messages.filter(m => m.unread).length;
  if (previous !== undefined && previous !== unread && lastData?.unread_in_view != null) {
    lastData.unread_in_view = Math.max(0, lastData.unread_in_view + (unread ? 1 : -1));
    showMarkAll(lastData);
  }
  if (previous !== undefined && previous !== unread && shownCounts) showCounts({...shownCounts, unread: Math.max(0, shownCounts.unread + (unread ? 1 : -1))});
  paintRead();
  if (view !== 'people' || !groups.length) { renderList(groups.length ? {groups} : {messages}); return; }
  const group = groups.find(g => g.messages.some(m => m.id === id));
  if (!group) return;
  const row = [...list.querySelectorAll('.row-wrap')].find(w => w.querySelector('.message')?.dataset.id === group.id);
  if (!row) return;
  const button = row.querySelector('.message');
  button.classList.toggle('unread', group.unread_count > 0);
  const badge = button.querySelector('.count-badge');
  if (badge) {
    badge.textContent = group.unread_count ? group.unread_count + '/' + group.count : String(group.count);
    badge.classList.toggle('unread-badge', group.unread_count > 0);
    badge.title = group.unread_count ? group.unread_count + ' unread of ' + group.count + ' messages' : group.count + ' messages';
  }
  const mark = row.querySelector('.row-read');
  if (mark) {
    mark.hidden = group.unread_count === 0;
    mark.title = 'Mark ' + group.unread_count + ' unread message(s) from this person as read';
    mark.setAttribute('aria-label', mark.title);
  }
  if (onlyUnread && !group.unread_count && currentGroup?.id !== group.id && !pendingRead.has(id)) retireRow(row, group.id);
}
const retiringRows = new WeakSet();
let retiringCount = 0, queuedListData = null, retiringContext = '';
const listContext = () => [folder, view, accountFilter, searchText, onlyUnread, onlyAttachments].join('|');
function retireRow(row, groupId) {
  if (retiringRows.has(row)) return;
  retiringRows.add(row);
  retiringCount++;
  retiringContext = listContext();
  const finish = () => {
    retiringRows.delete(row);
    retiringCount--;
    if (!retiringCount && queuedListData) {
      const data = queuedListData;
      queuedListData = null;
      if (retiringContext === listContext()) renderList(data);
    }
  };
  const eligible = () => {
    const group = groups.find(g => g.id === groupId);
    return row.isConnected && group && !group.unread_count && currentGroup?.id !== groupId && view === 'people' && onlyUnread;
  };
  const attempt = () => {
    if (!eligible()) { finish(); return; }
    // Keep the clicked row under the pointer until the pointer moves away from it.
    if (row.matches(':hover')) {
      row.addEventListener('pointerleave', () => setTimeout(attempt, 150), {once: true});
      return;
    }
    row.classList.add('read-leaving');
    setTimeout(() => {
      if (!eligible()) { row.classList.remove('read-leaving'); finish(); return; }
      row.classList.remove('read-leaving');
      row.classList.add('read-collapsing');
      setTimeout(() => {
        if (eligible()) {
          row.remove();
          groups = groups.filter(g => g.id !== groupId);
          messages = groups.flatMap(g => g.messages);
        } else row.classList.remove('read-collapsing');
        finish();
      }, 320);
    }, 400);
  };
  setTimeout(attempt, userSettings.unread_dismiss_delay_ms ?? 1000);
}
function settleReadUi(id) {
  if (view !== 'people' || !onlyUnread) return;
  const group = groups.find(g => g.messages.some(m => m.id === id));
  if (!group || group.unread_count || currentGroup?.id === group.id) return;
  const row = [...list.querySelectorAll('.row-wrap')].find(w => w.querySelector('.message')?.dataset.id === group.id);
  if (row) retireRow(row, group.id);
}
// Update the current row immediately; a failed save restores its previous state.
async function setRead(id, markRead) {
  if (pendingRead.has(id)) return pendingRead.get(id);
  const before = readState.get(id) ?? messages.find(m => m.id === id)?.unread ?? !markRead;
  const version = (readVersion.get(id) || 0) + 1;
  readVersion.set(id, version);
  pendingRead.set(id, !markRead);
  updateReadLocally(id, !markRead);
  try {
    const result = await request('/api/connectors/gmail/messages/' + encodeURIComponent(id) + '/read', 'POST', {read: markRead});
    if (result.gmail_error) notify('Saved here, but the mail server was not updated: ' + result.gmail_error, true);
    if (readVersion.get(id) === version) {
      pendingRead.delete(id);
      updateReadLocally(id, result.unread);
      settleReadUi(id);
    }
    request('/api/connectors/gmail/status').then(state => { const own = accountFilter ? (state.accounts || []).find(a => String(a.id) === accountFilter) : null; showCounts(own ? own.counts : state.counts); }).catch(() => {});
    return result.unread;
  } catch (error) {
    if (readVersion.get(id) === version) {
      pendingRead.delete(id);
      updateReadLocally(id, before);
    }
    throw error;
  }
}
async function selectMessage(id) {
  selectedId = id;
  clearWebsiteLink();
  detail.closest('.review-panel').scrollTop = 0;
  translationPanel.scrollTop = 0;
  detailRead = null;
  const m = messages.find(message => message.id === id) || {id, subject: '', from: '', date: ''};
  for (const button of list.querySelectorAll('button.message')) {
    button.setAttribute('aria-pressed', String(button.dataset.id === (currentGroup ? currentGroup.id : id)));
  }
  draft.replaceChildren();
  relatedMessages.replaceChildren();
  if (window.innerWidth < 1024) document.getElementById('review-heading').scrollIntoView({behavior: 'smooth', block: 'start'});   // One column: the message is below the list, so bring it into view.
  detail.replaceChildren(el('h3', m.subject || '(No subject)'),
    el('div', (m.from || 'Unknown sender') + ' · ' + formatWhen(m.internal_date, m.date || 'No date'), 'meta'), el('p', 'Loading message…'));
  let full;
  try { full = (await request('/api/connectors/gmail/messages/' + encodeURIComponent(id))).message; }
  catch (error) { if (selectedId === id) detail.append(el('p', error.message, 'error')); return; }
  if (selectedId !== id) return;
  showWebsiteLink(full.website);
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
  head[1].append(directionMark(full.direction), el('span', 'From ' + (full.from || 'unknown') + ' · ' + formatWhen(full.internal_date, full.date || 'No date')), agoSpan(full.internal_date));
  const detailFlag = flagEl(full);
  if (detailFlag) head[1].append(detailFlag, el('span', countryName(full.country)));
  if (full.direction !== 'sent') { detailRead.badge = el('span', '', 'read-badge'); head[1].append(detailRead.badge); }
  const body = linkedText(full.body_text || full.snippet || '(No text content)', 'message-body');  // Text only, never HTML; web addresses become links.
  const person = personCard(full.person, full);
  const about = topicButtons(full.topics, code => { searchBox.value = 'topic:' + code; searchText = searchBox.value; reloadList(); window.scrollTo({top: 0, behavior: 'smooth'}); });
  detail.replaceChildren(...head, ...(about ? [about] : []), ...(person ? [person] : []), body);
  const attachments = renderAttachments(full);
  if (currentGroup && currentGroup.kind === 'person' && currentGroup.messages.length > 1) {
    const all = el('section', undefined, 'conversation-thread');
    all.append(groupHeading('All messages with ' + currentGroup.title + ' · ' + currentGroup.messages.length, currentGroup.messages));
    for (const t of currentGroup.messages) {
      const item = el('button', undefined, 'message');
      item.type = 'button';
      item.setAttribute('aria-pressed', String(t.id === id));
      if (t.unread) item.className += ' unread';
      detailRead.items.push({id: t.id, node: item});
      const title = el('div', undefined, 'flex items-center gap-2');
      title.append(directionMark(t.direction), document.createTextNode(t.subject || '(No subject)'));
      item.append(title,
        el('div', formatWhen(t.internal_date, t.date || 'No date') + ' — ' + (t.snippet || ''), 'meta'));
      item.addEventListener('click', () => selectMessage(t.id));
      all.append(item);
    }
    relatedMessages.append(all);
  }
  if (full.thread.length > 1 && !(currentGroup && currentGroup.kind === 'person')) {
    const thread = el('section', undefined, 'conversation-thread');
    thread.append(groupHeading('Conversation · ' + full.thread.length + ' message(s)', full.thread));
    for (const t of full.thread) {
      const item = el('button', undefined, 'message');
      item.type = 'button';
      item.setAttribute('aria-pressed', String(t.id === id));
      if (t.unread) item.className += ' unread';
      detailRead.items.push({id: t.id, node: item});
      const title = el('div', undefined, 'flex items-center gap-2');
      title.append(directionMark(t.direction), document.createTextNode(t.direction === 'sent' ? 'To ' + (t.to || 'unknown') : 'From ' + (t.from || 'unknown')));
      item.append(title,
        el('div', formatWhen(t.internal_date, t.date || 'No date') + ' — ' + (t.snippet || ''), 'meta'));
      item.addEventListener('click', () => selectMessage(t.id));
      thread.append(item);
    }
    relatedMessages.append(thread);
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
  const manual = iconButton('plus', 'Create task manually');
  manual.addEventListener('click', async () => {
    manual.disabled = true;
    try {
      const base = await request('/api/connectors/gmail/messages/' + encodeURIComponent(id) + '/proposal');
      if (full.attachments.length) {
        base.inferred.title = 'Review ' + full.attachments.length + ' attachments';
        base.inferred.description = 'Review the attachments in this message and decide what follow-up is needed.';
      }
      base.note = 'Manual task: edit the fields after reviewing this message and its attachments.';
      await showProposal(id,base);
    } catch (error) { notify(error.message,true); }
    finally { manual.disabled = false; }
  });
  bar.append(manual);
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
    const readOwner = accounts.find(a => a.id === full.account_id);
    note = el('p', readOwner?.provider === 'imap' ? 'Read and unread changes are also applied on the IMAP server.' : writesToGmail(readOwner) ? 'Read and unread changes are also applied in Gmail.' : 'Read and unread changes are kept in Inbox Tend only; Gmail is not changed.', 'meta mt-2');
  }
  detail.append(bar);
  if (note) detail.append(note);
  // Opening a message marks it read. Shown as read at once; put back if the request fails.
  if (full.unread && userSettings.mark_read_on_open && full.direction !== 'sent')
    setRead(id, true).catch(error => notify('Could not mark the message as read: ' + error.message, true));
  paintRead();
  if (userSettings.ai_enabled) detail.append(aiControls(id, full));
  detail.append(el('div',undefined,'task-slot'));
  if (attachments) detail.append(attachments);
}
function aiControls(id, message) {
  const box = el('div', undefined, 'ai-actions');
  box.append(el('h4', 'AI assistant (' + aiProvider().name + ')'),
    el('p', 'Only your chosen action sends this message’s text, or its stored images for image review, to ' + aiProvider().vendor + ' through the server’s ' + aiProvider().name + ' login.', 'meta'));
  const status = el('p', '', 'meta');
  const guidance = document.createElement('input');
  guidance.type = 'text'; guidance.maxLength = 500; guidance.className = 'ai-guidance';
  guidance.placeholder = 'Optional: what should the reply say?';
  const suggest = iconButton('sparkles', 'Suggest task from email text');
  const write = iconButton('sparkles', 'Draft reply with AI');
  const reviewImages = iconButton('eye', 'Translate and explain images in Chinese');
  const run = async (button, work) => {
    suggest.disabled = write.disabled = reviewImages.disabled = true;
    status.textContent = 'Asking ' + aiProvider().name + '… this can take up to ' + userSettings.ai_timeout + ' seconds.';
    try { await work(); status.textContent = ''; }
    catch (error) { status.textContent = error.message; }
    suggest.disabled = write.disabled = reviewImages.disabled = false;
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
  if (!message.direction || message.direction !== 'sent') box.append(guidance, el('span', ' '), write);
  const imageCount = message.attachments.filter(file => file.stored && ['image/jpeg','image/png','image/gif','image/webp'].includes(file.mime_type)).length;
  if (imageCount) {
    reviewImages.addEventListener('click', () => run(reviewImages, async () => {
      const response = await request('/api/ai/images','POST',{id});
      if (selectedId !== id) return;
      for (const old of box.querySelectorAll('.image-review')) old.remove();
      const result = response.review;
      const review = el('section',undefined,'image-review');
      review.append(el('h5','Images translated and explained in Chinese'),
        el('p',imageCount + ' images · ' + result.provider + ' · ' + result.model + ' · AI interpretation; check against the images.','meta'));
      for (const item of result.images) {
        const section = el('div',undefined,'image-review-item');
        section.append(el('strong',item.number + '. ' + item.name),
          el('p','图中文字（中文）：' + (item.visible_text_zh || '没有可辨认的文字。')),
          el('p','图片含义：' + (item.meaning_zh || '无法确定。')));
        review.append(section);
      }
      review.append(el('p','整体说明：' + (result.summary_zh || '无法确定这些图片的整体含义。')));
      const create = iconButton('plus','Create task from image review');
      create.addEventListener('click', async () => {
        create.disabled = true;
        try {
          const base = await request('/api/connectors/gmail/messages/' + encodeURIComponent(id) + '/proposal');
          base.inferred.title = result.task_title;
          base.inferred.description = result.task_description || result.summary_zh;
          base.note = 'Task filled from an AI interpretation of the images. Check the images and edit before saving.';
          base.ai = {confidence:'low',provider:result.provider,model:result.model,evidence:[],image_review:true,
            notes:result.actionable ? 'The images may imply a follow-up; verify it yourself.' : 'No explicit action was found. This is a review task.'};
          await showProposal(id,base);
        } catch (error) { notify(error.message,true); }
        finally { create.disabled = false; }
      });
      review.append(create);
      box.append(review);
    }));
    box.append(el('span',' '),reviewImages);
  }
  box.append(status);
  return box;
}

// ---- search and filters
const searchBox = document.getElementById('mail-search');
// Used by the charts: show the mail behind a bar (a day, a sender or a topic).
window.inboxSearch = (query, wantFolder = 'inbox') => {
  if (folder !== wantFolder) {   // Same as pressing the folder tab, without its own reload.
    folder = wantFolder;
    folderInbox.setAttribute('aria-pressed', String(folder === 'inbox'));
    folderSent.setAttribute('aria-pressed', String(folder === 'sent'));
    selectedId = null; currentGroup = null; showViewState();
    detail.replaceChildren('Choose a message to see its details.'); draft.replaceChildren(); relatedMessages.replaceChildren(); clearTranslation(); clearWebsiteLink();
  }
  listLimit = PAGE;
  searchBox.value = query; searchText = query; clearTimeout(searchTimer); reloadList();
  document.getElementById('messages-heading').scrollIntoView({behavior: 'smooth', block: 'start'});
};
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
for (const [id, set] of [['filter-unread', v => { onlyUnread = v; try { localStorage.setItem(unreadPreferenceKey, String(v)); } catch (_) { /* Keep the current session usable. */ } }], ['filter-attachments', v => { onlyAttachments = v; }]]) {
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
  detail.replaceChildren('Choose a message to see its details.'); draft.replaceChildren(); relatedMessages.replaceChildren(); clearTranslation(); clearWebsiteLink();
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
  detail.replaceChildren('Choose a message to see its details.'); draft.replaceChildren(); relatedMessages.replaceChildren(); clearTranslation(); clearWebsiteLink();
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
// "Mark all as read": everything unread that the list shows (the search, filters and account choice count), Inbox only.
const markAllButton = document.getElementById('mark-all-read');
function showMarkAll(data) {
  const unread = data.unread_in_view != null ? data.unread_in_view : messages.filter(m => m.unread).length;
  markAllButton.hidden = folder !== 'inbox' || unread === 0;
  markAllButton.lastChild.textContent = (filtersActive() ? 'Mark these as read' : 'Mark all as read') + ' (' + unread.toLocaleString() + ')';
}
markAllButton.addEventListener('click', async () => {
  const n = markAllButton.lastChild.textContent.match(/\(([\d,.\s]+)\)/);
  const writes = gmailWriteAccess && userSettings.gmail_write_read;
  const hasImap = accounts.some(a => a.provider === 'imap' && (!accountFilter || String(a.id) === accountFilter));
  if (!confirm('Mark ' + (n ? n[1] : 'all') + ' unread message(s) as read?\n\n' + (filtersActive() || accountFilter ? 'Only the messages in the current list are changed. ' : '') +
               (hasImap ? 'IMAP read flags will be updated. ' : '') + (writes ? 'This is also applied in Gmail for accounts that allow it.' : 'Gmail read flags are not changed.'))) return;
  markAllButton.disabled = true;
  try {
    const body = {folder, q: searchText.trim(), unread: onlyUnread, attachments: onlyAttachments};
    if (accountFilter) body.account_id = Number(accountFilter);
    const result = await request('/api/connectors/gmail/messages/read-all', 'POST', body);
    if (result.gmail_error) notify('Marked as read here, but a mail server was not fully updated: ' + result.gmail_error, true);
    else notify(result.marked + ' message(s) marked as read' + (result.gmail_changed || result.imap_changed ? ' (also on the mail server).' : '.'));
    await refreshGmail();
  } catch (error) { notify('Could not mark messages as read: ' + error.message, true); }
  markAllButton.disabled = false;
});
// The heading above a conversation or a person's messages ("All messages with Mercury · 3"), with a button that marks
// only those messages as read.
function groupHeading(text, items) {
  const head = el('div', undefined, 'thread-head');
  head.append(el('h4', text));
  if (folder === 'inbox' && items.some(t => readState.get(t.id) ?? t.unread)) {
    const mark = el('button', undefined, 'thread-read');
    mark.type = 'button';
    mark.append(icon('check-double'), document.createTextNode(' Mark all as read'));
    mark.addEventListener('click', () => markGroupRead(items.filter(t => readState.get(t.id) ?? t.unread).map(t => t.id), mark));
    head.append(mark);
  }
  return head;
}
async function markGroupRead(ids, mark) {
  mark.disabled = true;
  mark.hidden = true;
  const before = new Map(ids.map(id => [id, readState.get(id) ?? messages.find(m => m.id === id)?.unread ?? true]));
  for (const id of ids) { pendingRead.set(id, false); updateReadLocally(id, false); }
  try {
    const result = await request('/api/connectors/gmail/messages/read-all', 'POST', {ids});
    if (result.gmail_error) notify('Marked as read here, but a mail server was not fully updated: ' + result.gmail_error, true);
    else notify(result.marked + ' message(s) marked as read' + (result.gmail_changed || result.imap_changed ? ' (also on the mail server).' : '.'));
    for (const id of ids) { pendingRead.delete(id); settleReadUi(id); }
    request('/api/connectors/gmail/status').then(state => { const own = accountFilter ? (state.accounts || []).find(a => String(a.id) === accountFilter) : null; showCounts(own ? own.counts : state.counts); }).catch(() => {});
  } catch (error) {
    for (const [id, unread] of before) { pendingRead.delete(id); updateReadLocally(id, unread); }
    mark.hidden = false;
    mark.disabled = false;
    notify('Could not mark messages as read: ' + error.message, true);
  }
}
// New mail while the page is open: remember which messages the list has shown for this folder and account, and make the
// ones that were not there before slide in. A changed folder, account or search starts over without any animation.
let knownKey = null, known = new Set();
const recent = new Map();   // id -> when its welcome animation is over, so a quick second redraw does not cut it short
function findFresh(all) {
  const key = folder + '|' + accountFilter + '|' + (filtersActive() ? 'filtered' : 'all');
  const ids = all.map(m => m.id);
  const fresh = new Set();
  if (key === knownKey && known.size) for (const id of ids) if (!known.has(id)) fresh.add(id);
  if (key !== knownKey) known = new Set();
  for (const id of ids) known.add(id);
  knownKey = key;
  if (fresh.size > 30) fresh.clear();   // A first sync brings in hundreds; that is not "something just arrived".
  const now = Date.now();
  for (const id of fresh) recent.set(id, now + 2500);
  for (const [id, until] of recent) if (until < now) recent.delete(id);
  if (fresh.size) {
    const unread = all.filter(m => fresh.has(m.id) && m.unread).length;
    notify(fresh.size + ' new message' + (fresh.size === 1 ? '' : 's') + (unread && unread !== fresh.size ? ' (' + unread + ' unread)' : ''));
  }
  return new Set(recent.keys());
}
function renderList(data) {
  if (retiringCount && retiringContext === listContext()) { queuedListData = data; return; }
  queuedListData = null;
  const previous = selectedId;
  list.replaceChildren();
  groups = data.groups || [];
  if (data.groups && view === 'people' && onlyUnread && currentGroup && selectedId && !groups.some(g => g.id === currentGroup.id))
    groups = [...groups, currentGroup].sort((a, b) => b.last.internal_date - a.last.internal_date);
  messages = data.groups ? groups.flatMap(g => g.messages) : data.messages;
  for (const m of messages) {
    if (pendingRead.has(m.id)) m.unread = pendingRead.get(m.id);
    readState.set(m.id, m.unread);
  }
  for (const g of groups) g.unread_count = g.messages.filter(m => m.unread).length;
  paintRead();
  if (!messages.some(m => m.id === previous)) {
    selectedId = null; currentGroup = null;
    detail.replaceChildren('Choose a message to see its details.'); draft.replaceChildren(); relatedMessages.replaceChildren(); clearTranslation(); clearWebsiteLink();
  } else if (currentGroup) {
    currentGroup = groups.find(g => g.id === currentGroup.id) || null;  // Fresh data for the open group.
  }
  lastData = data;
  const fresh = findFresh(messages);
  showMarkAll(data);
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
      if (fresh.has(m.id)) button.classList.add('fresh');
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
      if (g.messages.some(m => fresh.has(m.id))) button.classList.add('fresh');
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
      button.addEventListener('click', () => {
        const old = currentGroup;
        currentGroup = g;
        selectMessage(g.last.id);
        if (view === 'people' && onlyUnread && old && old.id !== g.id && !old.unread_count) {
          const previousRow = [...list.querySelectorAll('.row-wrap')].find(w => w.querySelector('.message')?.dataset.id === old.id);
          if (previousRow) retireRow(previousRow, old.id);
        }
      });
      const wrap = el('div', undefined, 'row-wrap');
      wrap.append(button);
      const unreadIds = g.messages.filter(m => m.unread).map(m => m.id);
      if (folder === 'inbox' && unreadIds.length) {   // The tick marks only this row's unread messages as read.
        const mark = el('button', undefined, 'row-read');
        mark.type = 'button';
        mark.title = 'Mark ' + unreadIds.length + ' unread message(s) from ' + (g.kind === 'person' ? 'this person' : 'this conversation') + ' as read';
        mark.setAttribute('aria-label', mark.title);
        mark.append(icon('check-double'));
        mark.addEventListener('click', () => markGroupRead(unreadIds, mark));
        wrap.append(mark);
      }
      list.append(wrap);
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
  let websitesWereRunning = false;
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
      detail.replaceChildren('Choose a message to see its details.'); draft.replaceChildren(); relatedMessages.replaceChildren(); clearTranslation(); clearWebsiteLink();
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
    const sites = state.websites || {};
    if (!sites.running && !sites.pending) websitesAttempted = false;
    if (websitesWereRunning && !sites.running && selectedId) {
      const opened = selectedId;
      request('/api/connectors/gmail/messages/' + encodeURIComponent(opened)).then(result => {
        if (selectedId === opened) showWebsiteLink(result.message.website);
      }).catch(() => {});
    }
    websitesWereRunning = Boolean(sites.running);
    if (userSettings.websites_enabled && sites.pending > 0 && !sites.running && !websitesAttempted) {
      websitesAttempted = true;
      try { await request('/api/websites/guess', 'POST', {}); } catch (error) { notify(error.message, true); }
      continue;
    }
    const busy = sync.running || guess.running || labels.running || sites.running;
    gmailRefresh.disabled = sync.running;
    // While a sync runs, fetch the list again only when its progress moved: a big mailbox is a big response.
    const signature = busy ? [sync.done, sync.failed, sync.total, guess.done, labels.done, sites.done].join(':') : null;
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
      (!labels.running && labels.error ? ' Topic icons: ' + labels.error : '') +
      (sites.running ? ' Finding likely websites… ' + sites.done + ' of ' + sites.total + ' messages.' : '') +
      (!sites.running && sites.error ? ' Website guesses: ' + sites.error : '');
    if (!busy) { if (window.refreshStats) window.refreshStats(); return; }
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
  detail.replaceChildren('Choose a message to see its details.'); draft.replaceChildren(); relatedMessages.replaceChildren(); clearTranslation(); clearWebsiteLink();
  refreshGmail().catch(error => { gmailStatus.textContent = error.message; });
});
gmailRefresh.addEventListener('click', async () => {
  try { await refreshGmail(true); } catch (error) { gmailStatus.textContent = error.message; }
});

async function init() {
  try { await sessionReady; } catch (error) { notify('Could not load session: ' + error.message, true); return; }
  await settingsReady;
  if (userSettings.websites_enabled) document.getElementById('review-caption').textContent = 'AI guesses may be wrong';
  view = ['people', 'threads', 'messages'].includes(userSettings.inbox_view) ? userSettings.inbox_view : 'people';
  document.getElementById('filter-unread').setAttribute('aria-pressed', String(onlyUnread));
  showViewState();
  if (userSettings.translate_enabled) {
    translationPanel.hidden = false;
    document.getElementById('inbox-grid').classList.add('has-translation');
  }
  try { await refreshGmail(); } catch (error) { gmailStatus.textContent = error.message; list.textContent = ''; }
  // The server syncs on a timer. While this page is open and visible, look once a minute and redraw only if mail was synced.
  let seen = null;
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const state = await request('/api/connectors/gmail/status');
      const mark = state.connected ? String(state.last_sync) : null;
      if (seen === null) { seen = mark; return; }
      if (mark !== seen && !state.sync.running) { seen = mark; await refreshGmail(); }
    } catch (error) { /* try again next minute */ }
  }, 60000);
}
init();
