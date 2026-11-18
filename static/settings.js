'use strict';
const prefsForm = document.getElementById('prefs-form');
const passwordForm = document.getElementById('password-form');

function fillForm(values) {
  for (const [key, value] of Object.entries(values)) {
    const control = prefsForm.elements[key];
    if (!control) continue;
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
    sync_sent: form.sync_sent.checked,
    sync_max_messages: Number(form.sync_max_messages.value),
    attachment_max_mb: Number(form.attachment_max_mb.value),
    ai_enabled: form.ai_enabled.checked,
    ai_model: form.ai_model.value.trim(),
    ai_language: form.ai_language.value.trim(),
    ai_timeout: Number(form.ai_timeout.value),
    ai_attachments: form.ai_attachments.checked
  };
}
async function init() {
  try { await sessionReady; } catch (error) { notify('Could not load session: ' + error.message, true); return; }
  const list = document.getElementById('timezone-list');
  if (typeof Intl.supportedValuesOf === 'function') {
    for (const name of Intl.supportedValuesOf('timeZone')) { const option = el('option'); option.value = name; list.append(option); }
  }
  try {
    const result = await request('/api/settings');
    fillForm(result.settings);
    document.getElementById('account-name').textContent = result.username;
  } catch (error) { notify('Could not load settings: ' + error.message, true); return; }

  const aiStatus = document.getElementById('ai-status');
  try {
    const ai = await request('/api/ai/status');
    aiStatus.textContent = !ai.available
      ? 'Codex CLI was not found on this server. Install it and run `codex login`, then reload this page.'
      : (ai.version || 'Codex CLI') + ' at ' + ai.path + ' · ' + (ai.logged_in ? (ai.login || 'logged in') : 'not logged in: run `codex login` as the user that runs Inbox Tend');
  } catch (error) { aiStatus.textContent = 'Could not check Codex: ' + error.message; }
  const aiTest = document.getElementById('ai-test'), aiResult = document.getElementById('ai-test-result');
  aiTest.addEventListener('click', async () => {
    aiTest.disabled = true; aiResult.textContent = ' Asking Codex…';
    try { const result = await request('/api/ai/test', 'POST', {}); aiResult.textContent = ' Codex answered in ' + result.seconds + ' s.'; }
    catch (error) { aiResult.textContent = ' ' + error.message; }
    aiTest.disabled = false;
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
