'use strict';
const taskList = document.getElementById('tasks');
const taskSummary = document.getElementById('task-summary');
const taskSearch = document.getElementById('task-search');
const taskFilter = document.getElementById('task-filter');
const taskRefresh = document.getElementById('task-refresh');
let cachedTasks = [];
let openedTaskFromHash = false;
function taskImageDetails(task) {
  const panel=el('details',undefined,'task-images');
  const heading=document.createElement('summary');
  heading.append(icon('paperclip'),document.createTextNode(' Images ('+task.image_count+')'));
  panel.append(heading);
  panel.addEventListener('toggle',async()=>{
    if(!panel.open || panel.dataset.loaded==='true')return;
    panel.dataset.loaded='true';
    const body=el('div','Loading task images…','task-image-list');panel.append(body);
    try {
      const result=await request('/api/tasks/'+task.id+'/images');
      body.replaceChildren();
      for(const file of result.images) {
        const row=el('div',undefined,'attachment-row');
        row.append(el('strong',file.name || 'Unnamed image'),el('span',(file.size_bytes/1048576).toFixed(1)+' MB','meta'));
        if(file.stored) {
          const base='/api/tasks/'+task.id+'/images/'+file.id;
          const download=el('a','Download','icon-button');download.href=base+'/download';download.setAttribute('download',file.name);
          row.append(download);
          if(['image/jpeg','image/png','image/gif','image/webp'].includes(file.mime_type) && file.size_bytes<=2*1024*1024) {
            const image=document.createElement('img');image.className='attachment-image';image.alt='Preview of '+file.name;
            image.loading='lazy';image.src=base+'/thumbnail';
            image.addEventListener('error',()=>image.replaceWith(el('p','Preview unavailable.','meta')));
            row.append(image);
          }
        } else row.append(el('p','Image was not stored: '+(file.skipped_reason || 'unavailable'),'meta'));
        body.append(row);
      }
      const reviewBox=el('div',undefined,'task-image-review');
      const run=iconButton('language','Translate and explain all '+result.images.length+' images in Chinese');
      body.prepend(run);
      body.append(reviewBox);
      const showReview=review=>{
        reviewBox.replaceChildren();
        if(!review)return;
        const section=el('section',undefined,'image-review');
        section.append(el('h4','Chinese translation and image interpretation'),
          el('p',review.provider+' · '+review.model+' · AI guesses may be wrong; check the images.','meta'));
        for(const item of review.images) {
          const part=el('div',undefined,'image-review-item');
          part.append(el('strong',item.number+'. '+item.name),
            el('p','图中文字（中文）：'+(item.visible_text_zh || '没有可辨认的文字。')),
            el('p','图片含义：'+(item.meaning_zh || '无法确定。')));
          section.append(part);
        }
        section.append(el('p','整体说明：'+(review.summary_zh || '无法确定。')));
        const apply=iconButton('check','Use suggested task text');
        apply.addEventListener('click',async()=>{
          apply.disabled=true;
          try {
            await request('/api/tasks/'+task.id,'PATCH',{revision:task.revision,
              title:review.task_title,description:review.task_description || review.summary_zh});
            notify('Task text updated from the image review. Check it before acting.');
            await refreshTasks();
          } catch(error) {notify(error.message,true);apply.disabled=false;}
        });
        section.append(apply);reviewBox.append(section);
      };
      showReview(result.review);
      run.addEventListener('click',async()=>{
        run.disabled=true;
        run.textContent='Translating and explaining images…';
        try {
          const answer=await request('/api/tasks/'+task.id+'/images/review','POST',{});
          showReview(answer.review);
          notify('Image review saved with this task.');
        } catch(error) {notify(error.message,true);}
        finally {
          run.replaceChildren(icon('language'),document.createTextNode('Translate and explain all '+result.images.length+' images in Chinese'));
          run.disabled=false;
        }
      });
      if(!userSettings.ai_enabled) {
        const hint=el('p',undefined,'meta');
        hint.append(document.createTextNode('The AI assistant may need to be enabled in '));
        const link=el('a','Settings');link.href='/user/settings#ai';hint.append(link,document.createTextNode('.'));
        body.insertBefore(hint,reviewBox);
      }
    } catch(error) {body.textContent='Could not load task images: '+error.message;panel.dataset.loaded='false';}
  });
  return panel;
}
let agentSteersReadiness={state:'checking',can_dispatch:false,message:'Checking AgentSteers mock configuration…'};
const pendingAgentDispatches=new Set();
let agentSteersVerifiedThisPage=false;
let agentSteersQueue=null;
let agentSteersQueueCheckedAt=null;
let agentSteersQueueError=null;
let syncInFlight=false;
let nextSyncOffset=0;
// Shared controls used in both the task list and expanded task details.
// Every dispatch requires a fresh user confirmation and server-side authorization.
function makeAgentSteersControls(task) {
  const agentBox=el('div',undefined,'task-agentsteers');
  const mockStatus=task.agentsteers_job_id
    ? 'AgentSteers simulation: '+task.agent_status+' · Job '+task.agentsteers_job_id
    : 'AgentSteers simulation: '+(task.agent_status || 'not dispatched');
  agentBox.append(el('p',mockStatus,'meta'));
  if(task.agent_status==='recovery_needed') {
    agentBox.append(el('p',
      'Recovery needed: the remote mock job cannot be found. Check the private task store and server identity before attempting any new dispatch.',
      'meta'));
  }
  if(task.agentsteers_result) {
    try {
      const result=JSON.parse(task.agentsteers_result);
      if(result.message)agentBox.append(el('p',result.message,'meta'));
      else if(result.result && result.result.message)agentBox.append(el('p',result.result.message,'meta'));
    } catch (_) { /* Invalid legacy result: omit display only. */ }
  }
  if(!task.agentsteers_job_id && task.agent_status!=='submitting' &&
     task.agent_status!=='dispatch_error' && task.status!=='done' &&
     agentSteersReadiness.can_dispatch && agentSteersVerifiedThisPage) {
    const dispatch=iconButton('check','Send to AgentSteers (mock only)');
    dispatch.addEventListener('click',async()=>{
      const prompt='Send reviewed task "'+task.title+'" to AgentSteers for a MOCK run?\n\nOnly the task title and description will be shared. No email, attachment, GitHub change, or shell command will be executed.';
      if(pendingAgentDispatches.has(task.id) || !window.confirm(prompt))return;
      pendingAgentDispatches.add(task.id);
      dispatch.disabled=true;
      try {
        await request('/api/tasks/'+task.id+'/agentsteers-dispatch','POST',
                      {revision:task.revision,confirm:'DISPATCH MOCK'});
        notify('Mock task submitted to AgentSteers. No code or email was executed.');
        await loadMockQueueSummary();
        await refreshTasks();
      } catch(error) { notify(error.message,true); dispatch.disabled=false; }
      finally { pendingAgentDispatches.delete(task.id); }
    });
    agentBox.append(dispatch);
  } else if(task.agentsteers_job_id) {
    const refresh=iconButton('refresh',task.agent_status==='recovery_needed'
      ? 'Recheck missing AgentSteers job' : 'Refresh AgentSteers mock status');
    refresh.addEventListener('click',async()=>{
      refresh.disabled=true;
      try {
        await request('/api/tasks/'+task.id+'/agentsteers-refresh','POST',{});
        notify('AgentSteers mock status refreshed.');
        await refreshTasks();
      } catch(error) { notify(error.message,true); refresh.disabled=false; }
    });
    agentBox.append(refresh);
    if(task.agent_status==='queued_mock' && agentSteersVerifiedThisPage) {
      const cancel=iconButton('xmark','Cancel AgentSteers mock task');
      cancel.addEventListener('click',async()=>{
        if(pendingAgentDispatches.has(task.id))return;
        if(!window.confirm('Cancel the queued AgentSteers MOCK task "'+task.title+'"? This does not cancel the original Inbox Tend task.'))return;
        pendingAgentDispatches.add(task.id);
        cancel.disabled=true;
        try {
          await request('/api/tasks/'+task.id+'/agentsteers-cancel','POST',
                        {revision:task.revision,confirm:'CANCEL MOCK'});
          notify('AgentSteers mock task cancellation confirmed.');
          await loadMockQueueSummary();
          await refreshTasks();
        } catch(error) {
          notify(error.message+' — refresh the AgentSteers status to reconcile.',true);
          cancel.disabled=false;
        } finally { pendingAgentDispatches.delete(task.id); }
      });
      agentBox.append(cancel);
    }
  } else if(task.agent_status==='dispatch_error') {
    agentBox.append(el('p','Dispatch status is uncertain. Check the AgentSteers service before submitting another task.','meta'));
  }
  return agentBox;
}

