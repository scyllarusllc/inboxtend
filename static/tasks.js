'use strict';
const taskList = document.getElementById('tasks');
const taskSummary = document.getElementById('task-summary');
const taskSearch = document.getElementById('task-search');
const taskFilter = document.getElementById('task-filter');
const taskRefresh = document.getElementById('task-refresh');
let cachedTasks = [];
let openedTaskFromHash = false;
const GROUPS = [['overdue', 'Overdue'], ['due_soon', 'Due soon'], ['later', 'Later and no deadline'], ['done', 'Completed']];
const groupOf = task => task.status === 'done' ? 'done' : (task.deadline_state === 'overdue' ? 'overdue' : (task.deadline_state === 'due_soon' ? 'due_soon' : 'later'));
let doneOpen = false;

async function changeStatus(task, status) {
  try {
    await request('/api/tasks/' + task.id, 'PATCH', {revision: task.revision, status});
    notify(status === 'done' ? 'Marked as done.' : 'Status saved: ' + status.replaceAll('_', ' ') + '.');
    await refreshTasks();
  } catch (error) { notify(error.message + ' — refresh and retry.', true); await refreshTasks(); }
}

function renderAgentPanel() {
  const panel = document.getElementById('agent-panel');
  const state = agentSteersReadiness.state;
  const used = cachedTasks.some(t => t.agentsteers_job_id || (t.assigned_agent && t.assigned_agent !== ''));
  if (!used && !['configured_unverified', 'connected_mock', 'connection_failed'].includes(state)) { panel.hidden = true; return; }   // Nothing to say when the bridge is off and unused.
  panel.hidden = false;
  panel.replaceChildren();
  const summary = document.createElement('summary');
  summary.append(icon('refresh'), document.createTextNode(' AgentSteers (mock only)'));
  panel.append(summary);
  const readiness = el('p', agentSteersReadiness.message, 'meta');
  readiness.dataset.bridgeState = state;
  panel.append(readiness);
  if (agentSteersVerifiedThisPage) {
    panel.append(el('p', agentSteersQueue
      ? 'Remote mock queue snapshot · Total: ' + agentSteersQueue.total + ' · Queued: ' + agentSteersQueue.queued_mock +
        ' · Cancelled: ' + agentSteersQueue.cancelled + ' · Checked: ' + agentSteersQueueCheckedAt.toLocaleTimeString()
      : (agentSteersQueueError || 'Remote mock queue not yet checked.'), 'meta'));
  }
  const setupHelp = el('p', undefined, 'meta');
  setupHelp.append(document.createTextNode('Mock mode only · '));
  const setupLink = el('a', 'Connection setup and limitations');
  setupLink.href = 'https://github.com/scyllarusllc/inboxtend/blob/main/docs/agentsteers-handoff.md';
  setupLink.target = '_blank';
  setupLink.rel = 'noopener noreferrer';
  setupHelp.append(setupLink);
  panel.append(setupHelp);
  if (state === 'configured_unverified' || state === 'connected_mock' || state === 'connection_failed') {
    const probe = iconButton('refresh', 'Check AgentSteers connection');
    probe.addEventListener('click', async () => {
      probe.disabled = true;
      try {
        const data = await request('/api/agentsteers/probe', 'POST', {});
        agentSteersReadiness = data.bridge;
        agentSteersVerifiedThisPage = data.bridge.state === 'connected_mock';
        await loadMockQueueSummary();
        notify(agentSteersQueueError || agentSteersReadiness.message, Boolean(agentSteersQueueError) || !agentSteersVerifiedThisPage);
      } catch (error) {
        agentSteersVerifiedThisPage = false;
        agentSteersQueue = null;
        agentSteersQueueCheckedAt = null;
        agentSteersQueueError = 'Connection lost; remote queue snapshot is unavailable.';
        agentSteersReadiness = {state: 'connection_failed', can_dispatch: false, message: 'Connection check failed: ' + error.message};
        notify(agentSteersReadiness.message, true);
      }
      renderTasks();
      document.getElementById('agent-panel').open = true;
    });
    panel.append(probe);
  }
}

