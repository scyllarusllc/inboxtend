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
const taskSearch = document.getElementById('task-search');
const taskFilter = document.getElementById('task-filter');
const taskRefresh = document.getElementById('task-refresh');
const taskNotice = document.getElementById('task-notice');
let cachedTasks = [];
// Lucide icon paths (ISC; Feather-derived icons also MIT). See THIRD_PARTY_LICENSES.md.
// Local SVGs; no CDN or runtime dependencies.
const paths = {
  search:[['circle',{cx:11,cy:11,r:8}],['path',{d:'m21 21-4.35-4.35'}]],
  refresh:[['path',{d:'M20 11a8 8 0 0 0-14.6-4.5L3 9'}],['path',{d:'M3 4v5h5'}],['path',{d:'M4 13a8 8 0 0 0 14.6 4.5L21 15'}],['path',{d:'M16 15h5v5'}]],
  pencil:[['path',{d:'M12 20h9'}],['path',{d:'M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z'}]],
  history:[['path',{d:'M3 12a9 9 0 1 0 3-6.7L3 8'}],['path',{d:'M3 3v5h5'}],['path',{d:'M12 7v5l4 2'}]],
  check:[['path',{d:'M20 6 9 17l-5-5'}]],
  plus:[['path',{d:'M12 5v14'}],['path',{d:'M5 12h14'}]],
  mail:[['rect',{x:2,y:4,width:20,height:16,rx:2}],['path',{d:'m22 7-10 6L2 7'}]]
};
function icon(name) {
  const ns='http://www.w3.org/2000/svg';
  const svg=document.createElementNS(ns,'svg');
  svg.setAttribute('viewBox','0 0 24 24');
  svg.setAttribute('class','ultra-icon');
  svg.setAttribute('aria-hidden','true');
  for (const [tag,attrs] of paths[name] || []) {
    const node=document.createElementNS(ns,tag);
    for (const [key,value] of Object.entries(attrs)) node.setAttribute(key,String(value));
    svg.append(node);
  }
  return svg;
}
function iconButton(name,label) {
  const button=el('button',undefined,'icon-button');
  button.type='button';
  button.append(icon(name),document.createTextNode(label));
  return button;
}
function notify(message,error=false) {
  taskNotice.textContent=message;
  taskNotice.className=error?'error':'meta';
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
  field(form,'Project','project_label',task.project_label || '');
  field(form,'Customer','customer_label',task.customer_label || '');
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
    taskList.append(el('p',cachedTasks.length?'No tasks match the current filter.':'No saved tasks yet. Review a fictional message to create one.'));
    return;
  }
  for(const task of matching){
    const item=el('article',undefined,'task-item');
    item.dataset.deadline=task.deadline_state;
    const date=task.due_date?task.due_date+' '+(task.due_time || '23:59')+' ('+task.due_timezone+')':'No deadline';
    const head=el('div',undefined,'task-header');
    head.append(el('h3',task.title));
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
      if(editor.open && editor.children.length===1)editor.append(makeEditForm(task));
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
        for(const event of result.events){
          let changes=event.changes;
          if(typeof changes==='string'){
            try{changes=JSON.parse(changes);}catch(_){/* Display original */ }
          }
          const timestamp=new Date(event.created_at);
          const when=Number.isNaN(timestamp.getTime())?event.created_at:timestamp.toLocaleString();
          const line=el('li',event.action+' · '+when);
          line.append(el('div',typeof changes==='string'?changes:JSON.stringify(changes),'meta'));
          ordered.append(line);
        }
        placeholder.replaceWith(ordered);history.dataset.loaded='true';
      }catch(error){placeholder.textContent='Could not load history: '+error.message;}
    });
    item.append(actions,editor,history);
    taskList.append(item);
  }
}
async function refreshTasks() {
  try {
    const [overview,rows]=await Promise.all([request('/api/tasks/summary'),request('/api/tasks')]);
    cachedTasks=rows.tasks;
    const summary=overview.summary;
    taskSummary.replaceChildren();
    for(const [label,count] of [
      ['Total',summary.total],['New',summary.by_status.new],
      ['In progress',summary.by_status.in_progress],['Waiting',summary.by_status.waiting],
      ['Done',summary.by_status.done],['Due soon',summary.due_soon],['Overdue',summary.overdue]
    ])taskSummary.append(el('span',label+': '+count,'task-count'));
    renderTasks();
  }catch(error){notify('Could not load tasks: '+error.message,true);}
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
  const reply = iconButton('mail','Prepare an unsent reply draft');
  reply.className += ' action';
  reply.addEventListener('click', async () => {
    reply.disabled = true;
    try {
      const response = await request('/api/draft', 'POST', {id});
      if (selectedId === id) draft.replaceChildren(el('h4', 'Unsent draft'), el('pre', response.draft));
    } catch (error) {
      if (selectedId === id) draft.replaceChildren(el('p', error.message, 'error'));
    } finally { reply.disabled = false; }
  });
  const propose = iconButton('plus','Suggest task');
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
  taskSearch.addEventListener('input',renderTasks);
  taskFilter.addEventListener('change',renderTasks);
  taskRefresh.prepend(icon('refresh'));
  taskRefresh.classList.add('icon-button');
  taskRefresh.addEventListener('click',async()=>{notify('');await refreshTasks();});
  await refreshTasks();
}
init();