function makeEditForm(task) {
  const form=el('form',undefined,'task-form');
  const title=field(form,'Title','title',task.title); title.required=true; title.maxLength=200;
  field(form,'Description','description',task.description,'textarea').parentElement.classList.add('wide');
  field(form,'Owner','owner',task.owner || '');
  const priority=field(form,'Priority','priority',task.priority,'select');
  for(const value of ['low','medium','high']) {
    const option=el('option',value); option.value=value; priority.append(option);
  }
  priority.value=task.priority;
  field(form,'Due date (YYYY-MM-DD)','due_date',task.due_date || '');
  field(form,'Due time (HH:MM)','due_time',task.due_time || '');
  field(form,'Timezone (IANA)','due_timezone',task.due_timezone || userSettings.timezone);
  field(form,'Project','project_label',task.project_label || '');
  field(form,'Customer','customer_label',task.customer_label || '');
  const agent=field(form,'AI Agent (assignment only)','assigned_agent',task.assigned_agent || '','select');
  for(const [value,label] of [['','Unassigned'],['Research Agent','Research Agent'],['Coding Agent','Coding Agent'],['General Agent','General Agent']]){
    const option=el('option',label); option.value=value; agent.append(option);
  }
  agent.value=task.assigned_agent || '';
  form.append(el('p','Assigning an agent only records a pending approval. No agent is executed and no emails are shared.','meta'));

  const save=iconButton('check','Save changes'); save.type='submit'; save.className+=' action';
  form.append(save);
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    const values=Object.fromEntries(new FormData(form).entries());
    values.due_date=values.due_date.trim() || null;
    values.due_time=values.due_time.trim() || null;
    const changed=values.due_date!==task.due_date || values.due_time!==task.due_time;
    if(changed && Boolean(values.due_date)!==Boolean(values.due_time)){
      notify('Enter both due date and time, or leave both blank.',true); return;
    }
    values.revision=task.revision;
    if(!changed){delete values.due_date;delete values.due_time;}
    values.due_timezone=(values.due_timezone||'').trim();
    if(values.due_timezone===task.due_timezone)delete values.due_timezone;
    save.disabled=true;
    try{
      await request('/api/tasks/'+task.id,'PATCH',values);
      notify('Task changes saved locally.');
      await refreshTasks();
    }catch(error){notify(error.message+' — refresh and try again.',true);save.disabled=false;}
  });
  return form;
}
async function loadMockQueueSummary() {
  // A missing or failed read is never represented as a live zero count.
  agentSteersQueue=null;
  agentSteersQueueCheckedAt=null;
  agentSteersQueueError=null;
  if(!agentSteersVerifiedThisPage)return;
  try {
    const response=await request('/api/agentsteers/queue-summary','POST',{});
    agentSteersQueue=response.queue;
    agentSteersQueueCheckedAt=new Date();
  } catch(_) {
    agentSteersQueueError='Remote mock queue unavailable. Recheck the connection.';
  }
}

