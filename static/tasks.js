'use strict';
const taskList = document.getElementById('tasks');
const taskSummary = document.getElementById('task-summary');
const taskSearch = document.getElementById('task-search');
const taskFilter = document.getElementById('task-filter');
const taskRefresh = document.getElementById('task-refresh');
let cachedTasks = [];
let agentSteersReadiness={state:'checking',can_dispatch:false,message:'Checking AgentSteers mock configuration…'};
const pendingAgentDispatches=new Set();
let agentSteersVerifiedThisPage=false;
let agentSteersQueue=null;
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
function renderTasks() {
  taskList.replaceChildren();
  const readiness=el('p','AgentSteers mock: '+agentSteersReadiness.message,'meta');
  readiness.dataset.bridgeState=agentSteersReadiness.state;
  taskList.append(readiness);
  if(agentSteersVerifiedThisPage) {
    const queue=el('p',agentSteersQueue
      ? 'Remote mock queue · Total: '+agentSteersQueue.total+
        ' · Queued: '+agentSteersQueue.queued_mock+
        ' · Cancelled: '+agentSteersQueue.cancelled
      : 'Remote mock queue counts not loaded. Check the AgentSteers connection.',
      'meta');
    taskList.append(queue);
  }
  // Provide a setup explanation without exposing server credentials or endpoints.
  const setupHelp=el('p',undefined,'meta');
  setupHelp.append(document.createTextNode('Mock mode only · '));
  const setupLink=el('a','Connection setup and limitations');
  setupLink.href='https://github.com/scyllarusllc/inboxtend/blob/main/docs/agentsteers-handoff.md';
  setupLink.target='_blank';
  setupLink.rel='noopener noreferrer';
  setupHelp.append(setupLink);
  taskList.append(setupHelp);
  if(agentSteersReadiness.state==='configured_unverified' ||
     agentSteersReadiness.state==='connected_mock' ||
     agentSteersReadiness.state==='connection_failed') {
    const probe=iconButton('refresh','Check AgentSteers connection');
    probe.addEventListener('click',async()=>{
      probe.disabled=true;
      try {
        const data=await request('/api/agentsteers/probe','POST',{});
        agentSteersReadiness=data.bridge;
        agentSteersVerifiedThisPage=data.bridge.state==='connected_mock';
        agentSteersQueue=null;
        if(agentSteersVerifiedThisPage) {
          try {
            const summary=await request('/api/agentsteers/queue-summary','POST',{});
            agentSteersQueue=summary.queue;
          } catch(error) {
            notify('Connected, but mock queue summary is unavailable: '+error.message,true);
          }
        }
        notify(agentSteersReadiness.message,!agentSteersVerifiedThisPage);
        renderTasks();
      } catch(error) {
        agentSteersVerifiedThisPage=false;
        agentSteersQueue=null;
        agentSteersReadiness={state:'connection_failed',can_dispatch:false,
          message:'Connection check failed: '+error.message};
        notify(agentSteersReadiness.message,true);
        renderTasks();
      }
    });
    taskList.append(probe);
  }
  const term=taskSearch.value.trim().toLowerCase();
  const filter=taskFilter.value;
  const matching=cachedTasks.filter(task=>{
    const searchable=[task.title,task.description,task.owner,task.project_label,task.customer_label]
      .filter(Boolean).join(' ').toLowerCase();
    if(term && !searchable.includes(term))return false;
    if(filter==='active')return task.status!=='done';
    if(filter==='done')return task.status==='done';
    if(filter==='overdue' || filter==='due_soon')return task.deadline_state===filter;
    return true;
  });
  if(!matching.length){
    taskList.append(el('p',cachedTasks.length?'No tasks match the current filter.':'No saved tasks yet. Open an inbox message and suggest a task to create one.'));
    return;
  }
  for(const task of matching){
    const item=el('article',undefined,'task-item');
    item.dataset.deadline=task.deadline_state;
    const date=task.due_date?task.due_date+' '+(task.due_time || '23:59')+' ('+task.due_timezone+')':'No deadline';
    const head=el('div',undefined,'task-header');
    head.append(el('h3',task.title));
    if(task.title==='Review student registration form issue (mock handoff)' &&
       task.source_provider==='demo') {
      const sampleTag=el('span','Fictional demo task','task-pill');
      head.append(sampleTag);
    }
    const labels=el('div',undefined,'task-labels');
    const statusBadge=el('span',task.status.replaceAll('_',' '),'task-pill');
    if(task.status==='done')statusBadge.dataset.tone='complete';
    labels.append(statusBadge);
    if(task.deadline_state==='overdue' || task.deadline_state==='due_soon'){
      const dueBadge=el('span',task.deadline_state==='overdue'?'Overdue':'Due soon','task-pill');
      dueBadge.dataset.tone=task.deadline_state==='overdue'?'warning':'info';
      labels.append(dueBadge);
    }
    head.append(labels);
    const meta=el('div',undefined,'task-metadata');
    meta.append(el('span','Priority: '+task.priority),el('span','·'),el('span',date));
    if(task.owner){meta.append(el('span','·'),el('span','Owner: '+task.owner));}
    if(task.project_label){meta.append(el('span','·'),el('span','Project: '+task.project_label));}
    if(task.assigned_agent){meta.append(el('span','·'),el('span','Agent: '+task.assigned_agent+' ('+task.agent_status+')'));}
    item.append(head,meta,el('p',task.description));
    const label=el('label','Status: ');
    const select=document.createElement('select');
    for(const status of ['new','in_progress','waiting','done']){
      const option=el('option',status.replaceAll('_',' '));option.value=status;select.append(option);
    }
    select.value=task.status; label.append(select);
    const save=iconButton('check','Update status');
    save.disabled=true;
    select.addEventListener('change',()=>{save.disabled=select.value===task.status;});
    save.addEventListener('click',async()=>{
      save.disabled=true;
      try{
        await request('/api/tasks/'+task.id,'PATCH',{revision:task.revision,status:select.value});
        notify('Task status saved locally.');await refreshTasks();
      }catch(error){notify(error.message+' — refresh and retry.',true);save.disabled=false;}
    });
    const actions=el('div',undefined,'task-actions');actions.append(label,save);
    const editor=el('details');
    const editSummary=document.createElement('summary');editSummary.append(icon('pencil'),document.createTextNode(' Edit task'));
    editor.append(editSummary);
    editor.addEventListener('toggle',()=>{
      if(editor.open && editor.children.length===1) {
        editor.append(makeEditForm(task));
        const heading=el('h4','AgentSteers mock handoff');
        editor.append(heading,makeAgentSteersControls(task));
      }
    });
    const history=el('details');
    const historySummary=document.createElement('summary');historySummary.append(icon('history'),document.createTextNode(' Activity history'));
    history.append(historySummary);
    history.addEventListener('toggle',async()=>{
      if(!history.open || history.dataset.loaded==='true')return;
      const placeholder=el('p','Loading history…','meta');history.append(placeholder);
      try{
        const result=await request('/api/tasks/'+task.id+'/events');
        const ordered=el('ol');
        const mockStates={
          submitting:'Mock dispatch requested',
          queued_mock:'Mock task queued — not executed',
          cancelled:'Mock cancellation confirmed',
          dispatch_error:'Mock dispatch uncertain',
          recovery_needed:'Mock job missing — recovery needed'
        };
        for(const event of result.events){
          let changes=event.changes;
          if(typeof changes==='string'){
            try{changes=JSON.parse(changes);}catch(_){/* Display original */ }
          }
          const timestamp=new Date(event.created_at);
          const when=Number.isNaN(timestamp.getTime())?event.created_at:timestamp.toLocaleString();
          const isMock=event.action==='agent_status_updated' && changes &&
            typeof changes==='object' && !Array.isArray(changes);
          const title=isMock
            ? (mockStates[changes.status] || 'AgentSteers mock status updated')
            : event.action.replaceAll('_',' ');
          const line=el('li',title+' · '+when);
          if(isMock){
            if(changes.task_id)line.append(el('div','Mock job ID: '+changes.task_id,'meta'));
            line.append(el('div','Simulation only; no real AI worker execution is implied.','meta'));
          } else if(changes!==null && changes!==undefined) {
            line.append(el('div',typeof changes==='string'?changes:JSON.stringify(changes),'meta'));
          }
          ordered.append(line);
        }
        if(!result.events.length)ordered.append(el('li','No saved activity yet.'));
        placeholder.replaceWith(ordered);history.dataset.loaded='true';
      }catch(error){placeholder.textContent='Could not load history: '+error.message;}
    });
    actions.append(makeAgentSteersControls(task));
    item.append(actions,editor,history);
    taskList.append(item);
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
    }
    const summary=overview.summary;
    taskSummary.replaceChildren();
    for(const [label,count] of [
      ['Total',summary.total],['New',summary.by_status.new],
      ['In progress',summary.by_status.in_progress],['Waiting',summary.by_status.waiting],
      ['Done',summary.by_status.done],['Due soon',summary.due_soon],['Overdue',summary.overdue]
    ]){
      const card=el('div',undefined,'metric-card');
      if(label==='Overdue')card.dataset.tone='warning';
      if(label==='Due soon')card.dataset.tone='info';
      card.append(el('span',label,'metric-label'),el('strong',String(count),'metric-value'));
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
    try {
      const summary=await request('/api/agentsteers/queue-summary','POST',{});
      agentSteersQueue=summary.queue;
      renderTasks();
    } catch(_) {
      agentSteersQueue=null;
      renderTasks();
    }
  } catch(_) {
    // A dropped connection is not a completed task. Keep the last saved state;
    // manual refresh and the connection check remain available.
    agentSteersVerifiedThisPage=false;
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
  const hint=document.getElementById('tz-hint');
  if(hint)hint.textContent='New deadlines default to '+userSettings.timezone+' · due soon = within '+userSettings.due_soon_hours+' h';
  await refreshTasks();
  window.setInterval(syncOneMockJob,60000);
}
init();
