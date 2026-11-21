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
    unread_dismiss_delay_ms: Number(form.unread_dismiss_delay_ms.value),
    sync_sent: form.sync_sent.checked,
    mark_read_on_open: form.mark_read_on_open.checked,
    gmail_write_read: form.gmail_write_read.checked,
    auto_sync_minutes: Number(form.auto_sync_minutes.value),
    sync_max_messages: Number(form.sync_max_messages.value),
    attachment_max_mb: Number(form.attachment_max_mb.value),
    ai_enabled: form.ai_enabled.checked,
    ai_provider: form.ai_provider.value,
    ai_model: form.ai_model_choice.value === OTHER_MODEL ? form.ai_model.value.trim() : form.ai_model_choice.value,
    ai_language: form.ai_language.value.trim(),
    ai_timeout: Number(form.ai_timeout.value),
    ai_attachments: form.ai_attachments.checked,
    translate_enabled: form.translate_enabled.checked,
    translate_preload: form.translate_preload.checked,
    translate_language: form.translate_language.value.trim(),
    translate_max_chars: Number(form.translate_max_chars.value),
    flags_enabled: form.flags_enabled.checked,
    topics_enabled: form.topics_enabled.checked,
    websites_enabled: form.websites_enabled.checked
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
    const languageSelect = prefsForm.elements.translate_language;
    const packStatus = document.getElementById('translation-packs-status');
    const sourceSelect = document.getElementById('translation-source');
    const targetSelect = document.getElementById('translation-target');
    const installButton = document.getElementById('install-translation-pack');
    const installStatus = document.getElementById('translation-install-status');
    const fallbackCatalog = [
      ['ar','Arabic'],['de','German'],['en','English'],['es','Spanish'],['fr','French'],
      ['hi','Hindi'],['id','Indonesian'],['it','Italian'],['ja','Japanese'],['ko','Korean'],
      ['nl','Dutch'],['pl','Polish'],['pt','Portuguese'],['ru','Russian'],['th','Thai'],
      ['tr','Turkish'],['vi','Vietnamese'],['zh','Chinese (Simplified)'],['zt','Chinese (Traditional)']
    ];
    sourceSelect.append(...fallbackCatalog.map(([code,name]) => new Option(name,code)));
    targetSelect.append(...fallbackCatalog.map(([code,name]) => new Option(name,code)));
    sourceSelect.value = 'en'; targetSelect.value = 'zh';
    installButton.disabled = true;
    let pollPacks = null;
    const showTiming = recent => {
      document.getElementById('translation-timing').textContent = recent
        ? `Last translation (${recent.input_chars.toLocaleString()} characters): ${recent.total_seconds}s total · mail ${recent.fetch_seconds}s · queue ${recent.queue_seconds}s · model ${recent.model_seconds}s.`
        : 'No translation measured since this server started.';
    };
    const showPacks = async () => {
      const packs = await request('/api/translation/packs');
      showTiming(packs.recent);
      const preload = packs.preload || {};
      document.getElementById('translation-preload-status').textContent = preload.last_error
        ? 'Last background attempt: ' + preload.last_error
        : `Pretranslated ${preload.completed || 0} message(s) since this server started.`;
      const selected = languageSelect.value || result.settings.translate_language || '';
      languageSelect.replaceChildren(new Option('Choose an installed language', ''), ...[...new Set(packs.installed.map(pair => pair.target))].map(code => {
        const language = packs.catalog.find(item => item.code === code);
        return new Option(language?.name || code, code);
      }));
      languageSelect.value = selected;
      if (!languageSelect.value && selected) languageSelect.value = '';
      packStatus.textContent = packs.installed.length ? 'Choose an installed target language. The source language also needs a translation route.' : 'No language packs installed yet.';
      document.getElementById('installed-translation-packs').textContent = packs.installed.length ? 'Installed: ' + packs.installed.map(pair => pair.label).join(', ') : 'Installed: none';
      installButton.disabled = !packs.runtime_ready || ['downloading', 'installing'].includes(packs.installation.state);
      installStatus.textContent = packs.runtime_ready ? packs.installation.message : 'Install Python dependencies from requirements.txt on this server first.';
      clearTimeout(pollPacks);
      if (['downloading', 'installing'].includes(packs.installation.state)) pollPacks = setTimeout(() => showPacks().catch(error => { installStatus.textContent = error.message; }), 1500);
      return packs;
    };
    try {
      await showPacks();
    } catch (error) {
      packStatus.textContent = 'Could not check installed language packs: ' + error.message;
      document.getElementById('installed-translation-packs').textContent = 'Installed packs unavailable';
      installStatus.textContent = error.message === 'Not found'
        ? 'The running server does not have the language-pack API yet. Deploy the updated app.py and restart Inbox Tend.'
        : 'The language-pack API is unavailable: ' + error.message;
      try {
        const legacy = (await request('/api/translation/languages')).languages;
        languageSelect.append(...legacy.map(item => new Option(item.name,item.code)));
      } catch (_) { /* The older server may not have the languages API either. */ }
    }
    installButton.addEventListener('click', async () => {
      if (sourceSelect.value === targetSelect.value) { installStatus.textContent = 'Choose two different languages.'; return; }
      installButton.disabled = true;
      installStatus.textContent = 'Starting download…';
      try {
        await request('/api/translation/packs/install', 'POST', {source:sourceSelect.value, target:targetSelect.value});
        await showPacks();
      } catch (error) { installStatus.textContent = error.message; installButton.disabled = false; }
    });
    document.getElementById('refresh-translation-timing').addEventListener('click', () => showPacks().catch(error => {
      document.getElementById('translation-timing').textContent = 'Could not load timing: ' + error.message;
    }));
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
    line.textContent = !gmail.configured ? 'No mail connector is configured.'
      : !accounts.length ? 'No mail account is connected.'
      : accounts.length === 1 ? '1 mail account connected.' : accounts.length + ' mail accounts connected.';
    show('connect-gmail', gmail.configured && !!gmail.gmail_configured, accounts.length ? 'Add another Gmail account' : 'Connect Gmail (read-only)').classList.toggle('action', !accounts.length);
    show('grant-modify', gmail.gmail_configured && !accounts.length);
    accountList.replaceChildren(...accounts.map((account, at) => accountRow(account, at, gmail.configured)));
    prefsForm.elements.gmail_write_read.disabled = !gmail.write_access;
    return gmail;
  }
  function accountRow(account, at, configured) {
    const row = el('li', undefined, 'account-row');
    const dot = el('span', undefined, 'account-dot'); dot.dataset.slot = String(at % 6);
    const name = account.email || 'Gmail account ' + account.id;
    const facts = [account.provider === 'imap' ? 'IMAP: read mail and change read/unread' : account.write ? 'Read, and change read/unread (gmail.modify)' : 'Read-only (gmail.readonly)', account.stored + ' message(s) saved',
                   account.last_sync ? 'last synced ' + formatWhen(account.last_sync * 1000) : 'not synced yet'];
    const text = el('div', undefined, 'min-w-0');
    text.append(el('strong', name, 'break-all'), el('div', facts.join(' · '), 'meta'));
    const actions = el('div', undefined, 'account-actions');
    const add = (label, className, handler) => { const b = el('button', label, className || ''); b.type = 'button'; b.addEventListener('click', () => handler(b)); actions.append(b); };
    if (configured && account.provider !== 'imap') add(account.write ? 'Return to read-only…' : 'Allow changing read/unread…', '', b => authorise(account.write ? 'readonly' : 'modify', b, 'settings', account.id));
    add('Remove', 'danger', async button => {
      if (!confirm('Remove ' + name + '?\n\nThis deletes its saved messages, attachments, topics and translations from this server. Your other accounts and your tasks are kept. You can connect it again later and sync again.')) return;
      button.disabled = true;
      try { await request(account.provider === 'imap' ? '/api/connectors/imap/disconnect' : '/api/connectors/gmail/disconnect', 'POST', {account_id: account.id}); notify(name + ' was removed. Its saved mail was deleted from this server.'); await showGmail(); }
      catch (error) { notify(error.message, true); button.disabled = false; }
    });
    row.append(dot, text, actions);
    return row;
  }
  document.getElementById('connect-imap').addEventListener('click', async event => {
    const form = document.getElementById('imap-form'), button = event.currentTarget;
    button.disabled = true;
    try {
      const fields = Object.fromEntries([...form.querySelectorAll('input')].map(input => [input.name,input.value.trim()]));
      const result = await request('/api/connectors/imap/connect', 'POST', fields);
      form.querySelector('[name=password]').value = '';
      await showGmail();
      await request('/api/connectors/gmail/sync', 'POST', {account_id: result.account_id});
      notify('IMAP account connected. Sync started.');
    } catch (error) { notify(error.message, true); }
    finally { button.disabled = false; }
  });
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
  // Sign in with Google: which Google accounts may sign in as this user.
  const googleBox = document.getElementById('google-signin-settings'), googleList = document.getElementById('google-links');
  async function showGoogle() {
    const info = await request('/api/auth/google/links');
    googleBox.hidden = !info.configured;
    if (!info.configured) return;
    googleList.replaceChildren(...info.links.map(link => {
      const row = el('li', undefined, 'account-row');
      const text = el('div', undefined, 'min-w-0');
      text.append(el('strong', link.email || 'Google account', 'break-all'), el('div', 'Linked ' + formatWhen(link.linked_at * 1000), 'meta'));
      const actions = el('div', undefined, 'account-actions');
      const remove = el('button', 'Unlink', 'danger'); remove.type = 'button';
      remove.addEventListener('click', async () => {
        if (!confirm('Unlink ' + link.email + '?\n\nIt can no longer be used to sign in. Your password keeps working.')) return;
        remove.disabled = true;
        try { await request('/api/auth/google/unlink', 'POST', {id: link.id}); notify('Google account unlinked.'); await showGoogle(); }
        catch (error) { notify(error.message, true); remove.disabled = false; }
      });
      actions.append(remove);
      row.append(el('span', undefined, 'account-dot'), text, actions);
      return row;
    }));
    document.getElementById('google-redirect').textContent = 'For the operator: the Google OAuth client must list ' + info.redirect_uri + ' as an authorised redirect URI.';
  }
  try {
    await showGoogle();
    const linkButton = document.getElementById('link-google');
    linkButton.addEventListener('click', async () => {
      linkButton.disabled = true;
      try { location.assign((await request('/api/auth/google/link', 'POST', {})).authorization_url); }
      catch (error) { notify(error.message, true); linkButton.disabled = false; }
    });
  } catch (error) { /* Without it the rest of the page still works. */ }
  const googleOutcome = new URLSearchParams(location.search).get('google');
  if (googleOutcome === 'linked') notify('Google account linked. You can now sign in with it.');
  else if (googleOutcome === 'taken') notify('That Google account is already linked to another user.', true);
  else if (googleOutcome === 'unverified') notify('Google says that email address is not verified, so it cannot be linked.', true);
  else if (googleOutcome) notify('Linking the Google account did not work. Please try again.', true);
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
  document.getElementById('use-browser-language').addEventListener('click', () => {
    const code = (navigator.language || 'en').toLowerCase().startsWith('zh-tw') ? 'zt' : (navigator.language || 'en').split('-')[0].toLowerCase();
    const select = prefsForm.elements.translate_language;
    if ([...select.options].some(option => option.value === code)) select.value = code;
    else notify('No offline language pack is installed for this browser language.', true);
  });
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
