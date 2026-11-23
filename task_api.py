"""Native FastAPI read routes for the task workspace."""
from __future__ import annotations

import json
import secrets
import subprocess
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import func
from sqlmodel import select

from auth import cookie_token
from db_models import Task, TaskAttachment, TaskImageReview, session as model_session
from tasks import DEFAULT_DB, connection, decorate, _soon, events, task_work_plan
from tasks import task_image_texts, save_task_image_texts
from app import raster_preview_mime
import ai
import settings as prefs
from auth import read_config
from app import ROOT
from gmail_web import _row as mail_row
from task_project import export_task, project_path
import website_jobs


router = APIRouter(prefix='/api/tasks')


def current_user(request: Request):
    host = request.headers.get('host', '')
    name = host.split(':', 1)[0].lower()
    if name not in ('127.0.0.1', 'localhost', '[::1]') or host.count(':') > 1:
        raise HTTPException(403, 'Invalid Host')
    manager = request.app.state.auth_manager
    user = manager.session(cookie_token(request.headers.get('cookie'))) if manager else None
    if not user:
        raise HTTPException(401, 'Sign in required')
    return user


def json_result(data):
    return JSONResponse(data, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer',
        'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})


def task_rows(user_id, path=DEFAULT_DB):
    with connection(path) as db:
        soon = _soon(db, user_id)
    with model_session(path) as db:
        rows = db.exec(select(Task, func.count(TaskAttachment.id))
            .outerjoin(TaskAttachment, TaskAttachment.task_id == Task.id)
            .where(Task.user_id == user_id).group_by(Task.id)
            .order_by(Task.status == 'done', Task.due_date.is_(None), Task.due_date, Task.due_time, Task.id)).all()
    return [decorate(dict(task.model_dump(), image_count=count), due_soon_hours=soon) for task, count in rows]


@router.get('/summary')
def summary(request: Request, user=Depends(current_user)):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    rows = task_rows(user['id'], path)
    by_status = {name: sum(task['status'] == name for task in rows)
                 for name in ('new', 'in_progress', 'waiting', 'done')}
    due_soon = sum(task['deadline_state'] == 'due_soon' for task in rows)
    overdue = sum(task['deadline_state'] == 'overdue' for task in rows)
    with connection(path) as db:
        from settings import effective
        zone = effective(db, user['id'])['timezone']
    return json_result({'summary': {'total': len(rows), 'by_status': by_status,
                                    'due_soon': due_soon, 'overdue': overdue}, 'timezone': zone})


@router.get('')
def list_saved_tasks(request: Request, user=Depends(current_user)):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with connection(path) as db:
        from settings import effective
        zone = effective(db, user['id'])['timezone']
    return json_result({'tasks': task_rows(user['id'], path), 'timezone': zone})


@router.get('/{task_id:int}')
def task_detail(request: Request, task_id: int, user=Depends(current_user)):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with model_session(path) as db:
        task = db.exec(select(Task).where(Task.id == task_id, Task.user_id == user['id'])).first()
        if task is None: raise HTTPException(404, 'Task not found')
        count = db.exec(select(func.count(TaskAttachment.id)).where(TaskAttachment.task_id == task_id)).one()
    with connection(path) as db: soon = _soon(db, user['id'])
    return json_result({'task': decorate(dict(task.model_dump(), image_count=count), due_soon_hours=soon)})


@router.get('/{task_id:int}/events')
def task_events(request: Request, task_id: int, user=Depends(current_user)):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with model_session(path) as db:
        exists = db.exec(select(Task.id).where(Task.id == task_id, Task.user_id == user['id'])).first()
    if exists is None: raise HTTPException(404, 'Task not found')
    with connection(path) as db: history = events(db, task_id, user['id'])
    return json_result({'events': history})


@router.get('/{task_id:int}/correspondence')
def task_correspondence(request: Request, task_id: int, offset: int = 0, user=Depends(current_user)):
    """Locally cached mail with the source message's correspondent."""
    if offset < 0: raise HTTPException(400, 'Invalid offset')
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with model_session(path) as db:
        task = db.exec(select(Task).where(Task.id == task_id, Task.user_id == user['id'])).first()
        if task is None: raise HTTPException(404, 'Task not found')
        message_id = task.anchor_message_id or task.message_id
        scope = task.source_scope
    if not message_id:
        return json_result({'messages': [], 'total': 0, 'address': '', 'scope': scope, 'next_offset': None})
    with connection(path) as db:
        snapshot_count = db.execute('SELECT COUNT(*) FROM task_source_messages WHERE task_id=?',(task_id,)).fetchone()[0]
        if snapshot_count:
            rows = db.execute('SELECT * FROM task_source_messages WHERE task_id=? ORDER BY internal_date DESC,message_id DESC LIMIT 50 OFFSET ?',
                              (task_id,offset)).fetchall()
            messages = [{'id':row['message_id'],'from':row['from_addr'],'to':row['to_addr'],
                         'subject':row['subject'],'date':row['date_header'],'internal_date':row['internal_date'],
                         'snippet':row['snippet'],'body_text':row['body_text'],
                         'direction':'sent' if '"SENT"' in row['label_ids'] else 'received',
                         'is_source':row['message_id']==message_id} for row in rows]
            next_offset = offset + len(messages) if offset + len(messages) < snapshot_count else None
            address = task.scope_key.split(':',1)[1] if scope=='people' and ':' in task.scope_key and not task.scope_key.startswith('single:') else ''
            return json_result({'messages':messages,'total':snapshot_count,'address':address,
                                'scope':scope,'next_offset':next_offset})
        source = db.execute('SELECT peer_email FROM gmail_messages WHERE user_id=? AND message_id=?',
                            (user['id'], message_id)).fetchone()
        if source is None or not source['peer_email']:
            return json_result({'messages': [], 'total': 0, 'address': '', 'scope': scope, 'next_offset': None})
        address = source['peer_email']
        params = (user['id'], address)
        where = 'user_id=? AND peer_email=?'
        total = db.execute('SELECT COUNT(*) FROM gmail_messages WHERE ' + where, params).fetchone()[0]
        rows = db.execute('SELECT * FROM gmail_messages WHERE ' + where +
                          ' ORDER BY internal_date DESC,message_id DESC LIMIT 50 OFFSET ?', (*params, offset)).fetchall()
    messages = [dict(mail_row(row), is_source=row['message_id'] == message_id) for row in rows]
    next_offset = offset + len(messages) if offset + len(messages) < total else None
    return json_result({'messages': messages, 'total': total, 'address': address, 'scope': scope, 'next_offset': next_offset})


@router.post('/{task_id:int}/project')
def create_project(request: Request, task_id: int, user=Depends(current_user)):
    _require_write(request, user)
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    try: result = export_task(path, user['id'], task_id)
    except LookupError: raise HTTPException(404, 'Task not found') from None
    except OSError: raise HTTPException(503, 'Could not create the task project directory') from None
    return json_result({'project': result})


def _require_write(request: Request, user):
    origin = request.headers.get('origin')
    host = request.headers.get('host', '')
    allowed = {f'http://{host}'}
    config = getattr(request.app.state, 'gmail_config', None) or read_config(ROOT / '.env')
    redirect = config.get('INBOX_TEND_GOOGLE_REDIRECT_URI', '')
    public = urlparse(redirect)
    if public.scheme == 'https' and public.netloc:
        allowed.add('https://' + public.netloc)
    if origin and origin not in allowed:
        raise HTTPException(403, 'Cross-origin write blocked')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Cross-site write blocked')
    if not secrets.compare_digest(request.headers.get('x-csrf-token', ''), user['csrf_token']):
        raise HTTPException(403, 'CSRF token required')


def _task_exists(request: Request, task_id: int, user_id: int):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with model_session(path) as db:
        exists = db.exec(select(Task.id).where(Task.id == task_id, Task.user_id == user_id)).first()
    if exists is None: raise HTTPException(404, 'Task not found')
    return path


@router.post('/{task_id:int}/website-project')
def start_website_project(request: Request, task_id: int, body: dict, user=Depends(current_user)):
    _require_write(request, user)
    path = _task_exists(request, task_id, user['id'])
    try: job = website_jobs.start(path, user['id'], task_id, body.get('slug'))
    except ValueError as exc: raise HTTPException(400, str(exc)) from None
    except RuntimeError as exc: raise HTTPException(503, str(exc)) from None
    except OSError: raise HTTPException(503, 'Could not prepare website project') from None
    return json_result({'job': job})


@router.get('/{task_id:int}/website-project')
def website_project_status(request: Request, task_id: int, user=Depends(current_user)):
    _task_exists(request, task_id, user['id'])
    job = website_jobs.status(task_id)
    if not job: return json_result({'job': None})
    preview = ''
    files = []
    log = ''
    if job.get('state') in ('running', 'failed', 'interrupted'):
        try: log = (project_path(task_id) / 'website-build.log').read_text(encoding='utf-8', errors='replace')[-3000:]
        except OSError: pass
    if job.get('state') in ('ready', 'published'):
        try:
            target = Path(job['path'])
            preview = (target / 'README.md').read_text(encoding='utf-8')[:8000]
            files = [p.relative_to(target).as_posix() for p in target.rglob('*')
                     if p.is_file() and '.git' not in p.relative_to(target).parts][:100]
        except OSError: pass
    return json_result({'job': job, 'readme': preview, 'files': files, 'log': log})


@router.post('/{task_id:int}/website-project/publish')
def publish_website_project(request: Request, task_id: int, body: dict, user=Depends(current_user)):
    _require_write(request, user)
    _task_exists(request, task_id, user['id'])
    if body.get('confirm') != 'PUBLISH_PRIVATE': raise HTTPException(400, 'Confirm private GitHub publishing')
    try: job = website_jobs.publish(task_id)
    except ValueError as exc: raise HTTPException(400, str(exc)) from None
    except RuntimeError as exc: raise HTTPException(503, str(exc)) from None
    except (OSError, subprocess.TimeoutExpired): raise HTTPException(503, 'GitHub publishing failed') from None
    return json_result({'job': job})


@router.get('/{task_id:int}/attachments.zip')
def download_all_attachments(request: Request, task_id: int, user=Depends(current_user)):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with model_session(path) as db:
        exists = db.exec(select(Task.id).where(Task.id == task_id, Task.user_id == user['id'])).first()
    if exists is None: raise HTTPException(404, 'Task not found')
    archive = project_path(task_id) / 'attachments.zip'
    if not archive.is_file(): raise HTTPException(404, 'Create the task project first')
    return FileResponse(archive, media_type='application/zip', filename=f'task-{task_id}-attachments.zip',
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


def _project_attachment(request: Request, task_id: int, user_id: int, attachment_id: int | None = None):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with model_session(path) as db:
        exists = db.exec(select(Task.id).where(Task.id == task_id, Task.user_id == user_id)).first()
    if exists is None: raise HTTPException(404, 'Task not found')
    folder = project_path(task_id)
    if folder.is_symlink(): raise HTTPException(404, 'Project unavailable')
    try: manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): raise HTTPException(404, 'Create the task project first') from None
    if not isinstance(manifest, list): raise HTTPException(404, 'Project manifest unavailable')
    if attachment_id is None: return manifest, None
    if not 0 <= attachment_id < len(manifest): raise HTTPException(404, 'Attachment not found')
    item = manifest[attachment_id]
    if not isinstance(item, dict): raise HTTPException(404, 'Attachment not found')
    filename = item.get('file')
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise HTTPException(404, 'Attachment is not stored')
    attachments_dir = folder / 'attachments'
    file = attachments_dir / filename
    if attachments_dir.is_symlink() or file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(folder.resolve()):
        raise HTTPException(404, 'Attachment is not stored')
    return item, file


@router.get('/{task_id:int}/project/attachments')
def project_attachments(request: Request, task_id: int, user=Depends(current_user)):
    manifest, _ = _project_attachment(request, task_id, user['id'])
    items = []
    for index, item in enumerate(manifest):
        if not isinstance(item, dict): continue
        mime = item.get('mime_type', '')
        size = item.get('size_bytes', 0)
        kind = 'missing'
        if item.get('file'):
            kind = 'image' if mime in ('image/jpeg', 'image/png', 'image/gif', 'image/webp') and size <= 2 * 1024 * 1024 else (
                'text' if mime in ('text/plain', 'text/csv') or str(item.get('name', '')).lower().endswith(('.txt', '.csv', '.md')) else 'download')
        items.append({'id': index, 'name': item.get('name', ''), 'mime_type': mime, 'size_bytes': size,
                      'kind': kind, 'message_id': item.get('message_id', ''),
                      'skipped_reason': item.get('skipped_reason', '')})
    return json_result({'attachments': items})


@router.get('/{task_id:int}/project/attachments/{attachment_id:int}/{action}')
def project_attachment_file(request: Request, task_id: int, attachment_id: int, action: str, user=Depends(current_user)):
    if action not in ('preview', 'download'): raise HTTPException(404, 'Not found')
    item, file = _project_attachment(request, task_id, user['id'], attachment_id)
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'}
    if action == 'download':
        return FileResponse(file, media_type='application/octet-stream', filename=str(item.get('name') or file.name), headers=headers)
    if file.stat().st_size > 2 * 1024 * 1024: raise HTTPException(415, 'Preview is limited to 2 MiB')
    content = file.read_bytes()
    mime = raster_preview_mime(item.get('mime_type', ''), content)
    if mime:
        return Response(content, media_type=mime, headers={**headers, 'Content-Disposition': 'inline'})
    if item.get('mime_type') in ('text/plain', 'text/csv') or str(item.get('name', '')).lower().endswith(('.txt', '.csv', '.md')):
        return json_result({'content': content[:65536].decode('utf-8', 'replace'), 'truncated': len(content) > 65536})
    raise HTTPException(415, 'Preview unavailable for this file type')


@router.get('/{task_id:int}/plan')
def saved_plan(request: Request, task_id: int, user=Depends(current_user)):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with model_session(path) as db:
        exists = db.exec(select(Task.id).where(Task.id == task_id, Task.user_id == user['id'])).first()
    if exists is None: raise HTTPException(404, 'Task not found')
    with connection(path) as db: saved = task_work_plan(db, task_id, user['id'])
    return json_result({'saved': saved})


@router.get('/{task_id:int}/images')
def images(request: Request, task_id: int, user=Depends(current_user)):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with model_session(path) as db:
        exists = db.exec(select(Task.id).where(Task.id == task_id, Task.user_id == user['id'])).first()
        if exists is None: raise HTTPException(404, 'Task not found')
        files = db.exec(select(TaskAttachment).where(TaskAttachment.task_id == task_id)
                        .order_by(TaskAttachment.id)).all()
        review = db.get(TaskImageReview, task_id)
        saved = dict(json.loads(review.result_json), created_at=review.created_at) if review and review.user_id == user['id'] else None
        metadata = [{'id': file.id, 'name': file.filename, 'mime_type': file.mime_type,
                     'size_bytes': file.size_bytes, 'stored': file.content is not None,
                     'skipped_reason': file.skipped_reason, 'rotation': file.rotation} for file in files]
    with connection(path) as db: texts = task_image_texts(db,task_id,user['id'])
    return json_result({'images': metadata, 'review': saved, 'texts': texts})


@router.post('/{task_id:int}/images/extract-text')
def extract_task_image_text(request: Request, task_id: int, body: dict, user=Depends(current_user)):
    _require_write(request,user)
    path = _task_exists(request,task_id,user['id'])
    ids = body.get('image_ids')
    if not isinstance(ids,list) or not 1 <= len(ids) <= ai.MAX_IMAGE_ATTACHMENTS or any(type(value) is not int or value <= 0 for value in ids) or len(set(ids)) != len(ids):
        raise HTTPException(400, f'Choose 1–{ai.MAX_IMAGE_ATTACHMENTS} distinct images')
    with connection(path) as db:
        user_settings = prefs.effective(db,user['id'],getattr(request.app.state,'gmail_config',{}))
        if not user_settings['ai_enabled']: raise HTTPException(403,'The AI assistant is turned off in Settings')
        placeholders = ','.join('?' for _ in ids)
        rows = db.execute(f'SELECT a.id,a.filename,a.mime_type,a.content FROM task_attachments a JOIN tasks t ON t.id=a.task_id '
                          f'WHERE t.id=? AND t.user_id=? AND a.id IN ({placeholders})',
                          (task_id,user['id'],*ids)).fetchall()
        by_id = {row['id']:row for row in rows}
        if len(by_id) != len(ids): raise HTTPException(404,'Task image not found')
        images = []
        for image_id in ids:
            row = by_id[image_id]
            content = bytes(row['content']) if row['content'] is not None else b''
            if not raster_preview_mime(row['mime_type'],content):
                raise HTTPException(400,'Every image must be stored, supported and at most 2 MiB')
            images.append((row['filename'],row['mime_type'],content))
    try:
        with ai.guard(user['id']): extracted = ai.extract_image_text(getattr(request.app.state,'gmail_config',{}),images,user_settings)
    except ai.AIBusy as exc: raise HTTPException(429,str(exc)) from None
    except ai.AIError as exc: raise HTTPException(424,str(exc)) from None
    provider = ai.PROVIDERS[ai.provider_of(user_settings)]['name']
    model = user_settings['ai_model'] or 'provider default'
    with connection(path) as db:
        saved = save_task_image_texts(db,task_id,user['id'],
                                     [{'image_id':image_id,'text':item['text']} for image_id,item in zip(ids,extracted)],provider,model)
    if saved is None: raise HTTPException(404,'Task not found')
    return json_result({'texts': saved})


@router.get('/{task_id:int}/images/{image_id:int}/{action}')
def image_file(request: Request, task_id: int, image_id: int, action: str, user=Depends(current_user)):
    if action not in ('thumbnail', 'download'): raise HTTPException(404, 'Not found')
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with model_session(path) as db:
        row = db.exec(select(TaskAttachment).join(Task, Task.id == TaskAttachment.task_id)
            .where(Task.id == task_id, Task.user_id == user['id'], TaskAttachment.id == image_id)).first()
        if row is None: raise HTTPException(404, 'Task image not found')
        if row.content is None: raise HTTPException(404, 'Task image was not stored')
        content, filename, mime_type = row.content, row.filename, row.mime_type
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
               'Referrer-Policy': 'no-referrer',
               'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}
    if action == 'thumbnail':
        mime = raster_preview_mime(mime_type, content)
        if not mime: raise HTTPException(415, 'Image preview unavailable')
        headers['Content-Disposition'] = 'inline'
        return Response(content, media_type=mime, headers=headers)
    safe = ''.join(c if c.isascii() and (c.isalnum() or c in '._-') else '_' for c in filename)[:100] or 'image'
    headers['Content-Disposition'] = f'attachment; filename="{safe}"; filename*=UTF-8\'\'{quote(filename)}'
    return Response(content, media_type='application/octet-stream', headers=headers)
