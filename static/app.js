'use strict';
const list = document.getElementById('messages');
const detail = document.getElementById('detail');
const draft = document.getElementById('draft');
const taskList = document.getElementById('tasks');
const taskSummary = document.getElementById('task-summary');
let messages = [], selectedId = null, csrfToken = '';

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function field(form, caption, name, value, kind = 'input') {
  const label = el('label', caption);
  const control = document.createElement(kind);
  control.name = name;
  control.value = value ?? '';
  if (kind === 'input') control.type = 'text';
  label.append(control);
  form.append(label);
  return control;
}
async function request(path, method = 'GET', body) {
  const options = {method, headers: {}};
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.headers['X-CSRF-Token'] = csrfToken;
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw Error(data.error || 'Request failed (' + response.status + ')');
  return data;
}
async function refreshTasks() {
  try {
    const [overview, rows] = await Promise.all([
      request('/api/tasks/summary'), request('/api/tasks')
    ]);
    const summary = overview.summary;
    taskSummary.textContent = 'Total ' + summary.total +
      ' · New ' + summary.by_status.new +
      ' · In progress ' + summary.by_status.in_progress +
      ' · Waiting ' + summary.by_status.waiting +
      ' · Done ' + summary.by_status.done +
      ' · Due soon ' + summary.due_soon +
      ' · Overdue ' + summary.overdue;
    taskList.replaceChildren();
    if (!rows.tasks.length) {
      taskList.append(el('p', 'No saved tasks yet. Review a fictional email and create one.'));
      return;
    }
    for (const task of rows.tasks) {
      const item = el('article', undefined, 'task-item');
      const date = task.due_date ? task.due_date + ' ' + (task.due_time || '23:59') + ' (' + task.due_timezone + ')' : 'No deadline';
      item.append(el('h3', task.title),
        el('div', 'Priority: ' + task.priority + ' · ' + date + ' · ' + task.deadline_state, 'meta'),
        el('p', task.description));
      const label = el('label', 'Status: ');
      const select = document.createElement('select');
      for (const status of ['new', 'in_progress', 'waiting', 'done']) {
        const option = el('option', status.replaceAll('_', ' '));
        option.value = status;
        select.append(option);
      }
      select.value = task.status;
      label.append(select);
      const save = el('button', 'Save status');
      const feedback = el('span', '', 'meta');
      save.addEventListener('click', async () => {
        save.disabled = true;
        try {
          await request('/api/tasks/' + task.id, 'PATCH', {
            revision: task.revision, status: select.value
          });
          await refreshTasks();
        } catch (error) {
          feedback.textContent = String(error.message) + ' (refresh to retry)';
        } finally { save.disabled = false; }
      });
      item.append(label, save, feedback);
      taskList.append(item);
    }
  } catch (error) {
    taskList.replaceChildren(el('p', 'Could not load tasks: ' + error.message, 'error'));
  }
}
async function showProposal(messageId) {
  const m = messages.find(message => message.id === messageId);
  if (!m) return;
  const proposal = await request('/api/messages/' + encodeURIComponent(messageId) + '/proposal');
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
      feedback.textContent = 'Task saved locally. No email sent.';
      await refreshTasks();
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
    el('h3', m.subject), el('div', m.from + ' · ' + m.date, 'meta'),
    el('p', m.body), el('h4', 'Excerpt (not AI-generated)'), el('p', m.summary)
  );
  const reply = el('button', 'Prepare an unsent reply draft', 'action');
  reply.addEventListener('click', async () => {
    reply.disabled = true;
    try {
      const response = await request('/api/draft', 'POST', {id});
      if (selectedId === id) draft.replaceChildren(el('h4', 'Unsent draft'), el('pre', response.draft));
    } catch (error) {
      if (selectedId === id) draft.replaceChildren(el('p', error.message, 'error'));
    } finally { reply.disabled = false; }
  });
  const propose = el('button', 'Suggest task');
  propose.addEventListener('click', async () => {
    propose.disabled = true;
    try { await showProposal(id); }
    catch (error) { if (selectedId === id) detail.append(el('p', error.message, 'error')); }
    finally { propose.disabled = false; }
  });
  detail.append(reply, el('span', ' '), propose);
  draft.replaceChildren();
}
async function init() {
  try {
    const session = await request('/api/session');
    csrfToken = session.csrf_token;
    const data = await request('/api/messages');
    messages = data.messages;
    list.replaceChildren();
    for (const m of messages) {
      const button = el('button', undefined, 'message');
      button.dataset.id = m.id;
      button.setAttribute('aria-pressed', 'false');
      button.append(el('div', m.subject), el('div', m.priority + ' priority · ' + m.from, 'meta'));
      button.addEventListener('click', () => selectMessage(m.id));
      list.append(button);
    }
    if (messages.length) selectMessage(messages[0].id);
  } catch (error) {
    list.replaceChildren(el('p', 'Could not load demo: ' + error.message, 'error'));
  }
  await refreshTasks();
}
init();
