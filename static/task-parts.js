'use strict';
// Pieces shared by the task list (/user/tasks) and the single-task page (/user/task/<id>).
// Callers define refreshTasks(): the list reloads every task, the detail page reloads the one it shows.
// The server answers once, when the whole review is done, so this bar is an estimate: it fills toward ~95% over
// the time a review usually takes (about 12 s per image), then jumps to 100% when the answer arrives.
function reviewProgress(count) {
  const node = el('div', undefined, 'review-progress');
  const track = el('div', undefined, 'progress-track');
  track.setAttribute('role', 'progressbar');
  track.setAttribute('aria-label', 'Image review progress (estimated)');
  track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', '100');
  const fill = el('div', undefined, 'progress-fill');
  track.append(fill);
  const text = el('p', undefined, 'meta');
  node.append(track, text);
  const started = Date.now();
  const expected = Math.max(15, count * 12);
  const paint = percent => {
    fill.style.width = percent + '%';
    track.setAttribute('aria-valuenow', String(Math.round(percent)));
  };
  const tick = () => {
    const seconds = (Date.now() - started) / 1000;
    paint(95 * (1 - Math.exp(-seconds / (expected / 2))));
    const stage = seconds < 4 ? 'Sending ' + count + ' image' + (count === 1 ? '' : 's') + ' to the AI…'
      : seconds < expected ? 'Reading the images and translating the text…' : 'Still working, large images take longer…';
    text.textContent = stage + ' ' + Math.floor(seconds) + 's · estimate, about ' + expected + 's in total';
  };
  tick();
  const timer = window.setInterval(tick, 500);
  return {node, finish(ok) {
    window.clearInterval(timer);
    if (ok) { paint(100); text.textContent = 'Done in ' + Math.round((Date.now() - started) / 1000) + 's.'; window.setTimeout(() => node.remove(), 1500); }
    else node.remove();
  }};
}
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
            row.append(turnableImage(file,base+'/thumbnail',base+'/rotate'));
          }
        } else row.append(el('p','Image was not stored: '+(file.skipped_reason || 'unavailable'),'meta'));
        body.append(row);
      }
      const reviewBox=el('div',undefined,'task-image-review');
      let saved=result.review;   // The saved answer lives in the database: it is shown on every visit, no new AI run needed.
      const count=result.images.length;
      const runLabel=()=>saved?'Translate again (replaces the saved result)':'Translate and explain all '+count+' images in Chinese';
      const run=iconButton('language',runLabel());
      body.prepend(reviewBox);body.prepend(run);
      const showReview=review=>{
        reviewBox.replaceChildren();
        if(!review)return;
        const section=el('section',undefined,'image-review');
        section.append(el('h4','Chinese translation and image interpretation'),
          el('p','Saved '+(review.created_at?new Date(review.created_at).toLocaleString():'')+' · '+review.provider+' · '+review.model+' · AI guesses may be wrong; check the images.','meta'));
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
      showReview(saved);
      run.addEventListener('click',async()=>{
        run.disabled=true;
        const progress=reviewProgress(count);
        run.after(progress.node);
        run.replaceChildren(icon('language'),document.createTextNode(' Translating and explaining images…'));
        try {
          const answer=await request('/api/tasks/'+task.id+'/images/review','POST',{refresh:true});
          progress.finish(true);
          saved=answer.review;showReview(saved);
          notify('Image review saved with this task.');
        } catch(error) {progress.finish(false);notify(error.message,true);}
        finally {
          run.replaceChildren(icon('language'),document.createTextNode(runLabel()));
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

function taskHistoryDetails(task) {
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
  return history;
}