function taskCard(task) {
  const item = el('article', undefined, 'task-item');
  item.id = 'task-' + task.id;
  item.dataset.deadline = task.deadline_state;
  item.dataset.status = task.status;
  const head = el('div', undefined, 'task-header');
  const check = el('button', undefined, 'task-check');
  check.type = 'button';
  const finished = task.status === 'done';
  check.setAttribute('aria-pressed', String(finished));
  check.title = finished ? 'Completed. Press to reopen' : 'Mark as done';
  check.setAttribute('aria-label', check.title);
  check.append(icon('check'));
  check.addEventListener('click', () => { check.disabled = true; changeStatus(task, finished ? 'new' : 'done'); });
  const title = el('h3');
  const titleLink = el('a', task.title, 'task-title-link');
  titleLink.href = '/user/task/' + task.id;
  title.append(titleLink);
  head.append(check, title);
  if (task.title === 'Review student registration form issue (mock handoff)' && task.source_provider === 'demo') head.append(el('span', 'Fictional demo task', 'task-pill'));
  const labels = el('div', undefined, 'task-labels');
  if (task.status !== 'new' && !finished) {
    const statusBadge = el('span', task.status.replaceAll('_', ' '), 'task-pill');
    labels.append(statusBadge);
  }
  if (task.priority === 'high' && !finished) { const high = el('span', 'High', 'task-pill'); high.dataset.tone = 'warning'; labels.append(high); }
  head.append(labels);
  const meta = el('div', undefined, 'task-metadata');
  const due = el('span', dueText(task), 'task-due');
  due.dataset.state = finished ? 'done' : task.deadline_state;
  meta.append(due);
  if (task.priority !== 'high') meta.append(el('span', '·'), el('span', task.priority + ' priority'));
  if (task.owner) meta.append(el('span', '·'), el('span', 'Owner: ' + task.owner));
  if (task.project_label) meta.append(el('span', '·'), el('span', 'Project: ' + task.project_label));
  if (task.customer_label) meta.append(el('span', '·'), el('span', 'Customer: ' + task.customer_label));
  if (task.assigned_agent) meta.append(el('span', '·'), el('span', 'Agent: ' + task.assigned_agent + ' (' + task.agent_status + ')'));
  if (task.image_count) meta.append(el('span', '·'), el('span', task.image_count + ' image(s) from the email'));
  item.append(head, meta);
  if (task.description) { const text = el('p', task.description, 'task-description'); text.title = 'Press to show all'; text.addEventListener('click', () => text.classList.toggle('expanded')); item.append(text); }

  const actions = el('div', undefined, 'task-actions');
  const label = el('label', undefined);
  label.append(el('span', 'Status', 'meta'));
  const select = document.createElement('select');
  select.setAttribute('aria-label', 'Status of ' + task.title);
  for (const status of ['new', 'in_progress', 'waiting', 'done']) { const option = el('option', status.replaceAll('_', ' ')); option.value = status; select.append(option); }
  select.value = task.status;
  select.addEventListener('change', () => { select.disabled = true; changeStatus(task, select.value); });   // Saves as soon as it changes.
  label.append(select);
  actions.append(label);
  if (task.source_provider === 'gmail' && task.message_id) {
    const open = el('a', undefined, 'task-link');
    open.href = '/user/inbox?message=' + encodeURIComponent(task.message_id);
    open.append(icon('mail'), document.createTextNode(' Open the email'));
    actions.append(open);
  }
  if (task.agentsteers_job_id || task.agent_status === 'dispatch_error' || task.agent_status === 'recovery_needed') actions.append(makeAgentSteersControls(task));
  item.append(actions);

  const more = el('div', undefined, 'task-more');
  const editor = el('details');
  const editSummary = document.createElement('summary'); editSummary.append(icon('pencil'), document.createTextNode(' Edit'));
  editor.append(editSummary);
  editor.addEventListener('toggle', () => {
    if (editor.open && editor.children.length === 1) {
      editor.append(makeEditForm(task));
      editor.append(el('h4', 'AgentSteers mock handoff'), makeAgentSteersControls(task));
    }
  });
  const history = taskHistoryDetails(task);
  more.append(editor, history);
  if (task.image_count) more.append(taskImageDetails(task));
  item.append(more);
  return item;
}

