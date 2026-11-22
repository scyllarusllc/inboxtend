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

function aiPlanProgress() {
  const node = el('div', undefined, 'review-progress ai-work-progress');
  const track = el('div', undefined, 'progress-track');
  track.setAttribute('role', 'progressbar');
  track.setAttribute('aria-label', 'AI plan preparation');
  const fill = el('div', undefined, 'progress-fill');
  const label = el('p', undefined, 'meta');
  track.append(fill);
  node.append(track, label);
  const started = Date.now();
  const tick = () => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    label.textContent = 'AI is preparing the plan · ' + seconds + 's elapsed';
    track.setAttribute('aria-valuetext', label.textContent);
  };
  tick();
  const timer = window.setInterval(tick, 1000);
  return {node, finish(ok) {
    window.clearInterval(timer);
    if (ok) {
      node.classList.add('is-complete');
      label.textContent = 'Plan ready in ' + Math.round((Date.now() - started) / 1000) + 's';
      track.setAttribute('aria-valuenow', '100');
      track.setAttribute('aria-valuetext', label.textContent);
    } else node.remove();
  }};
}

async function startWorking(task, button, refresh = false) {
  if (lastPlan && !refresh) { document.getElementById('ai-plan')?.scrollIntoView({behavior: 'smooth', block: 'start'}); return; }
  const label = button.lastChild.textContent;
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  button.replaceChildren(icon('sparkles'), document.createTextNode(' Thinking about how to handle this…'));
  const progress = aiPlanProgress();
  button.after(progress.node);
  try {
    const result = await request('/api/ai/work', 'POST', refresh ? {task_id: task.id, refresh: true} : {task_id: task.id});
    progress.finish(true);
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
    progress.finish(false);
    notify(error.message, true);
    button.disabled = false;
    button.replaceChildren(icon('sparkles'), document.createTextNode(label));
  } finally {
    button.removeAttribute('aria-busy');
  }
}

// Plan text comes as {en, zh} (older saved plans: a plain string). The plan card on the left is English; the card
// on its right is the same plan translated into Chinese by the AI (one above the other on a narrow screen).
const hasText = value => value && (typeof value === 'object' ? (value.en || value.zh) : value);
const enOf = value => (value && typeof value === 'object') ? (value.en || '') : (value || '');
const zhOf = value => (value && typeof value === 'object') ? (value.zh || '') : '';
const planHasChinese = p => [p.summary, p.approach].some(v => zhOf(v));

// The plan's content in one language: `pick` returns that language's text of an {en, zh} value, `head` a section title.
function planBody(p, pick, head, draft) {
  const body = el('div', undefined, 'plan-body');
  if (pick(p.summary)) body.append(el('p', pick(p.summary), 'ai-summary'));
  if (pick(p.approach)) body.append(el('p', pick(p.approach)));
  if (p.steps.length) {
    body.append(el('h3', head.steps));
    const steps = el('ol', undefined, 'ai-steps');
    for (const step of p.steps) {
      const li = el('li');
      if (pick(step.title)) li.append(el('strong', pick(step.title)));
      if (pick(step.detail)) li.append(el('span', pick(step.detail), 'meta'));
      steps.append(li);
    }
    body.append(steps);
  }
  for (const [key, items] of [['questions', p.questions], ['risks', p.risks]]) {
    const texts = items.map(pick).filter(Boolean);
    if (!texts.length) continue;
    body.append(el('h3', head[key]));
    const list = el('ul', undefined, 'ai-list');
    for (const text of texts) list.append(el('li', text));
    body.append(list);
  }
  if (draft) { body.append(el('h3', head.draft)); body.append(el('pre', draft, 'ai-draft')); }
  return body;
}

