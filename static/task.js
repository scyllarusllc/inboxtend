'use strict';
// One task on its own page: /user/task/<id>. Reuses the pieces in task-parts.js.
const detailBox = document.getElementById('task-detail');
const taskId = Number((location.pathname.match(/\/user\/task\/(\d+)/) || [])[1]);
let currentTask = null;
let lastPlan = null;   // The saved AI plan of this task (kept in the database; loaded with the page).

async function changeStatus(task, status) {
  try {
    await request('/api/tasks/' + task.id, 'PATCH', {revision: task.revision, status});
    notify(status === 'done' ? 'Marked as done.' : 'Status saved: ' + status.replaceAll('_', ' ') + '.');
  } catch (error) { notify(error.message + ' — reloaded the latest version.', true); }
  await refreshTasks();
}

async function startWorking(task, button, refresh = false) {
  if (lastPlan && !refresh) { document.getElementById('ai-plan')?.scrollIntoView({behavior: 'smooth', block: 'start'}); return; }
  const label = button.lastChild.textContent;
  button.disabled = true;
  button.replaceChildren(icon('sparkles'), document.createTextNode(' Thinking about how to handle this…'));
  try {
    const result = await request('/api/ai/work', 'POST', refresh ? {task_id: task.id, refresh: true} : {task_id: task.id});
    const first = !lastPlan;
    lastPlan = result.saved;   // Saved in the database by the server.
    // Starting work moves a new or waiting task to In progress.
    if (first && (task.status === 'new' || task.status === 'waiting')) {
      try { await request('/api/tasks/' + task.id, 'PATCH', {revision: task.revision, status: 'in_progress'}); } catch (_) { /* The plan is still useful. */ }
    }
    notify('AI plan saved with this task. It is a guess: check it before acting.');
    await refreshTasks();
    document.getElementById('ai-plan')?.scrollIntoView({behavior: 'smooth', block: 'start'});
  } catch (error) {
    notify(error.message, true);
    button.disabled = false;
    button.replaceChildren(icon('sparkles'), document.createTextNode(label));
  }
}

function planList(tag, items, cls) {
  const list = el(tag, undefined, cls);
  for (const item of items) list.append(el('li', item));
  return list;
}

function planPanel(plan, task) {
  const p = plan.plan;
  const box = el('section', undefined, 'panel ai-plan');
  box.id = 'ai-plan';
  const head = el('div', undefined, 'ai-plan-head');
  head.append(icon('sparkles'), el('h2', 'How the AI would handle this'),
    el('span', p.confidence + ' confidence', 'task-pill'));
  box.append(head, el('p', 'Saved ' + new Date(plan.created_at).toLocaleString() + ' · ' + plan.provider + ' · ' + plan.model + ' · A guess, not a fact: check it against the email.' +
    (plan.email_used ? '' : ' ' + (plan.note || 'Based on the task text only.')), 'meta'));
  if (p.summary) box.append(el('p', p.summary, 'ai-summary'));
  if (p.approach) box.append(el('p', p.approach));
  if (p.steps.length) {
    box.append(el('h3', 'Steps'));
    const steps = el('ol', undefined, 'ai-steps');
    for (const step of p.steps) {
      const li = el('li');
      li.append(el('strong', step.title));
      if (step.detail) li.append(el('span', step.detail, 'meta'));
      steps.append(li);
    }
    box.append(steps);
  }
  if (p.questions.length) box.append(el('h3', 'Confirm first'), planList('ul', p.questions, 'ai-list'));
  if (p.risks.length) box.append(el('h3', 'Watch out for'), planList('ul', p.risks, 'ai-list'));
  if (p.reply_draft) {
    box.append(el('h3', 'Reply draft (not sent)'));
    box.append(el('pre', p.reply_draft, 'ai-draft'));
    const copy = iconButton('copy', 'Copy the draft');
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(p.reply_draft); notify('Draft copied.'); } catch (_) { notify('Copy failed; select the text instead.', true); }
    });
    box.append(copy);
  }
  const again = iconButton('refresh', 'Ask the AI again');
  again.title = 'Replaces the saved plan with a new one';
  again.addEventListener('click', () => startWorking(task, again, true));
  box.append(again);
  return box;
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

  // Big buttons: the things a person does with a task, one press each.
  const actions = el('div', undefined, 'hero-actions');
  const big = (name, text, kind) => { const b = iconButton(name, text); b.className = 'big-action' + (kind ? ' ' + kind : ''); return b; };
  const main = finished ? big('refresh', 'Reopen task') : big('check', 'Mark as done', 'primary');
  main.addEventListener('click', () => { main.disabled = true; changeStatus(task, finished ? 'new' : 'done'); });
  actions.append(main);
  if (!finished) {
    const work = big('sparkles', lastPlan ? 'Show the AI plan' : 'Start working with AI', 'ai');
    work.title = lastPlan ? 'Already planned: this shows the saved plan without asking the AI again.' : 'The AI reads this task and its email, then guesses how to handle it. It only advises; nothing is sent. The answer is saved with the task.';
    work.addEventListener('click', () => startWorking(task, work));
    actions.append(work);
    const wait = big('clock', 'Waiting');
    wait.setAttribute('aria-pressed', String(task.status === 'waiting'));
    wait.addEventListener('click', () => { wait.disabled = true; changeStatus(task, task.status === 'waiting' ? 'new' : 'waiting'); });
    actions.append(wait);
  }
  if (task.source_provider === 'gmail' && task.message_id) {
    const open = el('a', undefined, 'big-action');
    open.href = '/user/mail/' + encodeURIComponent(task.message_id);
    open.append(icon('mail'), document.createTextNode(' Open the email'));
    actions.append(open);
  }
  const edit = big('pencil', 'Edit');
  edit.addEventListener('click', () => { const f = document.getElementById('task-edit'); f.scrollIntoView({behavior: 'smooth', block: 'start'}); f.querySelector('input').focus({preventScroll: true}); });
  actions.append(edit);
  card.append(actions);
  detailBox.append(card);
  if (lastPlan) detailBox.append(planPanel(lastPlan, task));

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

  const editBox = section('Edit', makeEditForm(task));
  editBox.id = 'task-edit';
  detailBox.append(editBox);
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
  try { lastPlan = (await request('/api/tasks/' + taskId + '/plan')).saved; } catch (_) { lastPlan = null; }
  await refreshTasks();
}
init();
