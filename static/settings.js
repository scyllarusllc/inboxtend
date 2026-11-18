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
    translate_language: form.translate_language.value.trim()
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
  // Gmail access level: read-only by default, write only after an explicit re-authorisation.
  try {
    const gmail = await request('/api/connectors/gmail/status');
    const line = document.getElementById('access-status'), grant = document.getElementById('grant-modify'), back = document.getElementById('grant-readonly');
    line.textContent = !gmail.configured ? 'Gmail is not configured on this server (see docs/gmail-web.md).'
      : !gmail.connected ? 'Gmail is not connected yet. Connecting from here with change access also works.'
      : gmail.write_access ? 'Current access: read and change read/unread (gmail.modify).' : 'Current access: read-only (gmail.readonly).';
    grant.hidden = !gmail.configured || gmail.write_access;
    back.hidden = !gmail.configured || !gmail.connected || !gmail.write_access;
    prefsForm.elements.gmail_write_read.disabled = !gmail.write_access;
    const authorise = async (access, button) => {
      button.disabled = true;
      try { location.assign((await request('/api/connectors/gmail/connect', 'POST', {access})).authorization_url); }
      catch (error) { notify(error.message, true); button.disabled = false; }
    };
    grant.addEventListener('click', () => authorise('modify', grant));
    back.addEventListener('click', () => authorise('readonly', back));
  } catch (error) { document.getElementById('access-status').textContent = 'Could not check Gmail: ' + error.message; }
  if (new URLSearchParams(location.search).get('gmail') === 'write') notify('Gmail now allows Inbox Tend to change read/unread. Your read/unread changes will be applied to Gmail.');
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
