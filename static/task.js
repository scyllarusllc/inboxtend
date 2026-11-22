'use strict';
// One task on its own page: /user/task/<id>. Reuses the pieces in task-parts.js.
const detailBox = document.getElementById('task-detail');
const taskId = Number((location.pathname.match(/\/user\/task\/(\d+)/) || [])[1]);
let currentTask = null;

async function changeStatus(task, status) {
  try {
    await request('/api/tasks/' + task.id, 'PATCH', {revision: task.revision, status});
    notify(status === 'done' ? 'Marked as done.' : 'Status saved: ' + status.replaceAll('_', ' ') + '.');
  } catch (error) { notify(error.message + ' — reloaded the latest version.', true); }
  await refreshTasks();
}

function section(title, ...children) {
  const box = el('section', undefined, 'panel task-section');
  box.append(el('h2', title), ...children);
  return box;
}

function fact(label, value) {
  const row = el('div', undefined, 'task-fact');
  row.append(el('dt', label), el('dd', value || '—'));
  return row;
}

function renderTask(task) {
  currentTask = task;
  document.title = task.title + ' · Inbox Tend';
  detailBox.replaceChildren();
  const finished = task.status === 'done';

  const card = el('article', undefined, 'task-item task-hero');
  card.dataset.deadline = task.deadline_state;
  card.dataset.status = task.status;
  const head = el('div', undefined, 'task-header');
  const check = el('button', undefined, 'task-check');
  check.type = 'button';
  check.setAttribute('aria-pressed', String(finished));
  check.title = finished ? 'Completed. Press to reopen' : 'Mark as done';
  check.setAttribute('aria-label', check.title);
  check.append(icon('check'));
  check.addEventListener('click', () => { check.disabled = true; changeStatus(task, finished ? 'new' : 'done'); });
  const title = el('h1', task.title);
  head.append(check, title);
  if (task.priority === 'high' && !finished) { const high = el('span', 'High', 'task-pill'); high.dataset.tone = 'warning'; head.append(high); }
  card.append(head);

  const due = el('p', dueText(task), 'task-due');
  due.dataset.state = finished ? 'done' : task.deadline_state;
  card.append(due);
  if (task.description) card.append(el('p', task.description, 'task-body'));

  const actions = el('div', undefined, 'task-actions');
  const label = el('label');
  label.append(el('span', 'Status', 'meta'));
  const select = document.createElement('select');
  select.setAttribute('aria-label', 'Status of ' + task.title);
  for (const status of ['new', 'in_progress', 'waiting', 'done']) { const option = el('option', status.replaceAll('_', ' ')); option.value = status; select.append(option); }
  select.value = task.status;
  select.addEventListener('change', () => { select.disabled = true; changeStatus(task, select.value); });
  label.append(select);
  actions.append(label);
  if (task.source_provider === 'gmail' && task.message_id) {
    const open = el('a', undefined, 'task-link');
    open.href = '/user/mail/' + encodeURIComponent(task.message_id);
    open.append(icon('mail'), document.createTextNode(' Open the email'));
    actions.append(open);
  }
  card.append(actions);
  detailBox.append(card);

  const facts = el('dl', undefined, 'task-facts');
  facts.append(
    fact('Priority', task.priority), fact('Owner', task.owner), fact('Project', task.project_label),
    fact('Customer', task.customer_label),
    fact('Deadline', task.due_date ? task.due_date + (task.due_time ? ' ' + task.due_time : '') + ' ' + (task.due_timezone || '') : 'No deadline'),
    fact('Source', task.source_provider === 'demo' ? 'Demo data' : (task.source_provider || 'Manual')),
    fact('Created', task.created_at ? new Date(task.created_at).toLocaleString() : ''),
    fact('Updated', task.updated_at ? new Date(task.updated_at).toLocaleString() : ''));
  if (task.assigned_agent) facts.append(fact('AI agent', task.assigned_agent + ' (' + task.agent_status + ')'));
  detailBox.append(section('Details', facts));

  detailBox.append(section('Edit', makeEditForm(task)));
  const history = taskHistoryDetails(task);
  history.open = true;
  detailBox.append(section('History', history));
  if (task.image_count) {
    const images = taskImageDetails(task);
    detailBox.append(section('Images', images));
    images.open = true;
  }
  if (task.agentsteers_job_id || task.assigned_agent || task.agent_status === 'dispatch_error') {
    detailBox.append(section('AgentSteers mock handoff', makeAgentSteersControls(task)));
  }
}

// Same name as on the list page: the edit form and image tools call it after a change.
async function refreshTasks() {
  try {
    const result = await request('/api/tasks/' + taskId);
    renderTask(result.task);
  } catch (error) {
    detailBox.replaceChildren();
    const missing = /not found/i.test(error.message);
    const box = el('div', undefined, 'panel');
    box.append(el('h1', missing ? 'Task not found' : 'Could not load the task'), el('p', error.message, 'meta'));
    const back = el('a', 'Back to all tasks'); back.href = '/user/tasks'; box.append(back);
    detailBox.append(box);
  }
}

async function init() {
  try { await sessionReady; } catch (error) { notify('Could not load session: ' + error.message, true); return; }
  await settingsReady;
  try {
    const bridge = await request('/api/agentsteers/status');
    agentSteersReadiness = bridge.bridge;
  } catch (_) { /* Dispatch stays disabled. */ }
  await refreshTasks();
}
init();
