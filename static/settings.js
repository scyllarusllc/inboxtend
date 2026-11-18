'use strict';
const prefsForm = document.getElementById('prefs-form');
const passwordForm = document.getElementById('password-form');

const OTHER_MODEL = '__other__';
let aiProviders = {};
async function loadModels(provider, selected) {
  // Fills the model dropdown for a provider; `selected` is a saved model name to preselect.
  const choice = prefsForm.elements.ai_model_choice, custom = prefsForm.elements.ai_model;
  choice.replaceChildren();
  const first = el('option', 'Provider default'); first.value = ''; choice.append(first);
  try {
    const result = await request('/api/ai/models?provider=' + encodeURIComponent(provider));
    for (const model of result.models) {
      const option = el('option', model.label + (model.id !== model.label ? ' (' + model.id + ')' : '')); option.value = model.id;
      if (model.note) option.title = model.note;
      choice.append(option);
    }
  } catch (error) { /* The list is only a convenience: the custom field still works. */ }
  const other = el('option', 'Other…'); other.value = OTHER_MODEL; choice.append(other);
  const known = [...choice.options].some(option => option.value === selected);
  choice.value = !selected ? '' : (known ? selected : OTHER_MODEL);
  custom.value = choice.value === OTHER_MODEL ? selected : '';
  custom.hidden = choice.value !== OTHER_MODEL;
}
function showProviderStatus() {
  const info = aiProviders[prefsForm.elements.ai_provider.value];
  const line = document.getElementById('ai-status');
  line.textContent = !info ? '' : (!info.available
    ? info.name + ' CLI was not found on this server. Install it and sign in with ' + info.login_hint + ', then reload this page.'
    : (info.version || info.name) + ' at ' + info.path + ' · ' + (info.logged_in ? (info.login || 'signed in') : 'not signed in: run ' + info.login_hint + ' as the user that runs Inbox Tend'));
}
function fillForm(values) {
  for (const [key, value] of Object.entries(values)) {
    const control = prefsForm.elements[key];
    if (!control || key === 'ai_model') continue;
    if (control.type === 'checkbox') control.checked = Boolean(value);
    else control.value = value;
  }
}
function readForm() {
  const form = prefsForm.elements;
  return {
    timezone: form.timezone.value.trim(),
    due_soon_hours: Number(form.due_soon_hours.value),
    default_priority: form.default_priority.value,
    inbox_view: form.inbox_view.value,
    sync_sent: form.sync_sent.checked,
    mark_read_on_open: form.mark_read_on_open.checked,
    gmail_write_read: form.gmail_write_read.checked,
    sync_max_messages: Number(form.sync_max_messages.value),
    attachment_max_mb: Number(form.attachment_max_mb.value),
    ai_enabled: form.ai_enabled.checked,
    ai_provider: form.ai_provider.value,
    ai_model: form.ai_model_choice.value === OTHER_MODEL ? form.ai_model.value.trim() : form.ai_model_choice.value,
    ai_language: form.ai_language.value.trim(),
    ai_timeout: Number(form.ai_timeout.value),
    ai_attachments: form.ai_attachments.checked,
    translate_enabled: form.translate_enabled.checked,
    translate_language: form.translate_language.value.trim(),
    flags_enabled: form.flags_enabled.checked,
    topics_enabled: form.topics_enabled.checked
  };
}
let savedModel = '';
async function init() {
  try { await sessionReady; } catch (error) { notify('Could not load session: ' + error.message, true); return; }
  const list = document.getElementById('timezone-list');
  if (typeof Intl.supportedValuesOf === 'function') {
    for (const name of Intl.supportedValuesOf('timeZone')) { const option = el('option'); option.value = name; list.append(option); }
  }
  try {
    const result = await request('/api/settings');
    fillForm(result.settings);
    savedModel = result.settings.ai_model || '';
    document.getElementById('account-name').textContent = result.username;
  } catch (error) { notify('Could not load settings: ' + error.message, true); return; }

  try { aiProviders = (await request('/api/ai/status')).providers; }
  catch (error) { document.getElementById('ai-status').textContent = 'Could not check the AI tools: ' + error.message; }
  const saved = prefsForm.elements;
  await loadModels(saved.ai_provider.value, savedModel);
  showProviderStatus();
  saved.ai_provider.addEventListener('change', async () => { await loadModels(saved.ai_provider.value, ''); showProviderStatus(); });
  saved.ai_model_choice.addEventListener('change', () => {
    saved.ai_model.hidden = saved.ai_model_choice.value !== OTHER_MODEL;
    if (!saved.ai_model.hidden) saved.ai_model.focus();
  });
  // Gmail accounts and their access level. Read-only by default; writing needs an explicit re-authorisation per account.
  const accountList = document.getElementById('gmail-accounts');
  async function showGmail() {
    const gmail = await request('/api/connectors/gmail/status');
    const accounts = gmail.accounts || [];
    const line = document.getElementById('access-status');
    const show = (id, visible, text) => { const b = document.getElementById(id); b.hidden = !visible; if (text) (b.querySelector('span') || b).textContent = text; return b; };
    line.textContent = !gmail.configured ? 'Gmail is not configured on this server (see docs/gmail-web.md).'
      : !accounts.length ? 'No Gmail account is connected.'
      : accounts.length === 1 ? '1 Gmail account connected.' : accounts.length + ' Gmail accounts connected.';
    show('connect-gmail', gmail.configured, accounts.length ? 'Add another Gmail account' : 'Connect Gmail (read-only)').classList.toggle('action', !accounts.length);
    show('grant-modify', gmail.configured && !accounts.length);
    accountList.replaceChildren(...accounts.map((account, at) => accountRow(account, at, gmail.configured)));
    prefsForm.elements.gmail_write_read.disabled = !gmail.write_access;
    return gmail;
  }
  function accountRow(account, at, configured) {
    const row = el('li', undefined, 'account-row');
    const dot = el('span', undefined, 'account-dot'); dot.dataset.slot = String(at % 6);
    const name = account.email || 'Gmail account ' + account.id;
    const facts = [account.write ? 'Read, and change read/unread (gmail.modify)' : 'Read-only (gmail.readonly)', account.stored + ' message(s) saved',
                   account.last_sync ? 'last synced ' + formatWhen(account.last_sync * 1000) : 'not synced yet'];
    const text = el('div', undefined, 'min-w-0');
    text.append(el('strong', name, 'break-all'), el('div', facts.join(' · '), 'meta'));
    const actions = el('div', undefined, 'account-actions');
    const add = (label, className, handler) => { const b = el('button', label, className || ''); b.type = 'button'; b.addEventListener('click', () => handler(b)); actions.append(b); };
    if (configured) add(account.write ? 'Return to read-only…' : 'Allow changing read/unread…', '', b => authorise(account.write ? 'readonly' : 'modify', b, 'settings', account.id));
    add('Remove', 'danger', async button => {
      if (!confirm('Remove ' + name + '?\n\nThis deletes its saved messages, attachments, topics and translations from this server. Your other accounts and your tasks are kept. You can connect it again later and sync again.')) return;
      button.disabled = true;
      try { await request('/api/connectors/gmail/disconnect', 'POST', {account_id: account.id}); notify(name + ' was removed. Its saved mail was deleted from this server.'); await showGmail(); }
      catch (error) { notify(error.message, true); button.disabled = false; }
    });
    row.append(dot, text, actions);
    return row;
  }
  const authorise = async (access, button, next, accountId) => {
    button.disabled = true;
    try { location.assign((await request('/api/connectors/gmail/connect', 'POST', accountId == null ? {access, next} : {access, next, account_id: accountId})).authorization_url); }
    catch (error) { notify(error.message, true); button.disabled = false; }
  };
  try {
    await showGmail();
    // A new account continues to the Inbox (to watch its first sync); changing an existing account stays here.
    const connect = document.getElementById('connect-gmail'), grant = document.getElementById('grant-modify');
    connect.addEventListener('click', () => authorise('readonly', connect, 'inbox'));
    grant.addEventListener('click', () => authorise('modify', grant, 'inbox'));
  } catch (error) { document.getElementById('access-status').textContent = 'Could not check Gmail: ' + error.message; }
  const outcome = new URLSearchParams(location.search).get('gmail');
  if (outcome === 'write') notify('Gmail now allows Inbox Tend to change read/unread for that account. Your read/unread changes will be applied to Gmail.');
  else if (outcome === 'readonly') notify('That account is read-only. Read/unread changes stay in Inbox Tend.');
  else if (outcome === 'declined') notify('Google sign-in was cancelled. Nothing changed.', true);
  else if (outcome === 'failed') notify('Google did not grant the requested access, or you signed in as a different account than the one being changed. Check that the scope is listed in your Google Cloud consent screen, then try again.', true);
  const resetButton = document.getElementById('flags-reset'), resetResult = document.getElementById('flags-reset-result');
  resetButton.addEventListener('click', async () => {
    if (!confirm('Forget all sender details guessed by the AI?\n\nFlags and cards lose their AI details until it guesses again (when sender details are on, that sends each sender to the AI provider once more). The history counted from your mail stays.')) return;
    resetButton.disabled = true;
    try { await request('/api/flags/reset', 'POST', {}); resetResult.textContent = ' All guesses were forgotten.'; }
    catch (error) { resetResult.textContent = ' ' + error.message; }
    resetButton.disabled = false;
  });
  const topicsReset = document.getElementById('topics-reset'), topicsResult = document.getElementById('topics-reset-result');
  topicsReset.addEventListener('click', async () => {
    if (!confirm('Forget all topic icons?\n\nThe icons disappear until the AI labels the messages again (when topic icons are on, that sends each message to the AI provider once more).')) return;
    topicsReset.disabled = true;
    try { await request('/api/topics/reset', 'POST', {}); topicsResult.textContent = ' All topics were forgotten.'; }
    catch (error) { topicsResult.textContent = ' ' + error.message; }
    topicsReset.disabled = false;
  });
  const aiTest = document.getElementById('ai-test'), aiResult = document.getElementById('ai-test-result');
  aiTest.addEventListener('click', async () => {
    aiTest.disabled = true; aiResult.textContent = ' Asking the AI…';
    try { const result = await request('/api/ai/test', 'POST', {}); aiResult.textContent = ' ' + result.provider + ' answered in ' + result.seconds + ' s.'; }
    catch (error) { aiResult.textContent = ' ' + error.message; }
    aiTest.disabled = false;
  });
  const browserLanguage = () => {
    try { return new Intl.DisplayNames(['en'], {type: 'language'}).of(navigator.language || 'en') || 'English'; } catch (_) { return 'English'; }
  };
  document.getElementById('use-browser-language').addEventListener('click', () => { prefsForm.elements.translate_language.value = browserLanguage(); });
  document.getElementById('use-browser-timezone').addEventListener('click', () => {
    prefsForm.elements.timezone.value = Intl.DateTimeFormat().resolvedOptions().timeZone;
  });
  prefsForm.addEventListener('submit', async event => {
    event.preventDefault();
    const save = document.getElementById('save-prefs');
    save.disabled = true;
    try {
      const result = await request('/api/settings', 'PATCH', readForm());
      fillForm(result.settings);
      await loadModels(result.settings.ai_provider, result.settings.ai_model || '');
      showProviderStatus();
      notify('Settings saved.');
    } catch (error) { notify(error.message, true); }
    save.disabled = false;
  });
  passwordForm.addEventListener('submit', async event => {
    event.preventDefault();
    const form = passwordForm.elements;
    if (form.new.value !== form.repeat.value) { notify('The new passwords do not match.', true); return; }
    const save = document.getElementById('save-password');
    save.disabled = true;
    try {
      await request('/api/auth/password', 'POST', {current: form.current.value, new: form.new.value});
      passwordForm.reset();
      notify('Password changed. Your other sessions were signed out.');
    } catch (error) { notify(error.message, true); }
    save.disabled = false;
  });
}
init();