// Today in the user's timezone as a UTC-midnight number, so two dates can be subtracted into whole days.
function dayNumber(iso) { const [y, m, d] = iso.split('-').map(Number); return Date.UTC(y, m - 1, d) / 86400000; }
function todayNumber() {
  try { return dayNumber(new Date().toLocaleDateString('en-CA', {timeZone: userSettings.timezone})); }
  catch (_) { return dayNumber(new Date().toISOString().slice(0, 10)); }
}
// "Overdue by 3 days", "Due today", "Due tomorrow", "Due in 5 days": the number a person needs, not just a date.
function dueText(task) {
  if (!task.due_date) return 'No deadline';
  const days = dayNumber(task.due_date) - todayNumber();
  const clock = (task.due_time || '23:59') + ' ' + (task.due_timezone || '');
  if (task.status === 'done') return task.due_date;
  if (days < 0) return 'Overdue by ' + (-days) + ' day' + (days === -1 ? '' : 's') + ' · ' + task.due_date;
  if (days === 0) return 'Due today · ' + clock;
  if (days === 1) return 'Due tomorrow · ' + clock;
  return 'Due in ' + days + ' days · ' + task.due_date;
}
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
  const title = el('h3', task.title);
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
  const history = el('details');
  const historySummary = document.createElement('summary'); historySummary.append(icon('history'), document.createTextNode(' History'));
  history.append(historySummary);
  history.addEventListener('toggle', async () => {
    if (!history.open || history.dataset.loaded === 'true') return;
    const placeholder = el('p', 'Loading history…', 'meta'); history.append(placeholder);
    try {
      const result = await request('/api/tasks/' + task.id + '/events');
      const ordered = el('ol');
      const mockStates = {
        submitting: 'Mock dispatch requested', queued_mock: 'Mock task queued — not executed', cancelled: 'Mock cancellation confirmed',
        dispatch_error: 'Mock dispatch uncertain', recovery_needed: 'Mock job missing — recovery needed'
      };
      for (const event of result.events) {
        let changes = event.changes;
        if (typeof changes === 'string') { try { changes = JSON.parse(changes); } catch (_) { /* Display original */ } }
        const timestamp = new Date(event.created_at);
        const when = Number.isNaN(timestamp.getTime()) ? event.created_at : timestamp.toLocaleString();
        const isMock = event.action === 'agent_status_updated' && changes && typeof changes === 'object' && !Array.isArray(changes);
        const line = el('li', (isMock ? (mockStates[changes.status] || 'AgentSteers mock status updated') : event.action.replaceAll('_', ' ')) + ' · ' + when);
        if (isMock) {
          if (changes.task_id) line.append(el('div', 'Mock job ID: ' + changes.task_id, 'meta'));
          line.append(el('div', 'Simulation only; no real AI worker execution is implied.', 'meta'));
        } else if (changes !== null && changes !== undefined) {
          line.append(el('div', typeof changes === 'string' ? changes : JSON.stringify(changes), 'meta'));
        }
        ordered.append(line);
      }
      if (!result.events.length) ordered.append(el('li', 'No saved activity yet.'));
      placeholder.replaceWith(ordered); history.dataset.loaded = 'true';
    } catch (error) { placeholder.textContent = 'Could not load history: ' + error.message; }
  });
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