function renderTasks() {
  taskList.replaceChildren();
  renderAgentPanel();
  for (const card of taskSummary.children) card.setAttribute('aria-pressed', String(card.dataset.filter === taskFilter.value));
  const term = taskSearch.value.trim().toLowerCase();
  const filter = taskFilter.value;
  const matching = cachedTasks.filter(task => {
    const searchable = [task.title, task.description, task.owner, task.project_label, task.customer_label].filter(Boolean).join(' ').toLowerCase();
    if (term && !searchable.includes(term)) return false;
    if (filter === 'active') return task.status !== 'done';
    if (filter === 'done') return task.status === 'done';
    if (['new', 'in_progress', 'waiting'].includes(filter)) return task.status === filter;
    if (filter === 'overdue' || filter === 'due_soon') return task.deadline_state === filter && task.status !== 'done';
    return true;
  });
  const info = document.getElementById('task-info');
  info.textContent = (term || filter !== 'all') ? matching.length + ' of ' + cachedTasks.length + ' task' + (cachedTasks.length === 1 ? '' : 's') + ' shown.' : '';
  if (!matching.length) {
    taskList.append(el('p', cachedTasks.length ? 'No tasks match the current filter.' : 'No saved tasks yet. Open an inbox message and suggest a task to create one.'));
    return;
  }
  let opened = null;
  for (const [key, name] of GROUPS) {
    const rows = matching.filter(task => groupOf(task) === key);
    if (!rows.length) continue;
    const grouped = filter === 'all' || filter === 'active';   // A specific filter is already one group; the headings would repeat it.
    if (key === 'done' && grouped) {
      const box = el('details', undefined, 'task-group task-group-done');
      box.open = doneOpen || Boolean(term) || location.hash.startsWith('#task-');
      box.addEventListener('toggle', () => { doneOpen = box.open; });
      const summary = document.createElement('summary');
      summary.append(el('span', name), el('span', String(rows.length), 'group-count'));
      box.append(summary);
      const list = el('div', undefined, 'task-group-list');
      for (const task of rows) list.append(taskCard(task));
      box.append(list);
      taskList.append(box);
    } else {
      const section = el('section', undefined, 'task-group');
      section.dataset.group = key;
      if (grouped) { const heading = el('h3', undefined, 'task-group-heading'); heading.append(el('span', name), el('span', String(rows.length), 'group-count')); section.append(heading); }
      const list = el('div', undefined, 'task-group-list');
      for (const task of rows) list.append(taskCard(task));
      section.append(list);
      taskList.append(section);
    }
  }
  const target = location.hash.startsWith('#task-') ? document.getElementById(location.hash.slice(1)) : null;
  if (target && !openedTaskFromHash) {
    openedTaskFromHash = true;
    const images = target.querySelector('details.task-images');
    if (images) images.open = true;
    requestAnimationFrame(() => target.scrollIntoView({behavior: 'smooth', block: 'start'}));
  }
}
async function refreshTasks() {
  try {
    const [overview,rows,bridge]=await Promise.all([
      request('/api/tasks/summary'),
      request('/api/tasks'),
      request('/api/agentsteers/status').catch(()=>({bridge:{state:'unavailable',can_dispatch:false,message:'Unable to check bridge readiness; dispatch is disabled.'}}))
    ]);
    cachedTasks=rows.tasks;
    agentSteersReadiness=bridge.bridge;
    if(agentSteersReadiness.state==='configured_unverified' && agentSteersVerifiedThisPage) {
      agentSteersReadiness={state:'connected_mock',can_dispatch:true,
        message:'Authenticated mock MCP connection verified in this browser session. No real worker is enabled.'};
    } else if(agentSteersReadiness.state!=='configured_unverified') {
      agentSteersVerifiedThisPage=false;
      agentSteersQueue=null;
      agentSteersQueueCheckedAt=null;
      agentSteersQueueError=null;
    }
    const summary=overview.summary;
    taskSummary.replaceChildren();
    for(const [label,count,filter] of [
      ['Total',summary.total,'all'],['New',summary.by_status.new,'new'],
      ['In progress',summary.by_status.in_progress,'in_progress'],['Waiting',summary.by_status.waiting,'waiting'],
      ['Done',summary.by_status.done,'done'],['Due soon',summary.due_soon,'due_soon'],['Overdue',summary.overdue,'overdue']
    ]){
      const card=el('button',undefined,'metric-card');
      card.type='button';
      card.dataset.filter=filter;
      card.title='Show: '+label.toLowerCase();
      if(label==='Overdue')card.dataset.tone='warning';
      if(label==='Due soon')card.dataset.tone='info';
      card.append(el('span',label,'metric-label'),el('strong',String(count),'metric-value'));
      card.addEventListener('click',()=>{taskFilter.value=card.getAttribute('aria-pressed')==='true'&&filter!=='all'?'all':filter;renderTasks();});
      taskSummary.append(card);
    }
    renderTasks();
  }catch(error){notify('Could not load tasks: '+error.message,true);}
}