function planPanel(plan, task) {
  const p = plan.plan;
  const pair = el('div', undefined, 'plan-pair');
  const box = el('section', undefined, 'panel ai-plan');
  box.id = 'ai-plan';
  const head = el('div', undefined, 'ai-plan-head');
  head.append(icon('sparkles'), el('h2', 'How the AI would handle this'),
    el('span', p.confidence + ' confidence', 'task-pill'));
  box.append(head, el('p', 'Saved ' + new Date(plan.created_at).toLocaleString() + ' · ' + plan.provider + ' · ' + plan.model + ' · A guess, not a fact: check it against the email.' +
    (plan.email_used ? '' : ' ' + (plan.note || 'Based on the task text only.')), 'meta'));
  box.append(planBody(p, enOf, {steps: 'Steps', questions: 'Confirm first', risks: 'Watch out for', draft: 'Reply draft (not sent)'}, p.reply_draft));
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

  const zh = el('section', undefined, 'panel ai-plan ai-plan-zh');
  zh.lang = 'zh';
  const zhHead = el('div', undefined, 'ai-plan-head');
  zhHead.append(icon('language'), el('h2', 'AI 中文翻译'));
  zh.append(zhHead);
  if (planHasChinese(p)) {
    zh.append(el('p', '由 AI 翻译自左侧英文方案，仅供参考，请对照原文核实。', 'meta'));
    zh.append(planBody(p, zhOf, {steps: '步骤', questions: '先确认', risks: '注意风险', draft: '回复草稿中文译文（未发送）'}, p.reply_draft_zh));
  } else {
    zh.append(el('p', '这份方案还没有中文翻译。点下面的按钮，让 AI 翻译并保存。', 'meta'));
    const translate = iconButton('language', 'Translate with AI');
    translate.addEventListener('click', async () => {
      translate.disabled = true;
      translate.replaceChildren(icon('language'), document.createTextNode(' 正在翻译…'));
      try {
        const result = await request('/api/ai/work-translate', 'POST', {task_id: task.id});
        lastPlan = result.saved;
        notify('已翻译并保存。');
        await refreshTasks();
      } catch (error) {
        notify(error.message, true);
        translate.disabled = false;
        translate.replaceChildren(icon('language'), document.createTextNode('Translate with AI'));
      }
    });
    zh.append(translate);
  }
  pair.append(box, zh);
  return pair;
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
  if (lastPlan) detailBox.append(planPanel(lastPlan, task));   // Full width: English plan on the left, its Chinese translation on the right.
  if (task.image_count) {
    // The images on the left; what the AI extracted from them and what it thinks they mean on the right.
    const pair = el('div', undefined, 'plan-pair');
    const reviewBox = el('div', undefined, 'task-image-review');
    const images = taskImageDetails(task, reviewBox);
    pair.append(section('Images', images), section('Extracted text and AI explanation', reviewBox));
    detailBox.append(pair);
    images.open = true;
  }
  // Two columns on a wide screen: the work (plan, images, edit) on the left, context (email, history) on the right.
  const columns = el('div', undefined, 'task-columns');
  const mainCol = el('div', undefined, 'task-main');
  const side = el('aside', undefined, 'task-side');
  columns.append(mainCol, side);
  detailBox.append(columns);
  
  const editBox = el('details', undefined, 'panel task-section task-edit');
  editBox.id = 'task-edit';
  const editSummary = el('summary', 'Edit task');
  editBox.append(editSummary, makeEditForm(task));
  mainCol.append(editBox);
  if (task.agentsteers_job_id || task.assigned_agent || task.agent_status === 'dispatch_error') {
    mainCol.append(section('AgentSteers mock handoff', makeAgentSteersControls(task)));
  }
  if (task.source_provider === 'gmail' && task.message_id) side.append(emailCard(task));
  if (task.message_id) side.append(correspondenceCard(task));
  const history = taskHistoryDetails(task);
  history.open = true;
  const stamps = el('p', 'Created ' + (task.created_at ? new Date(task.created_at).toLocaleString() : '—') + ' · Updated ' + (task.updated_at ? new Date(task.updated_at).toLocaleString() : '—'), 'meta');
  side.append(section('History', history, stamps));
}

function correspondenceCard(task) {
  const box = section('Emails with this sender');
  const summary = el('p', 'Loading saved emails…', 'meta');
  const list = el('ol', undefined, 'correspondence-list');
  const more = el('button', 'Load more emails');
  more.type = 'button';
  more.hidden = true;
  box.append(summary, list, more);
  let offset = 0;
  const load = async () => {
    more.disabled = true;
    try {
      const result = await request('/api/tasks/' + task.id + '/correspondence?offset=' + offset);
      if (!box.isConnected) return;
      summary.textContent = result.address ? result.total + ' saved email' + (result.total === 1 ? '' : 's') + ' with ' + result.address : 'No saved sender history for this task.';
      for (const mail of result.messages) {
        const item = el('li', undefined, 'correspondence-item');
        const link = el('a', mail.subject || '(No subject)');
        link.href = '/user/mail/' + encodeURIComponent(mail.id);
        if (mail.is_source) link.append(el('span', ' · task source', 'meta'));
        item.append(link, el('span', (mail.direction === 'sent' ? 'Sent' : 'Received') + ' · ' + formatWhen(mail.internal_date, mail.date || ''), 'meta'));
        if (mail.snippet) item.append(el('p', mail.snippet, 'email-excerpt'));
        list.append(item);
      }
      offset = result.next_offset;
      more.hidden = offset === null;
    } catch (error) {
      summary.textContent = 'Could not load sender history: ' + error.message;
      more.hidden = true;
    } finally { more.disabled = false; }
  };
  more.addEventListener('click', load);
  load();
  return box;
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
