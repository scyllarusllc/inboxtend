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
  refresh = refresh || Boolean(lastPlan);
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
  box.append(head, el('p', 'Saved ' + new Date(plan.created_at).toLocaleString() + ' · ' + plan.provider + ' · ' + plan.model +
    (plan.project_used ? ' · Read task project (' + plan.project_files + ' text files, ' + plan.project_images + ' images)' : '') +
    ' · A guess, not a fact: check it against the source files.' +
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

const anchorMessageId = task => task.anchor_message_id || task.message_id;

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
  for (const [name, value] of [['Priority', task.priority + ' priority'], ['Source', ({people:'People',threads:'Conversation',messages:'Message'})[task.source_scope] || 'Message'], ['Owner', task.owner], ['Project', task.project_label], ['Customer', task.customer_label],
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
    const wait = big('clock', 'Waiting');
    wait.setAttribute('aria-pressed', String(task.status === 'waiting'));
    wait.addEventListener('click', () => { wait.disabled = true; changeStatus(task, task.status === 'waiting' ? 'new' : 'waiting'); });
    actions.append(wait);
  }
  if (task.source_provider !== 'demo' && anchorMessageId(task)) {
    const open = el('a', undefined, 'big-action');
    open.href = '/user/mail/' + encodeURIComponent(anchorMessageId(task));
    open.append(icon('mail'), document.createTextNode(' Open the email'));
    actions.append(open);
  }
  const edit = big('pencil', 'Edit');
  edit.addEventListener('click', () => { const f = document.getElementById('task-edit'); f.open = true; f.scrollIntoView({behavior: 'smooth', block: 'start'}); f.querySelector('input').focus({preventScroll: true}); });
  actions.append(edit);
  card.append(actions);
  card.append(projectCard(task));
  detailBox.append(card);
  if (lastPlan) detailBox.append(planPanel(lastPlan, task));   // Full width: English plan on the left, its Chinese translation on the right.
  {
    const pair = el('div', undefined, 'plan-pair');
    const reviewBox = el('div', undefined, 'task-image-review');
    const files = taskProjectFiles(task);
    if (task.image_count) {
      const images = taskImageDetails(task, reviewBox);
      files.append(images);
      pair.append(section('Files', files), section('Extracted text and AI explanation', reviewBox));
    } else pair.append(section('Files', files));
    detailBox.append(pair);
  }
  // Two columns on a wide screen: the work (plan, images, edit) on the left, context (email, history) on the right.
  const columns = el('div', undefined, 'task-columns');
  const mainCol = el('div', undefined, 'task-main');
  const side = el('aside', undefined, 'task-side');
  columns.append(mainCol, side);
  detailBox.append(columns);

  if (task.message_id) mainCol.append(correspondenceCard(task));
  
  const editBox = el('details', undefined, 'panel task-section task-edit');
  editBox.id = 'task-edit';
  const editSummary = el('summary', 'Edit task');
  editBox.append(editSummary, makeEditForm(task));
  mainCol.append(editBox);
  if (task.agentsteers_job_id || task.assigned_agent || task.agent_status === 'dispatch_error') {
    mainCol.append(section('AgentSteers mock handoff', makeAgentSteersControls(task)));
  }
  if (task.source_provider !== 'demo' && anchorMessageId(task)) side.append(emailCard(task));
  const history = taskHistoryDetails(task);
  history.open = true;
  const stamps = el('p', 'Created ' + (task.created_at ? new Date(task.created_at).toLocaleString() : '—') + ' · Updated ' + (task.updated_at ? new Date(task.updated_at).toLocaleString() : '—'), 'meta');
  side.append(section('History', history, stamps));
}

function taskProjectFiles(task) {
  const box = el('div', 'Loading files from .tasks/' + task.id + '/attachments…', 'project-previews');
  box.id = 'task-files';
  const load = async () => {
    try {
      let data;
      try { data = await request('/api/tasks/' + task.id + '/project/attachments'); }
      catch (error) {
        if (!String(error.message).includes('Create the task project first')) throw error;
        await request('/api/tasks/' + task.id + '/project', 'POST', {});
        data = await request('/api/tasks/' + task.id + '/project/attachments');
      }
      box.replaceChildren(el('p', 'Files in .tasks/' + task.id + '/attachments · ' + data.attachments.length + ' total', 'meta'));
      if (!data.attachments.length) box.append(el('p', 'No attachments in this task project.', 'meta'));
      for (const file of data.attachments) {
        const row = el('div', undefined, 'attachment-row project-attachment');
        row.append(el('strong', file.name || 'Unnamed file'),
          el('span', (file.size_bytes / 1048576).toFixed(1) + ' MB · ' + (file.mime_type || 'File'), 'meta'));
        if (file.kind === 'missing') row.append(el('p', 'Not stored locally: ' + (file.skipped_reason || 'unavailable'), 'meta'));
        else {
          const base = '/api/tasks/' + task.id + '/project/attachments/' + file.id;
          const link = el('a', 'Download', 'icon-button');
          link.href = base + '/download'; link.setAttribute('download', file.name); row.append(link);
          if (file.kind === 'image') {
            const img = el('img', undefined, 'attachment-image');
            img.src = base + '/preview'; img.alt = 'Preview of ' + file.name; img.loading = 'lazy'; row.append(img);
          } else if (file.kind === 'text') {
            const preview = el('pre', 'Loading preview…', 'attachment-preview'); row.append(preview);
            request(base + '/preview').then(result => { preview.textContent = result.content + (result.truncated ? '\n…' : ''); })
              .catch(error => { preview.textContent = 'Preview unavailable: ' + error.message; });
          }
        }
        box.append(row);
      }
    } catch (error) { box.textContent = 'Could not load task files: ' + error.message; }
  };
  load();
  return box;
}

function projectCard(task) {
  const box = el('section', undefined, 'task-project');
  box.append(el('h2', 'Task project'));
  box.append(el('p', 'The AI plan reads .tasks/' + task.id + ' on the server. This folder includes the task, saved email history, and locally stored attachments. Add notes there before updating the plan.', 'meta'));
  const actions = el('div', undefined, 'project-actions');
  if (task.status !== 'done' || lastPlan) {
    const work = iconButton('sparkles', lastPlan ? 'Show the AI plan' : 'Start working with AI');
    work.className = 'big-action ai';
    work.title = 'Read the task project folder and prepare an updated AI plan.';
    work.addEventListener('click', () => startWorking(task, work));
    actions.append(work);
  }
  const create = iconButton('folder', 'Create as project');
  const download = iconButton('download', 'Download all attachments');
  const preview = iconButton('eye', 'Preview all attachments');
  preview.setAttribute('aria-expanded', 'false');
  const status = el('p', '', 'meta');
  const previews = el('div', undefined, 'project-previews');
  previews.hidden = true;
  const exportNow = async button => {
    create.disabled = download.disabled = preview.disabled = true;
    status.textContent = 'Preparing task project and attachments…';
    try {
      const result = (await request('/api/tasks/' + task.id + '/project', 'POST', {})).project;
      status.textContent = 'Saved to ' + result.path + ' · ' + result.emails + ' emails · ' + result.attachments + ' attachments' +
        (result.unavailable ? ' · ' + result.unavailable + ' unavailable in the local cache' : '') + '.';
      if (button === download) location.assign('/api/tasks/' + task.id + '/attachments.zip');
      if (button === preview) {
        const data = await request('/api/tasks/' + task.id + '/project/attachments');
        previews.replaceChildren();
        previews.append(el('h3', 'Attachments (' + data.attachments.length + ')'));
        if (!data.attachments.length) previews.append(el('p', 'No saved attachments for this task.', 'meta'));
        for (const file of data.attachments) {
          const row = el('div', undefined, 'attachment-row project-attachment');
          const heading = el('div', undefined, 'attachment-meta');
          heading.append(el('strong', file.name || 'Unnamed attachment'),
            el('span', (file.size_bytes / 1048576).toFixed(1) + ' MB · ' + (file.mime_type || 'File'), 'meta'));
          row.append(heading);
          if (file.kind === 'missing') row.append(el('p', 'Not stored locally: ' + (file.skipped_reason || 'unavailable'), 'meta'));
          else {
            const base = '/api/tasks/' + task.id + '/project/attachments/' + file.id;
            const link = el('a', 'Download', 'icon-button');
            link.href = base + '/download';
            link.setAttribute('download', file.name);
            row.append(link);
            if (file.kind === 'image') {
              const img = el('img', undefined, 'attachment-image');
              img.src = base + '/preview';
              img.alt = 'Preview of ' + file.name;
              img.loading = 'lazy';
              row.append(img);
            } else if (file.kind === 'text') {
              const text = el('pre', 'Loading text preview…', 'attachment-preview');
              row.append(text);
              request(base + '/preview').then(data => { text.textContent = data.content + (data.truncated ? '\n…' : ''); })
                .catch(error => { text.textContent = 'Preview unavailable: ' + error.message; });
            }
          }
          previews.append(row);
        }
        previews.hidden = false;
        preview.setAttribute('aria-expanded', 'true');
        preview.replaceChildren(icon('eye'), document.createTextNode(' Hide attachment previews'));
      }
    } catch (error) { status.textContent = error.message; notify(error.message, true); }
    finally { create.disabled = download.disabled = preview.disabled = false; }
  };
  create.addEventListener('click', () => exportNow(create));
  download.addEventListener('click', () => exportNow(download));
  preview.addEventListener('click', () => {
    if (!previews.hidden) {
      previews.hidden = true;
      preview.setAttribute('aria-expanded', 'false');
      preview.replaceChildren(icon('eye'), document.createTextNode(' Preview all attachments'));
    } else exportNow(preview);
  });
  actions.append(create, download, preview);
  box.append(actions, status, previews);
  if (lastPlan) {
    const ready = el('div', undefined, 'review-progress ai-work-progress is-complete');
    const track = el('div', undefined, 'progress-track');
    track.setAttribute('role', 'progressbar');
    track.setAttribute('aria-label', 'AI plan progress');
    track.setAttribute('aria-valuemin', '0');
    track.setAttribute('aria-valuemax', '100');
    track.setAttribute('aria-valuenow', '100');
    track.append(el('div', undefined, 'progress-fill'));
    ready.append(track, el('p', 'AI plan ready · saved ' + new Date(lastPlan.created_at).toLocaleString(), 'meta'));
    box.append(ready);
  }
  box.append(websiteSkillCard(task));
  return box;
}

function websiteSkillCard(task) {
  const panel = el('details', undefined, 'website-skill');
  panel.append(el('summary', 'Create a website project (GitHub)'));
  panel.append(el('p', 'Uses the website project skill and this task’s files. First builds a local project; publish it as a private GitHub repository only after reviewing the result.', 'meta'));
  const label = el('label', 'Repository name');
  const slug = el('input');
  slug.type = 'text';
  slug.autocomplete = 'off';
  slug.pattern = '[a-z][a-z0-9-]{2,62}';
  slug.value = ('site-' + task.id + '-' + task.title.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/-+$/, '')).slice(0, 63).replace(/-+$/, '');
  label.append(slug);
  const build = iconButton('sparkles', 'Build website locally');
  const status = el('p', '', 'meta');
  const review = el('div', undefined, 'website-review');
  const display = data => {
    const job = data.job;
    review.replaceChildren();
    if (!job) { status.textContent = 'No website project started.'; return; }
    status.textContent = 'Website project: ' + job.state + ' · ' + job.path + (job.error ? ' · ' + job.error : '');
    build.disabled = job.state === 'running' || job.state === 'publishing';
    if (data.log) review.append(el('pre', data.log, 'attachment-preview'));
    if (job.state === 'ready' || job.state === 'published') {
      review.append(el('h3', 'Local project preview'));
      review.append(el('pre', data.readme || 'README unavailable; inspect the project folder on the server.', 'attachment-preview'));
      if (data.files?.length) review.append(el('p', 'Files: ' + data.files.join(' · '), 'meta'));
      if (job.state === 'ready') {
        const approval = el('label', undefined, 'website-publish-check');
        const check = el('input'); check.type = 'checkbox';
        approval.append(check, document.createTextNode(' I reviewed the local project and want to publish a private GitHub repository.'));
        const publish = iconButton('upload', 'Publish private GitHub repository');
        publish.disabled = true;
        check.addEventListener('change', () => { publish.disabled = !check.checked; });
        publish.addEventListener('click', async () => {
          publish.disabled = true;
          status.textContent = 'Publishing private GitHub repository…';
          try { await request('/api/tasks/' + task.id + '/website-project/publish', 'POST', {confirm: 'PUBLISH_PRIVATE'}); await refresh(); }
          catch (error) { notify(error.message, true); await refresh(); }
        });
        review.append(approval, publish);
      } else if (job.remote_url) {
        const link = el('a', job.remote_url); link.href = job.remote_url; link.target = '_blank'; link.rel = 'noopener noreferrer'; review.append(link);
      }
    }
  };
  const refresh = async () => {
    if (!panel.isConnected) return;
    try { display(await request('/api/tasks/' + task.id + '/website-project')); }
    catch (error) { status.textContent = error.message; }
  };
  build.addEventListener('click', async () => {
    if (!slug.checkValidity()) { slug.reportValidity(); return; }
    build.disabled = true;
    status.textContent = 'Starting website build…';
    try { await request('/api/tasks/' + task.id + '/website-project', 'POST', {slug: slug.value}); await refresh(); }
    catch (error) { status.textContent = error.message; build.disabled = false; notify(error.message, true); }
  });
  panel.addEventListener('toggle', () => { if (panel.open) refresh(); });
  const timer = window.setInterval(() => {
    if (!panel.isConnected) { window.clearInterval(timer); return; }
    if (panel.open && status.textContent.includes('running')) refresh();
  }, 3000);
  panel.append(label, build, status, review);
  return panel;
}

function correspondenceCard(task) {
  const box = section(({people:'Emails with this person',threads:'Emails in this conversation',messages:'Source message'})[task.source_scope] || 'Source emails');
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
      summary.textContent = result.total ? result.total + ' saved email' + (result.total === 1 ? '' : 's') + (result.address ? ' with ' + result.address : '') : 'No saved source emails for this task.';
      for (const mail of result.messages) {
        const item = el('li', undefined, 'correspondence-item');
        const link = el('a', mail.subject || '(No subject)');
        link.href = '/user/mail/' + encodeURIComponent(mail.id);
        if (mail.is_source) link.append(el('span', ' · task source', 'meta'));
        item.append(link, el('span', (mail.direction === 'sent' ? 'Sent' : 'Received') + ' · ' + formatWhen(mail.internal_date, mail.date || ''), 'meta'));
        if (mail.snippet) item.append(el('p', mail.snippet, 'email-excerpt'));
        if (mail.body_text) {
          const full = el('details');
          full.append(el('summary', 'Read saved content'), el('pre', mail.body_text, 'message-body'));
          item.append(full);
        }
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
  const messageId = anchorMessageId(task);
  const box = section('From this email');
  const fill = m => {
    box.append(el('strong', m.subject || '(No subject)'), el('p', 'From ' + (m.from || 'unknown') + ' · ' + formatWhen(m.internal_date, m.date || ''), 'meta'));
    const text = (m.body_text || m.snippet || '').replace(/\s+/g, ' ').trim();
    if (text) box.append(el('p', text.length > 360 ? text.slice(0, 360) + '…' : text, 'email-excerpt'));
    const more = el('a', 'Read the full email'); more.href = '/user/mail/' + encodeURIComponent(messageId); box.append(more);
  };
  if (emailContext && emailContext.id === messageId) { fill(emailContext); return box; }
  box.hidden = true;   // Shown only once the email is loaded.
  request('/api/connectors/gmail/messages/' + encodeURIComponent(messageId))
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