// Only poll mock jobs while this page is open, visible, and a user has
// successfully checked connectivity. One job per minute to limit load.
async function syncOneMockJob() {
  if(syncInFlight || !agentSteersVerifiedThisPage || document.visibilityState!=='visible')return;
  const jobs=cachedTasks.filter(task=>task.agentsteers_job_id && task.agent_status==='queued_mock');
  if(!jobs.length)return;
  const job=jobs[nextSyncOffset % jobs.length];
  nextSyncOffset++;
  syncInFlight=true;
  try {
    await request('/api/tasks/'+job.id+'/agentsteers-refresh','POST',{});
    await refreshTasks();
    await loadMockQueueSummary();
    renderTasks();
  } catch(_) {
    // A dropped connection is not a completed task. Keep the last saved state;
    // manual refresh and the connection check remain available.
    agentSteersVerifiedThisPage=false;
    agentSteersQueue=null;
    agentSteersQueueCheckedAt=null;
    agentSteersQueueError='Connection lost; remote queue snapshot is unavailable.';
    agentSteersReadiness={state:'connection_failed',can_dispatch:false,
      message:'AgentSteers status sync stopped after a connection error. Recheck connection.'};
    renderTasks();
  } finally {
    syncInFlight=false;
  }
}

async function init() {
  taskSearch.addEventListener('input',renderTasks);
  taskFilter.addEventListener('change',renderTasks);
  taskRefresh.prepend(icon('refresh'));
  taskRefresh.classList.add('icon-button');
  taskRefresh.addEventListener('click',async()=>{notify('');await refreshTasks();});
  try { await sessionReady; } catch (error) { notify('Could not load session: '+error.message,true); return; }
  await settingsReady;
  document.addEventListener('keydown',event=>{   // "/" jumps to the search box, like the Inbox.
    const typing=/^(input|textarea|select)$/i.test((event.target&&event.target.tagName)||'');
    if(event.key==='/'&&!typing&&!event.ctrlKey&&!event.metaKey&&!event.altKey){event.preventDefault();taskSearch.focus();}
  });
  await refreshTasks();
  window.setInterval(syncOneMockJob,60000);
}
init();
