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

// Plan text comes as {en, zh} (older saved plans: a plain string). English on the left, Chinese on the right
// (one above the other on a narrow screen).
const hasText = value => value && (typeof value === 'object' ? (value.en || value.zh) : value);
const enOf = value => (value && typeof value === 'object') ? (value.en || '') : (value || '');
const zhOf = value => (value && typeof value === 'object') ? (value.zh || '') : '';

// One row: the English node and the Chinese node side by side.
function biRow(enNode, zhNode, cls) {
  const row = el('div', undefined, 'bi-row' + (cls ? ' ' + cls : ''));
  const left = el('div', undefined, 'bi-en'); left.append(...enNode);
  row.append(left);
  if (zhNode.length) { const right = el('div', undefined, 'bi-zh'); right.lang = 'zh'; right.append(...zhNode); row.append(right); }
  else row.classList.add('bi-single');
  return row;
}
// A paragraph-like row from one {en, zh} value; `prefix` (a number or bullet) goes in front of both sides.
function biLine(value, cls, prefix = '') {
  const side = text => text ? [el('p', prefix + text, cls)] : [];
  return biRow(side(enOf(value)), side(zhOf(value)));
}
function biHeading(en, withChinese = true) {
  return biRow([el('h3', en)], withChinese && ZH[en] ? [el('h3', ZH[en])] : [], 'bi-heading');
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
  const body = el('div', undefined, 'bi-grid');
  const bilingualPlan = [p.summary, p.approach].some(v => zhOf(v));   // Older saved plans have English only: no right-hand column.
  if (bilingualPlan) body.append(biRow([el('h3', 'English')], [el('h3', '中文')], 'bi-heading bi-columns'));
  if (hasText(p.summary)) body.append(biLine(p.summary, 'ai-summary'));
  if (hasText(p.approach)) body.append(biLine(p.approach));
  if (p.steps.length) {
    body.append(biHeading('Steps', bilingualPlan));
    p.steps.forEach((step, index) => {
      const side = pick => {
        const title = pick(step.title), detail = pick(step.detail);
        const nodes = [];
        if (title) nodes.push(el('strong', (index + 1) + '. ' + title, 'ai-step-title'));
        if (detail) nodes.push(el('span', detail, 'meta'));
        if (!nodes.length) return [];
        const wrap = el('div', undefined, 'ai-step'); wrap.append(...nodes);
        return [wrap];
      };
      body.append(biRow(side(enOf), side(zhOf)));
    });
  }
  for (const [heading, items] of [['Confirm first', p.questions], ['Watch out for', p.risks]]) {
    if (!items.length) continue;
    body.append(biHeading(heading, bilingualPlan));
    for (const item of items) body.append(biLine(item, 'ai-item', '• '));
  }
  if (p.reply_draft) {
    body.append(biHeading('Reply draft (not sent)', bilingualPlan));
    body.append(biRow([el('pre', p.reply_draft, 'ai-draft')], p.reply_draft_zh ? [el('pre', p.reply_draft_zh, 'ai-draft ai-draft-zh')] : []));
  }
  box.append(body);
  if (p.reply_draft) {
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
  card.append(head);

  const due = el('p', dueText(task), 'task-due');
  due.dataset.state = finished ? 'done' : task.deadline_state;
  card.append(due);
  const chips = el('div', undefined, 'task-chips');
  if (task.priority === 'high' && !finished) chips.dataset.tone = 'high';
  for (const [name, value] of [['Priority', task.priority + ' priority'], ['Owner', task.owner], ['Project', task.project_label], ['Customer', task.customer_label],
      ['Agent', task.assigned_agent ? task.assigned_agent + ' (' + task.agent_status + ')' : '']]) {
    if (value) chips.append(el('span', name === 'Priority' ? value : name + ': ' + value, 'task-pill'));
  }
  card.append(chips);
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
  edit.addEventListener('click', () => { const f = document.getElementById('task-edit'); f.open = true; f.scrollIntoView({behavior: 'smooth', block: 'start'}); f.querySelector('input').focus({preventScroll: true}); });
  actions.append(edit);
  card.append(actions);
  detailBox.append(card);
  // Two columns on a wide screen: the work (plan, images, edit) on the left, context (email, history) on the right.
  const columns = el('div', undefined, 'task-columns');
  const mainCol = el('div', undefined, 'task-main');
  const side = el('aside', undefined, 'task-side');
  columns.append(mainCol, side);
  detailBox.append(columns);
  if (lastPlan) mainCol.append(planPanel(lastPlan, task));
  if (task.image_count) {
    const images = taskImageDetails(task);
    mainCol.append(section('Images', images));
    images.open = true;
  }
  const editBox = el('details', undefined, 'panel task-section task-edit');
  editBox.id = 'task-edit';
  const editSummary = el('summary', 'Edit task');
  editBox.append(editSummary, makeEditForm(task));
  mainCol.append(editBox);
  if (task.agentsteers_job_id || task.assigned_agent || task.agent_status === 'dispatch_error') {
    mainCol.append(section('AgentSteers mock handoff', makeAgentSteersControls(task)));
  }
  if (task.source_provider === 'gmail' && task.message_id) side.append(emailCard(task));
  const history = taskHistoryDetails(task);
  history.open = true;
  const stamps = el('p', 'Created ' + (task.created_at ? new Date(task.created_at).toLocaleString() : '—') + ' · Updated ' + (task.updated_at ? new Date(task.updated_at).toLocaleString() : '—'), 'meta');
  side.append(section('History', history, stamps));
}

// What the task came from: subject, sender and the start of the email. Quiet if the email cannot be loaded.
let emailContext = null;
function emailCard(task) {
  const box = section('From this email');
  const fill = m => {
    box.append(el('strong', m.subject || '(No subject)'), el('p', 'From ' + (m.from || 'unknown') + ' · ' + formatWhen(m.internal_date, m.date || ''), 'meta'));
    const text = (m.body_text || m.snippet || '').replace(/\s+/g, ' ').trim();
    if (text) box.append(el('p', text.length > 360 ? text.slice(0, 360) + '…' : text, 'email-excerpt'));
    const more = el('a', 'Read the full email'); more.href = '/user/mail/' + encodeURIComponent(task.message_id); box.append(more);
  };
  if (emailContext && emailContext.id === task.message_id) { fill(emailContext); return box; }
  box.hidden = true;   // Shown only once the email is loaded.
  request('/api/connectors/gmail/messages/' + encodeURIComponent(task.message_id))
    .then(result => { emailContext = result.message; fill(emailContext); box.hidden = false; })
    .catch(() => {});
  return box;
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
